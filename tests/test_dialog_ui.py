"""Regression checks for designed, same-origin business confirmations."""
from html.parser import HTMLParser
from pathlib import Path
import re
import os
import shutil
import subprocess
import threading
import unittest
import urllib.request
from stock_agent import server

STATIC=Path(__file__).resolve().parents[1]/'stock_agent'/'static'

class Markup(HTMLParser):
    def __init__(self,text):
        super().__init__();self.scripts=[];self.ids=set();self.dialogs=[];self.feed(text)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if attrs.get('id'):self.ids.add(attrs['id'])
        if tag=='script':self.scripts.append(attrs)
        if tag=='dialog':self.dialogs.append(attrs)

class DialogUiTests(unittest.TestCase):
    def test_controller_focus_cancel_concurrency_and_safe_text(self):
        bundled=Path(os.environ.get('USERPROFILE',''))/'.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node.exe'
        node=str(bundled) if bundled.is_file() else shutil.which('node')
        if not node:self.skipTest('Node.js is needed for the controller state-machine regression')
        result=subprocess.run([node,str(Path(__file__).with_name('dialog_controller.test.js'))],timeout=20)
        self.assertEqual(result.returncode,0)

    def test_business_calls_do_not_use_native_browser_dialogs(self):
        source=(STATIC/'app.js').read_text(encoding='utf-8')
        self.assertIsNone(re.search(r'(?<![\w.$])(?:(?:window|globalThis)\s*\.\s*)?(?:confirm|alert|prompt)\s*\(',source))
        self.assertIn('beforeunload',source,'Keep the browser-only unsaved-page close protection')

    def test_controller_loads_before_app_without_weakening_csp(self):
        doc=Markup((STATIC/'index.html').read_text(encoding='utf-8'))
        scripts=[x.get('src') for x in doc.scripts]
        self.assertLess(scripts.index('/static/action-dialog.js'),scripts.index('/static/app.js'))
        for script in doc.scripts:
            if script.get('src') in ('/static/action-dialog.js','/static/app.js'):self.assertIn('defer',script)
        self.assertTrue((STATIC/'action-dialog.js').is_file())

    def test_business_dialog_has_accessible_name_description_and_is_closed(self):
        doc=Markup((STATIC/'index.html').read_text(encoding='utf-8'))
        dialogs=[x for x in doc.dialogs if x.get('id')!='sample-dialog']
        self.assertEqual(len(dialogs),1)
        dialog=dialogs[0]
        self.assertNotIn('open',dialog)
        for attr in ('aria-labelledby','aria-describedby'):
            self.assertTrue(dialog.get(attr))
            for target in dialog[attr].split():self.assertIn(target,doc.ids)

    def test_controller_is_served_as_same_origin_javascript(self):
        httpd=server.LocalServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open('http://127.0.0.1:'+str(httpd.server_port)+'/static/action-dialog.js',timeout=5) as response:
                self.assertEqual(response.status,200)
                self.assertIn('javascript',response.headers['Content-Type'])
                self.assertIn("script-src 'self'",response.headers['Content-Security-Policy'])
                self.assertIn(b'AppDialog',response.read())
        finally:
            httpd.shutdown();httpd.server_close();thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
