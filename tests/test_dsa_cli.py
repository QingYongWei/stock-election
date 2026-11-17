"""Bridge process contracts with explicit temporary fixture scripts, not real DSA analysis."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from tools import run_dsa

class DsaCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.repo=self.root/'fixture-repo';self.repo.mkdir()
        (self.repo/'requirements.txt').write_text('# explicit test fixture',encoding='utf-8')
        self.manifest=self.root/'run.json'
    def tearDown(self):self.tmp.cleanup()
    def run_fixture(self,script,timeout='5'):
        (self.repo/'main.py').write_text(script,encoding='utf-8')
        args=['--repo',str(self.repo),'--python',sys.executable,'--stocks','600519','--timeout',timeout,'--manifest-out',str(self.manifest)]
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):return run_dsa.main(args)

    def test_child_uses_single_run_and_no_external_document_env(self):
        code=self.run_fixture("""import os, sys
from pathlib import Path
assert '--no-notify' in sys.argv
assert os.environ['SCHEDULE_ENABLED']=='false'
assert os.environ['RUN_IMMEDIATELY']=='true'
assert os.environ['FEISHU_APP_ID']==''
assert os.environ['FEISHU_FOLDER_TOKEN']==''
assert os.environ['BOT_ENABLED']=='false'
p=Path('reports');p.mkdir();(p/'fixture.md').write_text('TEST FIXTURE ONLY',encoding='utf-8')
""")
        self.assertEqual(code,0)
        result=json.loads(self.manifest.read_text(encoding='utf-8'))
        self.assertEqual(result['repo'],str(self.repo.resolve()))
        self.assertEqual(len(result['reports']),1)
        self.assertTrue(Path(result['reports'][0]['path']).is_absolute())
        self.assertEqual(result['reports'][0]['size'],17)

    def test_old_report_not_misreported_as_new(self):
        r=self.repo/'reports';r.mkdir();(r/'old.md').write_text('TEST FIXTURE ONLY')
        self.manifest.write_text('{}')
        self.assertEqual(self.run_fixture('pass'),3)
        self.assertFalse(self.manifest.exists())

    def test_nonzero_child_has_no_success_manifest(self):
        self.manifest.write_text('{}')
        self.assertEqual(self.run_fixture('raise SystemExit(7)'),7)
        self.assertFalse(self.manifest.exists())

    def test_timeout_kills_direct_fixture_child(self):
        self.manifest.write_text('{}')
        self.assertEqual(self.run_fixture('import time;time.sleep(10)',timeout='0.05'),124)
        self.assertFalse(self.manifest.exists())

    def test_empty_report_is_not_success(self):
        self.assertEqual(self.run_fixture("from pathlib import Path\np=Path('reports');p.mkdir();(p/'empty.md').write_text('')"),3)
        self.assertFalse(self.manifest.exists())

if __name__=='__main__':unittest.main()
