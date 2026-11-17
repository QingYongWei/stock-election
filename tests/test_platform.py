"""Platform tests never read production DATA or invoke real network services."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_agent import core
from stock_agent import platform as platform


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_patch = patch.object(core, 'DATA', Path(self.temp.name))
        self.data_patch.start()
        self.network = patch.object(core, 'request', side_effect=AssertionError('unexpected network'))
        self.request = self.network.start()
        core.init()
        with core.db() as c:
            platform.initialize(c)

    def tearDown(self):
        self.network.stop()
        self.data_patch.stop()
        self.temp.cleanup()

    def provider(self, **extra):
        return platform.save_provider(dict(name='local',preset='custom',api_base='http://127.0.0.1:8888/v1',model='own-model',api_key='secret-test-key',**extra))

    def test_fresh_samples_and_idempotence(self):
        s = platform.state()
        self.assertEqual(s['brand'],'创作罗盘（暂名）')
        self.assertEqual(s['workspaces'],[])
        self.assertIsNone(s['active_workspace_id'])
        self.assertEqual([d['id'] for d in s['directions']],['finance','work','tech','life','culture','learning'])
        for d in s['directions']:
            self.assertTrue(d['is_sample'])
            self.assertEqual(len(d['topics']),3 if d['id']=='finance' else 2)
            for t in d['topics']:
                self.assertTrue(t['preview']['is_sample'])
                self.assertTrue(t['preview']['body'])
        a=platform.create_workspace({'direction_id':'work'})
        platform.update_workspace(a['id'],{'profile':{'brand':'我的空间'}})
        self.assertEqual(platform.create_workspace({'direction_id':'work'})['name'],'我的空间')
        with core.db() as c: platform.initialize(c,legacy=True)
        self.assertEqual(len(platform.list_workspaces()),1)
        self.request.assert_not_called()

    def test_legacy_migration_preserves_and_syncs(self):
        # Independent in-memory connection verifies initialize uses its argument only.
        c=sqlite3.connect(':memory:')
        c.execute('CREATE TABLE config (id INTEGER PRIMARY KEY,value TEXT)')
        c.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY,value TEXT)')
        old={**core.DEFAULT,'brand':'旧品牌','goal':'旧目标','dsa_timeout':900,'api_key':'legacy-secret','model':'own'}
        job={'id':'abc','status':'approved','content':{'body':'keep'}}
        c.execute('INSERT INTO config VALUES (1,?)',(json.dumps(old),))
        c.execute('INSERT INTO jobs VALUES (?,?)',('abc',json.dumps(job)))
        platform.initialize(c,legacy=True)
        w=json.loads(c.execute('SELECT value FROM workspaces').fetchone()[0])
        self.assertEqual(w['profile']['brand'],'旧品牌')
        self.assertEqual(w['profile']['dsa_timeout'],900)
        self.assertNotIn('api_key',w['profile'])
        j=json.loads(c.execute('SELECT value FROM jobs').fetchone()[0])
        self.assertEqual(j['content'],job['content'])
        self.assertEqual(j['workspace_id'],'workspace-finance')
        w['profile']['brand']='用户更新'
        c.execute('UPDATE workspaces SET value=?',(json.dumps(w),))
        platform.initialize(c,legacy=True)
        self.assertEqual(json.loads(c.execute('SELECT value FROM workspaces').fetchone()[0])['profile']['brand'],'用户更新')
        self.assertEqual(json.loads(c.execute('SELECT value FROM config').fetchone()[0]),old)
        c.close()
        platform.create_workspace({'direction_id':'finance'})
        platform.update_workspace('workspace-finance',{'profile':{'brand':'新财经','stock_list':'600519','schedule_enabled':True,'schedule_time':'17:30'}})
        self.assertEqual(core.config()['brand'],'新财经')
        self.assertEqual(core.config()['schedule_time'],'17:30')

    def test_workspace_feedback_isolation_and_validation(self):
        for d in ('finance','work','life'): platform.create_workspace({'direction_id':d})
        platform.activate_workspace('workspace-life')
        self.assertEqual(platform.state()['active_workspace_id'],'workspace-life')
        platform.update_workspace('workspace-work',{'profile':{'tone':'尊重','materials':'脱敏案例'}})
        self.assertNotEqual(platform.get_workspace('workspace-life')['profile']['tone'],'尊重')
        platform.feedback('workspace-work',{'difficulty':'hard','question':'问题'})
        self.assertEqual(platform.get_workspace('workspace-life')['feedback_records'],[])
        for _ in range(105): platform.feedback('workspace-work',{})
        self.assertEqual(len(platform.get_workspace('workspace-work')['feedback_records']),100)
        for p in ({'profile':{'api_key':'bad'}},{'profile':{'model':'bad'}},{'profile':{'weekly_day':True}},{'profile':{'schedule_time':'25:00'}},{'profile':{'schedule_enabled':1}},{'profile':{'stock_list':'600519'}}):
            with self.assertRaises(ValueError): platform.update_workspace('workspace-work',p)
        with self.assertRaises(ValueError): platform.update_workspace('workspace-finance',{'profile':{'stock_list':'BAD'}})
        with self.assertRaises(ValueError): platform.feedback('workspace-work',{'revenue':100})
        with self.assertRaises(ValueError): platform.get_workspace('../escape')
        with self.assertRaises(KeyError): platform.get_workspace('workspace-missing')

    def test_secret_assignments_clear_and_effective(self):
        platform.create_workspace({'direction_id':'tech'})
        self.assertEqual(platform.effective_config('workspace-tech')['provider_mode'],'facts')
        p=self.provider()
        self.assertTrue(p['has_api_key'])
        platform.save_assignments({'topic_provider_id':p['id'],'writing_provider_id':p['id']})
        effective=platform.effective_config('workspace-tech')
        self.assertEqual(effective['_writing_config']['api_key'],'secret-test-key')
        self.assertEqual(effective['_domain'],'tech')
        self.assertNotIn('stock_list',effective)
        self.assertNotIn('secret-test-key',json.dumps(platform.state()))
        self.assertNotIn('api_key',p)
        self.assertNotIn('Authorization',json.dumps(platform.state()))
        self.assertTrue(platform.save_provider({'api_key':''},p['id'])['has_api_key'])
        self.assertFalse(platform.save_provider({'clear_api_key':True},p['id'])['has_api_key'])
        with self.assertRaises(KeyError): platform.save_assignments({'writing_provider_id':'provider-unknown'})
        result=platform.delete_provider(p['id'])
        self.assertIsNone(result['assignments']['topic_provider_id'])
        self.assertIsNone(result['assignments']['writing_provider_id'])
        self.request.assert_not_called()

    def test_provider_url_and_json_validation(self):
        for url in ('https://user:key@example.com','https://example.com?key=secret','https://example.com#secret','file:///tmp/a','http://localhost:bad','https://example.com\\evil'):
            with self.assertRaises(ValueError):
                platform.save_provider({'name':'x','api_base':url,'model':'my-model'})
        for payload in ([],{'clear_api_key':'yes'},{'api_key':42}):
            with self.assertRaises(ValueError): platform.save_provider(payload)
        p=self.provider(promotion_url='https://example.com/promotion')
        self.assertTrue(p['is_promotion'])

    def test_explicit_connection_test_sanitizes_errors(self):
        p=self.provider()
        self.request.assert_not_called()
        self.request.side_effect=RuntimeError('Authorization: secret-test-key upstream body')
        r=platform.test_provider(p['id'])
        self.assertFalse(r['connected'])
        self.assertNotIn('secret-test-key',json.dumps(r))
        self.request.assert_called_once()
        self.request.reset_mock()
        self.request.side_effect=None
        self.request.return_value=json.dumps({'choices':[{'message':{'content':'OK'}}]})
        self.assertTrue(platform.test_provider(p['id'])['connected'])
        self.request.assert_called_once()
        self.assertLessEqual(self.request.call_args.args[1]['max_tokens'],16)


    def test_legacy_key_never_resurrects_after_unassignment_delete_clear(self):
        platform.create_workspace({'direction_id':'finance'})
        legacy = {**core.DEFAULT, 'api_key':'old-key', 'model':'old-model', 'provider_mode':'openai'}
        with core.db() as c:
            c.execute('UPDATE config SET value=? WHERE id=1',(json.dumps(legacy),))
        self.assertEqual(platform.effective_config('workspace-finance')['api_key'],'old-key')
        platform.save_assignments({'topic_provider_id':None,'writing_provider_id':None})
        cfg=platform.effective_config('workspace-finance')
        self.assertEqual(cfg['api_key'],'')
        self.assertEqual(cfg['model'],'')
        self.assertEqual(cfg['provider_mode'],'facts')
        for action in ('clear','delete'):
            with self.subTest(action=action), core.db() as c:
                c.execute('UPDATE config SET value=? WHERE id=1',(json.dumps(legacy),))
                c.execute('INSERT OR REPLACE INTO providers VALUES (?,?)',('provider-legacy',json.dumps(dict(id='provider-legacy',name='legacy',preset='custom',api_base='https://api.deepseek.com',model='old-model',api_key='old-key',promotion_url=''))))
            platform.save_assignments({'topic_provider_id':'provider-legacy','writing_provider_id':'provider-legacy'})
            if action == 'clear': platform.save_provider({'clear_api_key':True},'provider-legacy')
            else: platform.delete_provider('provider-legacy')
            self.assertEqual(core.config(True)['api_key'],'')
            self.assertEqual(core.config(True)['provider_mode'],'facts')
            effective=platform.effective_config('workspace-finance')
            self.assertEqual(effective['api_key'],'')
            self.assertNotIn('old-key',json.dumps(effective))
        self.request.assert_not_called()

    def test_legacy_facts_mode_remains_facts_with_saved_key(self):
        platform.create_workspace({'direction_id':'finance'})
        legacy={**core.DEFAULT,'api_key':'stored-unused-key','model':'own','provider_mode':'facts'}
        with core.db() as c:
            c.execute('UPDATE config SET value=? WHERE id=1',(json.dumps(legacy),))
        self.assertEqual(platform.effective_config('workspace-finance')['provider_mode'],'facts')
        with core.db() as c:
            c.execute("DELETE FROM platform_settings WHERE key='initialized'")
            platform.initialize(c,legacy=True)
        self.assertIsNone(platform.state()['assignments']['writing_provider_id'])
        self.assertEqual(platform.effective_config('workspace-finance')['provider_mode'],'facts')
        self.request.assert_not_called()

    def test_connection_endpoint_matches_editorial(self):
        self.request.side_effect=None
        self.request.return_value=json.dumps({'choices':[{'message':{'content':'OK'}}]})
        for base,endpoint in (
            ('http://localhost:8000','http://localhost:8000/v1/chat/completions'),
            ('http://localhost:8000/v1','http://localhost:8000/v1/chat/completions'),
            ('http://localhost:8000/v1/chat/completions','http://localhost:8000/v1/chat/completions'),
            ('https://api.deepseek.com','https://api.deepseek.com/chat/completions'),
        ):
            with self.subTest(base=base):
                p=platform.save_provider({'api_base':base,'model':'own'})
                self.request.reset_mock()
                self.assertTrue(platform.test_provider(p['id'])['connected'])
                self.request.assert_called_once()
                self.assertEqual(self.request.call_args.args[0],endpoint)

    def test_finance_dsa_validation_is_atomic(self):
        platform.create_workspace({'direction_id':'finance'})
        before=platform.get_workspace('workspace-finance')
        base=Path(self.temp.name)
        repo=base/'dsa'; repo.mkdir()
        (repo/'main.py').write_text('# fixture',encoding='utf-8')
        interpreter=base/'python.exe'; interpreter.write_text('fixture',encoding='utf-8')
        good=dict(dsa_run_enabled=True,dsa_repo=str(repo),dsa_python=str(interpreter),dsa_report_dir=str(repo/'reports'))
        for bad in ({**good,'dsa_repo':str(base/'missing')},{**good,'dsa_python':str(base/'missing.exe')},{**good,'dsa_report_dir':str(base/'old-reports')}):
            with self.assertRaises(ValueError): platform.update_workspace('workspace-finance',{'profile':bad})
            self.assertEqual(platform.get_workspace('workspace-finance'),before)
            self.assertFalse(core.config()['dsa_run_enabled'])
        platform.update_workspace('workspace-finance',{'profile':good})
        self.assertTrue(core.config()['dsa_run_enabled'])
        with self.assertRaises(ValueError):
            platform.update_workspace('workspace-finance',{'profile':{'dsa_report_dir':str(base/'other')}})
        self.assertEqual(platform.get_workspace('workspace-finance')['profile']['dsa_report_dir'],str(repo/'reports'))
        self.request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
