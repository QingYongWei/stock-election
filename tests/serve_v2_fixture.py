"""Temporary UI acceptance server ONLY. Fictional model replies are clearly labelled.
Run with STOCK_AGENT_DATA pointing to a dedicated *v0.2-ui-test-data* directory.
Never use customer storage. No model or platform publish API is called.
"""
import copy
import os
from pathlib import Path
import threading
from unittest.mock import patch
from stock_agent import core, server
from tests.test_editorial import EditorialTest


def seed():
    target=Path(os.environ.get('STOCK_AGENT_DATA','')).resolve()
    if target.name!='v0.2-ui-test-data':raise ValueError('Dedicated v0.2-ui-test-data directory required')
    core.init()
    fixture=EditorialTest();fixture.setUp()
    try:
        facts=copy.deepcopy(fixture.facts)
        facts['quotes'].insert(0,dict(code='sh000001',name='样例指数',price=3000,pct=-1,date='2026-11-16',timestamp='2026-11-16T15:00:00+08:00',is_index=True))
        facts.update(sources=copy.deepcopy(facts['article_sources']),announcements=[],dsa_reports=[])
        first=copy.deepcopy(fixture.first);second=copy.deepcopy(fixture.second)
        first['topics'][0].update(title='【功能测试样例】价格变化≠经营改善',why_today='这是界面和发布操作验收，不是真实模型选题。')
        first['topics'] += [dict(id='t2',title='样例备选：如何识别论据不足',angle='先检查来源与推断之间的距离',why_today='测试第二张选题卡',reader_value='明确哪些结论还不能下',evidence_ids=['S1'],limitations=['仅用于测试']),dict(id='t3',title='样例备选：追踪论点何时失效',angle='给观点设置可被推翻的条件',why_today='测试第三张选题卡',reader_value='不把解释当确定性预言',evidence_ids=['S1'],limitations=['仅用于测试'])]
        second['wechat_title']='【功能测试样例】价格上涨，就说明公司变好了吗？'
        second['wechat_markdown']='# '+second['wechat_title']+'\n\n> 以下为人工编写的模拟AI回执，仅验收界面及复制操作，不是真实模型生成，也不是投资分析交付。\n\n'+second['wechat_markdown']
        second['xhs_title']='【测试】上涨不等于变好'
        second['card_plan'][0]['title']='【测试样例】上涨≠变好'
        cfg=core.config(True);cfg.update(provider_mode='openai',api_key='fixture-only-not-real',api_base='https://model.example/v1',model='fixture-model',schedule_enabled=False)
        core.save_config(cfg)
        requester=fixture.requester(first,second)
        with patch.object(core,'collect',return_value=(facts,[])),patch('stock_agent.research.enrich',side_effect=lambda f,r:(f,[])),patch.object(core,'request',requester):
            j=core.start_job('daily')
            # Wait for this owned producer, not a separate managed background job.
            for thread in threading.enumerate():
                if thread is not threading.current_thread() and thread.daemon:thread.join(timeout=30)
        j=core.get(j['id'])
        if not j.get('content_ready'):raise RuntimeError('Fixture did not exercise successful v2 pipeline')
        j['data_origin']='功能测试：模拟AI回执及虚构测试资料；不是实际模型生成'
        core.put(j)
        print('FIXTURE_JOB='+j['id'],flush=True)
        core.save_config(dict(provider_mode='facts',clear_api_key=True,model='',api_base='https://api.deepseek.com'))
        materials=copy.deepcopy(j);materials.update(id='b'*16,title='无模型资料模式验收',engine_mode='facts',content_ready=False,status='materials_ready',editorial={},cards=[],created_at=core.stamp())
        core.put(materials)
    finally:fixture.doCleanups()

if __name__=='__main__':
    seed();server.main()
