"""v0.3 end-to-end contracts. Temporary DB, mock models, no real HTTP/Key."""
import copy
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from tests.test_agent import IsolatedAgentTest
from tests.test_generic import fake_requester
from stock_agent import core, platform


class PlatformIntegrationTests(IsolatedAgentTest):
    def service(self, name='writer', secret='OFFLINE-ONLY-WRITER'):
        return platform.save_provider(dict(name=name,preset='custom',api_base='https://model.invalid/v1',model=name,api_key=secret))

    def generate_generic(self, domain='work', dual=False):
        w=platform.create_workspace({'direction_id':domain})
        platform.update_workspace(w['id'],{'profile':{'audience':'特定合成读者','goal':'检查交接边界'}})
        writer=self.service()
        topic=self.service('topic','OFFLINE-ONLY-TOPIC') if dual else writer
        platform.save_assignments(dict(topic_provider_id=topic['id'],writing_provider_id=writer['id']))
        request=fake_requester(domain);self.network.side_effect=request
        initial=core.start_job(workspace_id=w['id'])
        return core.get(initial['id'])

    def test_five_domains_real_pipeline_shape_and_no_financial_network(self):
        for domain in ('work','tech','life','culture','learning'):
            with self.subTest(domain=domain):
                j=self.generate_generic(domain)
                self.assertEqual(j['status'],'review_required',j.get('error'))
                self.assertEqual(j['domain'],domain)
                self.assertEqual(j['workspace_id'],'workspace-'+domain)
                self.assertEqual(len(j['cards']),4)
                self.assertTrue(core.public_job(j)['publication']['ready'])
                self.assertNotIn('不构成投资建议',j['wechat_markdown'])
                self.assertNotIn('OFFLINE-ONLY',json.dumps(core.public_job(j)))
                self.assertNotIn('_topic_config',j['config_snapshot'])
        self.assertTrue(all(call.args[0].startswith('https://model.invalid/') for call in self.network.call_args_list))

    def test_profile_and_dual_provider_routing_reach_actual_model_payloads(self):
        j=self.generate_generic(dual=True)
        self.assertEqual(j['status'],'review_required',j.get('error'))
        calls=self.network.call_args_list
        self.assertEqual(calls[0].args[1]['model'],'topic')
        self.assertEqual(calls[1].args[1]['model'],'writer')
        self.assertEqual(calls[0].kwargs['headers']['Authorization'],'Bearer OFFLINE-ONLY-TOPIC')
        payload=json.loads(calls[1].args[1]['messages'][1]['content'])
        self.assertEqual(payload['profile']['audience'],'特定合成读者')
        self.assertNotIn('OFFLINE-ONLY',json.dumps(payload))

    def test_no_provider_cannot_generate_generic_or_claim_sample_ready(self):
        w=platform.create_workspace({'direction_id':'work'})
        with self.assertRaises(ValueError):core.start_job(workspace_id=w['id'])
        self.assertEqual(core.jobs(),[])
        self.network.assert_not_called()
        self.assertFalse(core.RUN_LOCK.locked())

    def test_partial_assignment_does_not_start_paid_generic_requests(self):
        w=platform.create_workspace({'direction_id':'work'});writer=self.service()
        platform.save_assignments(dict(topic_provider_id=None,writing_provider_id=writer['id']))
        with self.assertRaises(ValueError):core.start_job(workspace_id=w['id'])
        self.network.assert_not_called();self.assertEqual(core.jobs(),[])

    def test_removed_disclosure_blocks_approval_and_preserves_other_workspace(self):
        j=self.generate_generic()
        platform.create_workspace({'direction_id':'finance'})
        edited=core.update_content(j['id'],{'wechat_markdown':j['wechat_markdown'].split('\n\n---\n创作边界：')[0]})
        self.assertTrue(any(c['level']=='error' for c in edited['checks']))
        with self.assertRaises(ValueError):core.review(j['id'],{'action':'approve','confirm_cards':True})
        self.assertEqual(core.jobs('workspace-finance'),[])
        self.assertEqual(len(core.jobs('workspace-work')),1)

    def test_api_scope_and_persistence(self):
        j=self.generate_generic()
        platform.create_workspace({'direction_id':'finance'})
        self.assertEqual(self.api('GET','/api/jobs/'+j['id']+'?workspace_id=workspace-finance')[0],404)
        self.assertEqual(len(self.api('GET','/api/jobs?workspace_id=workspace-work')[1]),1)
        platform.activate_workspace('workspace-work')
        platform.feedback('workspace-work',dict(published='unknown',difficulty='hard',question='合成反馈'))
        core.init()
        state=self.api('GET','/api/platform')[1]
        self.assertEqual(state['active_workspace_id'],'workspace-work')
        self.assertEqual(platform.get_workspace('workspace-work')['feedback']['question'],'合成反馈')
        self.assertFalse(platform.get_workspace('workspace-finance')['feedback'])
        self.assertNotIn('OFFLINE-ONLY',json.dumps(state))

    def test_legacy_config_invalid_sync_is_atomic(self):
        platform.create_workspace({'direction_id':'finance'})
        before=core.config(True);workspace=platform.get_workspace('workspace-finance')
        with self.assertRaises(ValueError):core.save_config({'brand':'字'*81})
        self.assertEqual(core.config(True),before)
        self.assertEqual(platform.get_workspace('workspace-finance'),workspace)

    def test_legacy_migration_backup_and_idempotence_without_touching_live_data(self):
        with tempfile.TemporaryDirectory(prefix='platform-legacy-integration-') as tmp:
            target=Path(tmp)
            with closing(sqlite3.connect(target/'agent.db')) as c:
                c.execute('CREATE TABLE config(id INTEGER PRIMARY KEY,value TEXT)')
                cfg={**core.DEFAULT,'brand':'旧空间合成品牌','provider_mode':'openai','model':'legacy','api_key':'SYNTHETIC-LEGACY-ONLY'}
                c.execute('INSERT INTO config VALUES(1,?)',(json.dumps(cfg),))
                c.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,value TEXT)')
                c.execute('INSERT INTO jobs VALUES(?,?)',('b'*16,json.dumps(dict(id='b'*16,status='materials_ready',title='旧记录'))))
                c.commit()
            with patch.object(core,'DATA',target):
                core.init();core.init()
                self.assertTrue((target/'agent.pre-platform-v030.backup').is_file())
                self.assertEqual(len(platform.list_workspaces()),1)
                self.assertEqual(platform.get_workspace('workspace-finance')['name'],'旧空间合成品牌')
                self.assertEqual(core.get('b'*16)['workspace_id'],'workspace-finance')
                self.assertEqual(len(platform.list_providers()),1)
                self.assertNotIn('SYNTHETIC-LEGACY-ONLY',json.dumps(platform.state()))
                core.save_config({'clear_api_key':True})
                self.assertFalse(platform.list_providers()[0]['has_api_key'])
                self.assertEqual(platform.effective_config('workspace-finance')['provider_mode'],'facts')
                self.network.assert_not_called()
