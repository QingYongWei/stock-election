import datetime as dt
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from stock_agent import core
from stock_agent.export import wechat_html

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.data=patch.object(core,'DATA',Path(self.tmp.name));self.data.start();core.init()
    def tearDown(self):
        self.data.stop();self.tmp.cleanup()

    def test_safe_html_does_not_execute_report_html(self):
        result=wechat_html('# 标题\n<script>alert(1)</script>\n| A | B |\n| --- | --- |\n| 1 | 2 |')
        self.assertNotIn('<script>',result)
        self.assertIn('&lt;script&gt;',result)
        self.assertIn('<table',result)
        self.assertIn('<h1',result)

    def test_restart_marks_interrupted_job_failed(self):
        core.put(dict(id='a'*16,status='running'))
        core.init()
        self.assertEqual(core.get('a'*16)['status'],'failed')
        self.assertIn('中断',core.get('a'*16)['error'])

    def test_scheduler_disabled_and_before_time_do_nothing(self):
        at=dt.datetime(2026,11,16,17,59,tzinfo=core.TZ)
        with patch.object(core,'start_job') as start:
            core.scheduler_tick(at)
            start.assert_not_called()
            core.save_config(dict(schedule_enabled=True))
            core.scheduler_tick(at)
            start.assert_not_called()

    def test_scheduler_dedup_daily_and_weekly_with_serial_retry(self):
        core.save_config(dict(schedule_enabled=True,weekly_day=0))
        at=dt.datetime(2026,11,16,18,1,tzinfo=core.TZ)
        def fake(kind,scheduled_key=None):
            with core.db() as c:c.execute('INSERT INTO schedules VALUES (?,?,?)',(scheduled_key,'test',core.stamp()))
        with patch.object(core,'start_job',side_effect=fake) as start:
            core.scheduler_tick(at);core.scheduler_tick(at)
            self.assertEqual(start.call_count,2)
            self.assertEqual([c.args[0] for c in start.call_args_list],['daily','weekly'])

    def test_weekend_can_generate_configured_weekly_not_daily(self):
        core.save_config(dict(schedule_enabled=True,weekly_day=6))
        with patch.object(core,'start_job') as start:
            core.scheduler_tick(dt.datetime(2026,11,22,19,0,tzinfo=core.TZ))
            start.assert_called_once_with('weekly',scheduled_key='2026-11-22:weekly')

    def test_scheduler_busy_does_not_claim_or_duplicate(self):
        core.save_config(dict(schedule_enabled=True))
        core.RUN_LOCK.acquire()
        try:
            with patch.object(core,'start_job') as start:
                core.scheduler_tick(dt.datetime(2026,11,16,19,0,tzinfo=core.TZ))
                start.assert_not_called()
            with core.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM schedules').fetchone()[0],0)
        finally:core.RUN_LOCK.release()

    def test_dsa_bridge_disabled_by_default(self):
        with patch.object(core.subprocess,'run') as run:
            self.assertEqual(core.run_dsa_bridge(core.config(True)),[])
            run.assert_not_called()

    def test_dsa_bridge_arguments_and_failure_are_not_silent(self):
        cfg=core.config(True);cfg.update(dsa_run_enabled=True,dsa_repo='fake-repo',dsa_python='fake-python',dsa_timeout=100)
        with patch.object(core.subprocess,'run') as run:
            run.return_value.returncode=3
            with self.assertRaisesRegex(ValueError,'未使用旧报告'):core.run_dsa_bridge(cfg)
            args,kw=run.call_args
            self.assertIn('--timeout',args[0]);self.assertIn('100',args[0])
            self.assertEqual(kw['stdout'],core.subprocess.DEVNULL)
            self.assertEqual(kw['stderr'],core.subprocess.DEVNULL)

    def test_dsa_model_credentials_are_env_only_not_commandline(self):
        cfg=core.config(True);cfg.update(dsa_run_enabled=True,dsa_repo='fixture-repo',dsa_python='fixture-python',dsa_timeout=100,provider_mode='openai',api_key='OFFLINE-FIXTURE-ONLY',model='fixture-model')
        with patch.object(core.subprocess,'run') as run:
            run.return_value.returncode=3
            with self.assertRaises(ValueError):core.run_dsa_bridge(cfg)
            args,kw=run.call_args
            self.assertNotIn('OFFLINE-FIXTURE-ONLY',' '.join(args[0]))
            self.assertEqual(kw['env']['OPENAI_API_KEY'],'OFFLINE-FIXTURE-ONLY')
            self.assertEqual(kw['env']['LITELLM_MODEL'],'openai/fixture-model')

if __name__=='__main__':unittest.main()
