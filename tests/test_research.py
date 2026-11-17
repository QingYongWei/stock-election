"""Deterministic source parsing/derived-signal tests; no outbound network."""
import json
import math
import unittest
from unittest.mock import Mock
from stock_agent import research

class ResearchTests(unittest.TestCase):
    def quote(self):return dict(code='sh600519',name='测试公司',date='2026-11-17',timestamp='2026-11-17T10:00:00+08:00')
    def history(self):
        return [[f'2026-11-{i:02d}','10',str(100+i),'200','50','10'] for i in range(1,18)]
    def req(self,rows):return Mock(return_value=json.dumps({'data':{'sh600519':{'qfqday':rows}}}))

    def test_stock_only_materials_do_not_require_index_quotes(self):
        from stock_agent import core
        quote=self.quote();quote.update(price=116,pct=1,is_index=False)
        facts=dict(collected_at=quote['timestamp'],market_date=quote['date'],quotes=[quote],announcements=[],sources=[],history=[])
        content=core.compose_facts({'brand':'测试'},facts,'daily')
        self.assertIn('不据此总结大盘走势',content['wechat_markdown'])
        self.assertIn('测试公司',content['wechat_markdown'])

    def test_intraday_candle_is_not_called_a_closed_session(self):
        signal,source=research.historical_signal(self.quote(),self.req(self.history()),'2026-11-17')
        self.assertEqual(signal['last_date'],'2026-11-16')
        self.assertEqual(signal['latest_close'],116)
        self.assertEqual(signal['return_5d'],round((116/111-1)*100,2))
        self.assertIsNone(signal['ma20'])
        self.assertEqual(source['scope'],'historical_prices')

    def test_close_session_and_malformed_future_rows(self):
        quote=self.quote();quote['timestamp']='2026-11-17T16:00:00+08:00'
        rows=self.history()+[['2026-11-18','1','999','1','1'],['bad'],['2026-11-10','1','NaN'],['2026-11-09','1','-1']]
        signal,_=research.historical_signal(quote,self.req(rows),'2026-11-17')
        self.assertEqual(signal['last_date'],'2026-11-17')
        self.assertEqual(signal['latest_close'],117)
        self.assertTrue(math.isfinite(signal['return_5d']))

    def test_history_is_sorted_and_deduplicated(self):
        rows=list(reversed(self.history()))+[self.history()[1]]
        q=self.quote();q['timestamp']='2026-11-17T16:00:00+08:00'
        signal,_=research.historical_signal(q,self.req(rows),'2026-11-17')
        self.assertEqual(signal['sessions'],17)
        self.assertEqual(signal['first_date'],'2026-11-01')

    def test_insufficient_history_fails_instead_of_inventing_returns(self):
        with self.assertRaises(ValueError):research.historical_signal(self.quote(),self.req(self.history()[:3]),'2026-11-17')

    def item(self):return dict(title='测试公告',url='https://data.eastmoney.com/notices/detail/600519/AN123.html',published_at='2026-11-16T18:00:00+08:00')
    def test_announcement_excerpt_not_fulltext_claim(self):
        body='<p>'+('这是明确的测试公告正文片段。'*10)+'</p><script>DO_NOT_INCLUDE_SCRIPT</script>'
        req=Mock(return_value=json.dumps({'data':{'art_code':'AN123','notice_content':body}}))
        result=research.announcement_body(self.item(),req)
        self.assertEqual(result['scope'],'announcement_excerpt')
        self.assertNotIn('DO_NOT_INCLUDE_SCRIPT',result['text'])
        self.assertIn('不等于完整原文已核验',result['note'])

    def test_upstream_ai_summary_is_not_presented_as_announcement_text(self):
        req=Mock(return_value=json.dumps({'data':{'art_code':'AN123','is_ai_summary':True,'notice_content':'上游模型摘要。'*100}}))
        self.assertIsNone(research.announcement_body(self.item(),req))

    def test_wrong_announcement_id_rejected(self):
        req=Mock(return_value=json.dumps({'data':{'art_code':'AN999','notice_content':'测试'*100}}))
        self.assertIsNone(research.announcement_body(self.item(),req))

    def test_short_or_missing_body_stays_title_only(self):
        req=Mock(return_value=json.dumps({'data':{'notice_content':'短文本'}}))
        self.assertIsNone(research.announcement_body(self.item(),req))

    def test_enrichment_binds_all_signal_source_ids_and_preserves_facts(self):
        q=self.quote();q['price']=116;q['pct']=1;q['is_index']=False
        ann=self.item();ann['kind']='announcement'
        facts=dict(quotes=[q],market_date='2026-11-17',announcements=[ann],sources=[dict(title='行情',url='https://qt.gtimg.cn/',kind='market',published_at=q['timestamp']),ann])
        original=json.dumps(facts,sort_keys=True)
        def request(url,**kwargs):
            if 'fqkline' in url:return self.req(self.history())()
            return json.dumps({'data':{'art_code':'AN123','notice_content':'测试摘录。'*100}})
        result,warnings=research.enrich(facts,request)
        self.assertEqual(json.dumps(facts,sort_keys=True),original)
        self.assertEqual([s['id'] for s in result['sources']],['S1','S2','S3'])
        self.assertEqual(result['signals'][0]['source_id'],'S3')
        self.assertEqual(result['article_sources'][1]['scope'],'announcement_excerpt')
        self.assertFalse(warnings)

    def test_source_failures_do_not_fabricate_trends_or_article_text(self):
        facts=dict(quotes=[self.quote()],market_date='2026-11-17',announcements=[self.item()],sources=[dict(title='公告线索',url=self.item()['url'],kind='announcement')])
        result,warnings=research.enrich(facts,Mock(side_effect=OSError('offline fixture')))
        self.assertEqual(result['signals'],[])
        self.assertEqual(result['sources'][0]['scope'],'title_only')
        self.assertNotIn('text',result['sources'][0])
        self.assertEqual(len(warnings),2)

if __name__=='__main__':unittest.main()
