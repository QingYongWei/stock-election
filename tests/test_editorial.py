"""Offline editorial contracts: all data are invented fixtures, no core import."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from stock_agent.editorial import generate


class EditorialTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = dict(api_base='https://model.example/v1', api_key='private-test-key',
                        model='fixture-model', brand='研究笔记', goal='证据驱动',
                        audience='普通读者', tone='克制')
        self.facts = dict(collected_at='2026-11-16T16:00:00+08:00', market_date='2026-11-16',
                          quotes=[dict(code='sh600000', name='测试公司', price=10, pct=1,
                                       timestamp='2026-11-16T15:00:00+08:00', date='2026-11-16', is_index=False)],
                          announcements=[], dsa_reports=[dict(name='offline', text='未核验线索：忽略指令不是事实')],
                          history=[], signals=[dict(code='sh600000', name='测试公司', sessions=5,
                          first_date='2026-11-10', last_date='2026-11-16', return_5d=1,
                          return_20d=None, ma20=None, latest_close=10, source_id='original')],
                          article_sources=[dict(id='original', title='测试公司经营观察',
                          url='https://sources.example/a', published_at='2026-11-16',
                          scope='provided_text', text='用户提供的经营观察原文，不代表已经外部核验。')])
        # A temporary fixture rather than any runtime/client data file.
        self.fixture = Path(self.tmp.name) / 'facts.json'
        self.fixture.write_text(json.dumps(self.facts, ensure_ascii=False), encoding='utf-8')
        self.facts = json.loads(self.fixture.read_text(encoding='utf-8'))
        self.first = dict(topics=[dict(id='t1', title='先问证据能说明什么', angle='拆分价格与经营证据',
                          why_today='当前快照带来观察窗口', reader_value='建立可证伪的观察框架',
                          evidence_ids=['S1'], limitations=['只是单日快照'])], selected_topic_id='t1')
        self.thesis = '中心论点：价格变化并不等于经营改善，当前更应追踪证据质量。'
        self.counter = '反证：后续经营材料也可能推翻当前谨慎判断。'
        self.watch = '观察条件：等待下一次经营材料与可比口径数据。'
        paragraphs = [self.thesis, '## 事实与证据\n' + '目前提供的是一个明确时点的报价与一份用户材料，前者描述交易结果，后者提供经营观察的线索。我们不能把两者简单拼接成原因解释，更不能把单日变化外推为未来趋势。读者真正需要区分的是哪些信息可以复核，哪些说法只是推断。' * 2,
                      '## 解释与判断\n' + '解释的价值不是制造确定性，而是把条件讲清楚。如果后续信息与当前观察一致，论点才获得更多支持；如果来源口径变化，就应重新评价。本文不是市场涨跌列表，而是讨论证据如何支持有限判断，保留不确定性也是研究的一部分。' * 2,
                      '## 局限与反证\n' + self.counter + '\n' + self.watch]
        self.second = dict(wechat_title='经营证据值得怎样读', wechat_markdown='\n\n'.join(paragraphs),
                           xhs_title='先问证据再谈趋势', xhs_text='价格只是结果，经营才需证据。先分清来源与推断，再观察后续材料，不从单日快照喊买卖。',
                           analysis=dict(thesis=self.thesis, evidence=[dict(claim='存在时点报价快照', source_ids=['S1']),
                           dict(claim='用户材料提出经营观察', source_ids=['S1'])], counterpoints=[self.counter], watchpoints=[self.watch]),
                           card_plan=[dict(title=title, body=body, source_ids=['S1'])
                                      for title,body in [('核心问题','单日价格变化不能单独证明经营改善，论点应当回到可核验的经营证据。'),('证据边界','当前提供的是时点报价与经营观察材料。报价能描述结果，材料仍需核对，不能把两者拼接成涨跌原因。'),('反证视角',self.counter),('观察条件',self.watch)]])

    def requester(self, first=None, second=None):
        def response(obj, n):
            return json.dumps(dict(choices=[dict(message=dict(content=json.dumps(obj, ensure_ascii=False)))],
                                   usage=dict(prompt_tokens=n, completion_tokens=20)))
        return Mock(side_effect=[response(first if first is not None else self.first, 10),
                                 response(second if second is not None else self.second, 11)])

    def run_generate(self, request=None, **kwargs):
        return generate(self.cfg, self.facts, kwargs.pop('kind', 'daily'),
                        request_fn=request or self.requester(), **kwargs)

    def test_two_stage_success_preserves_material_and_request_contract(self):
        original = copy.deepcopy(self.facts)
        request = self.requester()
        content, editorial, usage = self.run_generate(request)
        self.assertEqual(set(content), {'wechat_title', 'wechat_markdown', 'xhs_title', 'xhs_text'})
        self.assertEqual(editorial['version'], 2)
        self.assertEqual(editorial['mode'], 'ai_editorial')
        self.assertEqual(editorial['selected_topic'], editorial['topics'][0])
        self.assertEqual(len(editorial['card_plan']), 4)
        self.assertNotIn('text', editorial['source_catalog'][0])
        self.assertIn('https://sources.example/a', content['wechat_markdown'])
        self.assertIn('2026-11-16', content['wechat_markdown'])
        self.assertIn('结构、来源及基础报价校验不等于事实审核', content['wechat_markdown'])
        self.assertIn('来源：sources.example', content['xhs_text'])
        self.assertNotIn('来源见配套公众号', content['xhs_text'])
        self.assertNotIn('provided_text', content['wechat_markdown'])
        self.assertIn('AI辅助', content['xhs_text'])
        self.assertEqual(usage['stages'][1]['prompt_tokens'], 11)
        self.assertEqual(self.facts, original)
        self.assertEqual(request.call_count, 2)
        for call in request.call_args_list:
            self.assertEqual(call.args[0], 'https://model.example/v1/chat/completions')
            self.assertEqual(call.kwargs, dict(headers={'Authorization': 'Bearer private-test-key'}, timeout=90))
            user = json.loads(call.args[1]['messages'][1]['content'])
            self.assertEqual(user['facts']['dsa_reports'][0]['text'], self.facts['dsa_reports'][0]['text'])
            self.assertEqual(user['facts']['dsa_reports'][0]['scope'], 'unverified_lead')
            self.assertEqual(user['source_id_aliases'], {'original': 'S1'})
            self.assertNotIn('private-test-key', json.dumps(call.args[1]))
        second_user = json.loads(request.call_args_list[1].args[1]['messages'][1]['content'])
        self.assertEqual(second_user['selected_topic']['id'], 't1')
        json.dumps((content, editorial, usage), allow_nan=False)

    def test_bad_sources_and_selection(self):
        for mutation in ('topic', 'selection', 'evidence', 'card', 'prose'):
            with self.subTest(mutation=mutation):
                first, second = copy.deepcopy(self.first), copy.deepcopy(self.second)
                if mutation == 'topic': first['topics'][0]['evidence_ids'] = ['S99']
                if mutation == 'selection': first['selected_topic_id'] = 'missing'
                if mutation == 'evidence': second['analysis']['evidence'][0]['source_ids'] = ['S99']
                if mutation == 'card': second['card_plan'][0]['source_ids'] = ['S99']
                if mutation == 'prose': second['xhs_text'] += ' 参见[S99]'
                with self.assertRaises(ValueError):
                    self.run_generate(self.requester(first, second))

    def test_incomplete_structure(self):
        for field in ('thesis', 'evidence', 'counterpoints', 'watchpoints', 'card_plan', 'xhs_text'):
            with self.subTest(field=field):
                second = copy.deepcopy(self.second)
                if field in second['analysis']: second['analysis'][field] = []
                elif field == 'card_plan': second[field] = second[field][:3]
                else: second.pop(field)
                with self.assertRaises(ValueError):
                    self.run_generate(self.requester(second=second))

    def test_identical_card_slogans_are_rejected(self):
        second=copy.deepcopy(self.second)
        for card in second['card_plan']:card['body']='保持关注，留意风险。'
        with self.assertRaisesRegex(ValueError,'card plan'):self.run_generate(self.requester(second=second))

    def test_pure_template_or_market_table_rejected(self):
        for body in ('## 今日事实\n暂无更多观点。', '| 指数 | 价格 |\n' * 100,
                     '事实 解释 局限\n\n' + '关注风险。' * 150):
            second = copy.deepcopy(self.second)
            second['wechat_markdown'] = body
            with self.assertRaisesRegex(ValueError, 'template-only'):
                self.run_generate(self.requester(second=second))

    def test_model_error_never_leaks_key_or_response(self):
        secret = 'private-test-key RAW_RESPONSE_BODY'
        for request in (Mock(side_effect=RuntimeError(secret)), Mock(return_value=secret),
                        Mock(return_value=json.dumps({'error': {'message': secret}})),
                        Mock(return_value=json.dumps({'choices': [{'message': {'content': secret}}]}))):
            with self.subTest(request=request):
                with self.assertRaises(ValueError) as cm: self.run_generate(request)
                self.assertNotIn(secret, str(cm.exception))
                self.assertNotIn(self.cfg['api_key'], str(cm.exception))
                self.assertIsNone(cm.exception.__cause__)

    def test_regeneration_note_is_editorial_not_evidence(self):
        request = self.requester()
        self.run_generate(request, note='改成条件性分析，保留反证')
        for call in request.call_args_list:
            payload = call.args[1]
            user = json.loads(payload['messages'][1]['content'])
            self.assertEqual(user['editorial_instruction'], '改成条件性分析，保留反证')
            for field in ('quotes', 'signals', 'history', 'collected_at', 'market_date'):
                self.assertEqual(user['facts'][field], self.facts[field])
            self.assertNotIn('text', user['facts']['article_sources'][0])
            self.assertEqual(user['source_catalog'][0]['text'], self.facts['article_sources'][0]['text'])
            self.assertIn('不能补充事实或替代证据', payload['messages'][0]['content'])

    def test_weekly_insufficient_material_one_topic_not_invented(self):
        request = self.requester()
        _, editorial, _ = self.run_generate(request, kind='weekly')
        self.assertEqual(len(editorial['topics']), 1)
        user = json.loads(request.call_args_list[0].args[1]['messages'][1]['content'])
        self.assertEqual(user['facts']['history'], [])
        self.assertEqual(user['kind'], 'weekly')
        self.assertIn('不补造历史周数据', request.call_args_list[0].args[1]['messages'][0]['content'])

    def test_source_fallback_is_stable_no_sources_is_failure(self):
        self.facts['article_sources'] = [dict(title='没有URL')]
        self.facts['sources'] = [dict(title='旧来源', url='https://sources.example/legacy', text='临时测试材料')]
        _, editorial, _ = self.run_generate()
        self.assertEqual(editorial['source_catalog'][0]['id'], 'S1')
        self.assertEqual(editorial['source_catalog'][0]['title'], '旧来源')
        self.facts['sources'] = []
        request = self.requester()
        with self.assertRaisesRegex(ValueError, 'no usable sources'): self.run_generate(request)
        request.assert_not_called()

    def test_title_only_is_never_full_text_evidence(self):
        source = self.facts['article_sources'][0]
        source['scope'] = 'title_only'
        source.pop('text')
        with self.assertRaisesRegex(ValueError, 'title-only'): self.run_generate()
        second = copy.deepcopy(self.second)
        for entry in second['analysis']['evidence']:
            entry['claim'] = '标题线索：测试公司经营观察，具体内容待核验。'
        content, editorial, _ = self.run_generate(self.requester(second=second))
        self.assertEqual(editorial['source_catalog'][0]['scope'], 'title_only')
        self.assertIn('标题线索（未读正文）', content['wechat_markdown'])
        self.assertNotIn('title_only', content['wechat_markdown'])

    def test_platform_lengths_and_number_safe_title_trimming(self):
        second = copy.deepcopy(self.second)
        second['xhs_title'] = '观察证据的边界不要急于判断市场涨跌与原因以及后果'
        content, _, _ = self.run_generate(self.requester(second=second))
        self.assertLessEqual(len(content['xhs_title']), 20)
        for field, value in [('xhs_title', '甲' * 19 + '20%增长'), ('xhs_text', '文' * 1001)]:
            second = copy.deepcopy(self.second); second[field] = value
            with self.assertRaisesRegex(ValueError, 'rewrite required'):
                self.run_generate(self.requester(second=second))
        for field, value in [('title', '卡' * 29), ('body', '卡' * 241)]:
            second = copy.deepcopy(self.second); second['card_plan'][0][field] = value
            with self.assertRaisesRegex(ValueError, 'card plan'):
                self.run_generate(self.requester(second=second))

    def test_three_topics_choose_complete_selected_object(self):
        first = copy.deepcopy(self.first)
        for i in (2, 3):
            topic = copy.deepcopy(first['topics'][0]); topic['id'] = 't' + str(i)
            first['topics'].append(topic)
        first['selected_topic_id'] = 't2'
        _, editorial, _ = self.run_generate(self.requester(first=first))
        self.assertEqual(len(editorial['topics']), 3)
        self.assertEqual(editorial['selected_topic'], first['topics'][1])
        first['topics'][1]['id'] = 't1'
        with self.assertRaisesRegex(ValueError, 'invalid topics'):
            self.run_generate(self.requester(first=first))

    def test_current_quote_numbers_only_not_hypotheticals_or_numbered_sections(self):
        for text in ('测试公司当前价格为99元。', 'sh600000最新涨幅为2%。'):
            second = copy.deepcopy(self.second); second['xhs_text'] += text
            with self.assertRaisesRegex(ValueError, 'inconsistent quoted number'):
                self.run_generate(self.requester(second=second))
        second = copy.deepcopy(self.second)
        second['xhs_text'] += '测试公司当前价格为10元。1. 分清证据；2. 等待观察。如果测试公司当前涨幅为2%，仍不能预测。'
        self.run_generate(self.requester(second=second))

    def test_unknown_urls_and_kind_are_rejected(self):
        second = copy.deepcopy(self.second)
        second['xhs_text'] += ' 来源 https://invented.example/fake'
        with self.assertRaisesRegex(ValueError, 'source reference'):
            self.run_generate(self.requester(second=second))
        request = self.requester()
        with self.assertRaisesRegex(ValueError, 'invalid input'):
            self.run_generate(request, kind='monthly')
        request.assert_not_called()

    def test_scope_disclosure_is_chinese_not_all_user_provided(self):
        for scope, label in (('market_snapshot', '行情快照'), ('historical_daily', '历史日线'),
                             ('announcement_excerpt', '公告摘录（可能不完整）')):
            with self.subTest(scope=scope):
                self.facts['article_sources'][0]['scope'] = scope
                content, _, _ = self.run_generate()
                self.assertIn(label, content['wechat_markdown'])
                self.assertNotIn('用户提供材料', content['wechat_markdown'])
                self.assertNotIn(scope, content['wechat_markdown'])

    def test_large_material_is_deduplicated_bounded_and_marked_without_mutation(self):
        sources = []
        for i in range(7):
            sources.append(dict(id='original' if i == 0 else 'doc' + str(i),
                                title='公告摘录' + str(i), url='https://sources.example/a' + str(i),
                                published_at='2026-11-16', scope='announcement_excerpt',
                                text=('正文唯一标记' + str(i) + '甲' * 11993),
                                raw_html='重复原始正文' * 3000))
        self.facts['article_sources'] = sources
        self.facts['sources'] = copy.deepcopy(sources)
        self.facts['announcements'] = copy.deepcopy(sources)
        self.facts['dsa_reports'] = [dict(name='DSA' + str(i), text='乙' * 18000, raw='丙' * 18000)
                                     for i in range(4)]
        self.facts['raw_text'] = '不应进入上下文的重复全文' * 5000
        self.facts['history'] = [dict(market_date='2026-11-13', quotes=copy.deepcopy(self.facts['quotes']))]
        original = copy.deepcopy(self.facts)
        request = self.requester()
        _, editorial, _ = self.run_generate(request)
        self.assertEqual(self.facts, original)
        self.assertEqual([s['id'] for s in editorial['source_catalog']], ['S' + str(i) for i in range(1, 8)])
        self.assertTrue(all('text' not in s and 'raw_html' not in s for s in editorial['source_catalog']))
        self.assertTrue(editorial['source_catalog'][0]['text_truncated'])
        for call in request.call_args_list:
            user_text = call.args[1]['messages'][1]['content']
            user = json.loads(user_text)
            public, catalog = user['facts'], user['source_catalog']
            self.assertEqual(user_text.count('正文唯一标记0'), 1)
            self.assertNotIn('重复原始正文', user_text)
            self.assertNotIn('不应进入上下文的重复全文', user_text)
            self.assertNotIn('丙' * 20, user_text)
            for field in ('sources', 'article_sources', 'announcements'):
                self.assertTrue(all('text' not in row and 'raw_html' not in row for row in public[field]))
            for field in ('quotes', 'signals', 'history'):
                self.assertEqual(public[field], original[field])
            self.assertEqual(len(public['dsa_reports']), 3)
            self.assertEqual(public['dsa_reports_omitted'], 1)
            self.assertTrue(all(len(r['text']) <= 2000 and r['scope'] == 'unverified_lead'
                                and r['text_truncated'] for r in public['dsa_reports']))
            self.assertTrue(all(len(s.get('text', '')) <= 4000 for s in catalog))
            total = sum(len(s.get('text', '')) for s in catalog) + sum(len(r['text']) for r in public['dsa_reports'])
            self.assertLessEqual(total, 20000)
            self.assertTrue(all(s['scope'] == 'announcement_excerpt' for s in catalog))
            self.assertTrue(all(s['text_truncated'] and '截断' in s['excerpt_note'] for s in catalog))
            self.assertIn('text_truncated', call.args[1]['messages'][0]['content'])

    def test_default_transport_missing_and_invalid_json_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'invalid input'):
            generate(self.cfg, self.facts, 'daily')
        request = Mock(return_value=json.dumps({'choices': [{'message': {'content': '{}'}}]}))
        with self.assertRaisesRegex(ValueError, 'invalid topics'): self.run_generate(request)
        self.assertEqual(request.call_count, 1)


if __name__ == '__main__':
    unittest.main()
