"""Launchers must parse in Windows PowerShell 5.1 without UTF-8 BOM."""
import os
from pathlib import Path
import subprocess
import unittest
import json
import socket
import tempfile
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
POWERSHELL=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'

class StartupTests(unittest.TestCase):
    def test_launchers_are_encoding_independent_ascii(self):
        for name in ('start.ps1','启动工作台.cmd'):
            with self.subTest(name=name):
                content=(ROOT/name).read_bytes()
                self.assertTrue(content)
                self.assertTrue(all(b<128 for b in content),'Keep launcher contents ASCII for Windows PowerShell 5.1')

    @unittest.skipUnless(os.name=='nt' and POWERSHELL.is_file(),'Windows PowerShell 5.1 only')
    def test_windows_powershell_51_parses_start_script(self):
        path=str(ROOT/'start.ps1').replace("'","''")
        command="$t=$null;$e=$null;[System.Management.Automation.Language.Parser]::ParseFile('"+path+"',[ref]$t,[ref]$e)|Out-Null;if($e.Count){$e|ForEach-Object{Write-Error $_};exit 1}"
        result=subprocess.run([str(POWERSHELL),'-NoProfile','-NonInteractive','-Command',command],timeout=30)
        self.assertEqual(result.returncode,0)

    @unittest.skipUnless(os.name=='nt' and POWERSHELL.is_file(),'Windows PowerShell 5.1 only')
    def test_windows_powershell_51_really_starts_isolated_server(self):
        with tempfile.TemporaryDirectory(prefix='startup-ps51-',ignore_cleanup_errors=True) as tmp:
            with socket.socket() as reserve:
                reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
            env={**os.environ,'STOCK_AGENT_DATA':tmp,'PYTHONIOENCODING':'utf-8'}
            with (Path(tmp)/'startup.log').open('wb') as log:
                process=subprocess.Popen([str(POWERSHELL),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'start.ps1'),'-Port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
                try:
                    deadline=time.monotonic()+20
                    while True:
                        if process.poll() is not None:self.fail('Launcher exited before service became healthy')
                        try:
                            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:'+str(port)+'/api/health',timeout=1) as response:health=json.load(response)
                            break
                        except (OSError,urllib.error.URLError):
                            if time.monotonic()>deadline:raise TimeoutError('PowerShell 5.1 launch did not become healthy')
                            time.sleep(.1)
                    self.assertTrue(health['ok'])
                    self.assertTrue((Path(tmp)/'agent.db').is_file())
                finally:
                    # Kill only the exact child process tree created above, never a port owner or user service.
                    if process.poll() is None:
                        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
                    process.wait(timeout=10)
            with socket.socket() as probe:
                probe.settimeout(1)
                self.assertNotEqual(probe.connect_ex(('127.0.0.1',port)),0,'Owned startup test server was not stopped')
