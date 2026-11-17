import datetime as dt
import io
import json
import threading
import zipfile
from pathlib import Path
from unittest.mock import patch
from tests.test_agent import IsolatedAgentTest, FIXED_NOW
from stock_agent import core

class ReviewRegressions(IsolatedAgentTest):
    def test_preview_route_returns_safe_html(self):
        j=self.generate_ai()
        core.update_content(j['id'],{'wechat_markdown':j['wechat_markdown']+'\n<script>alert(1)</script>'})
        status,body,extra=self.api('GET','/api/jobs/'+j['id']+'/preview')
        self.assertEqual(status,200)
        self.assertIn('text/html',extra[0])
        self.assertNotIn('<script>',body)
        self.assertIn('&lt;script&gt;',body)

    def test_card_confirmation_is_required(self):
        j=self.generate_ai()
        with self.assertRaisesRegex(ValueError,'核对全部图片'):core.review(j['id'],{'action':'approve'})
        core.review(j['id'],{'action':'approve','confirm_cards':True})
        p=core.DATA/'artifacts'/j['id']/'card-1.png';p.write_bytes(b'changed image')
        with self.assertRaisesRegex(ValueError,'批准版本不一致'):core.export_zip(j['id'])
        with self.assertRaisesRegex(ValueError,'批准版本不一致'):core.review(j['id'],{'action':'publish','publish_url':'https://example.test/real-post'})

    def test_afternoon_cached_morning_snapshot_not_postclose(self):
        self.mock_market();quotes,source=core.quote_data('')
        for q in quotes:q['timestamp']='2026-11-16T09:30:00+08:00'
        with patch.object(core,'quote_data',return_value=(quotes,source)):
            facts,checks=core.collect(core.config(True),'daily')
        self.assertTrue(any('早于15:00' in x['message'] for x in checks))
        content=core.compose_facts(core.config(True),facts,'daily')
        self.assertIn('盘中',content['wechat_title'])
        self.assertIn('09:30:00',content['wechat_markdown'])
        content['wechat_title']='2026-11-16 盘后复盘'
        self.assertTrue(any(x['level']=='error' for x in core.validate_content(content,facts)))

    def test_weekly_uses_latest_market_capture_not_edited_rowid(self):
        first=self.generate();second=self.generate()
        first['facts']['collected_at']='2026-11-16T15:10:00+08:00'
        second['facts']['collected_at']='2026-11-16T18:30:00+08:00'
        second['facts']['quotes'][0]['price']=9999
        core.put(second);core.put(first)  # Put old capture last to move rowid.
        self.mock_market()
        facts,_=core.collect(core.config(True),'weekly')
        self.assertEqual(facts['history'][0]['quotes'][0]['price'],9999)

    def test_dsa_manifest_rejects_unrelated_or_changed_report(self):
        root=self.data/'reports';root.mkdir()
        report=root/'report_20261116.md';report.write_text('explicit offline fixture',encoding='utf-8')
        s=report.stat()
        cfg=core.config(True);cfg.update(dsa_report_dir=str(root),_dsa_current_reports=[dict(path=str(report),mtime_ns=s.st_mtime_ns,size=s.st_size)])
        reports,_=core.dsa_reports(cfg)
        self.assertEqual(len(reports),1)
        report.write_text('changed',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'发生变动'):core.dsa_reports(cfg)
        cfg['_dsa_current_reports'][0]['path']=str(self.data/'outside.md')
        with self.assertRaisesRegex(ValueError,'非预期'):core.dsa_reports(cfg)

    def test_export_locks_snapshot_while_editing(self):
        # Start real threads here despite the inline thread fixture used for producers.
        j=self.generate_ai();jid=j['id'];started=threading.Event();release=threading.Event();edited=threading.Event()
        real_write=zipfile.ZipFile.write;result={}
        def paused_write(z,file,*a,**kw):
            if not started.is_set():started.set();release.wait(3)
            return real_write(z,file,*a,**kw)
        def export():result['zip']=core.export_zip(jid)
        def edit():
            core.update_content(jid,{'wechat_title':'2026-11-16 新标题'});edited.set()
        # Import saved stdlib class independently because core.threading.Thread was patched.
        import _thread
        with patch.object(zipfile.ZipFile,'write',paused_write):
            _thread.start_new_thread(export,())
            self.assertTrue(started.wait(2))
            _thread.start_new_thread(edit,())
            self.assertFalse(edited.wait(0.05))
            release.set()
            self.assertTrue(edited.wait(3))
        with zipfile.ZipFile(io.BytesIO(result['zip'])) as z:
            self.assertEqual(z.read('wechat.md').decode(),j['wechat_markdown'])
        self.assertEqual(core.get(jid)['status'],'review_required')
