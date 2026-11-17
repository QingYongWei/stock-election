"""Guarded offline UI fixture. NOT a production launcher or real model test."""
import argparse
import copy
import json
import os
import sys
import threading
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from stock_agent import core, server
from tests.test_editorial import EditorialTest
from tests.test_generic import fake_model_responses


def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=18767);args=p.parse_args()
    expected=(ROOT/'delivery'/'v0.3-ui-test-data').resolve()
    if core.DATA.resolve()!=expected:raise ValueError('Refusing non-fixture data directory')
    helper=EditorialTest();helper.setUp()
    def collect(cfg,kind):
        facts=copy.deepcopy(helper.facts);facts['sources']=copy.deepcopy(facts['article_sources']);return facts,[]
    def request(url,payload=None,**kwargs):
        if not url.startswith('https://model.invalid/'):raise ValueError('Offline fixture blocks all real HTTP')
        if payload.get('max_tokens')==8:obj='OK'
        else:
            supplied=json.loads(payload['messages'][1]['content']);domain=supplied.get('domain','finance')
            first,second=(helper.first,helper.second) if domain=='finance' else fake_model_responses(domain)
            obj=json.dumps(second if 'selected_topic' in supplied else first,ensure_ascii=False)
        return json.dumps({'choices':[{'message':{'content':obj}}],'usage':{'prompt_tokens':1,'completion_tokens':1}},ensure_ascii=False)
    core.collect=collect;core.request=request
    import stock_agent.research
    stock_agent.research.enrich=lambda facts,request:(facts,[])
    class FixtureHandler(server.Handler):
        def send(self,code,data,ctype='application/json; charset=utf-8',extra=None):
            if ctype.startswith('text/html'):
                text=data.decode() if isinstance(data,bytes) else data
                text=text.replace('<body>','<body><div style="background:#9b3a1b;color:white;padding:10px;text-align:center">离线联调测试实例：行情和模型均为固定回执，不代表真实模型或数据连通。</div>',1)
                data=text
            return super().send(code,data,ctype,extra)
        def do_POST(self):
            if self.path=='/__fixture_shutdown':
                self.verify(True);self.body();self.send(200,{'stopped':True});threading.Thread(target=self.server.shutdown,daemon=True).start();return
            super().do_POST()
    httpd=server.LocalServer(('127.0.0.1',args.port),FixtureHandler)
    try:
        core.init()
        print('OFFLINE FIXTURE ONLY http://127.0.0.1:'+str(args.port),flush=True)
        httpd.serve_forever()
    finally:
        core.STOP.set();httpd.server_close();helper.doCleanups()
        print('Offline fixture server closed',flush=True)

if __name__=='__main__':main()
