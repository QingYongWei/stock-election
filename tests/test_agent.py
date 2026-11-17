"""Offline regression tests: python -m unittest discover -s tests -v.

Only temporary SQLite/artifact storage is used. All outbound HTTP is blocked by
 default; quote/announcement responses and card rendering are local fixtures.
No real provider credentials, API calls, sockets or scheduler threads are used.
"""
from __future__ import annotations

import base64
import copy
import datetime as dt
import io
import json
import tempfile
import unittest
import zipfile
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from stock_agent import core, server


FIXED_NOW = dt.datetime(2026, 11, 16, 18, 0, tzinfo=core.TZ)
# Real one-pixel PNG, used as a cheap deterministic render_cards replacement.
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
CONTENT_FIELDS = ('wechat_title', 'wechat_markdown', 'xhs_title', 'xhs_text')


def quote_line(code='sh000001', name='上证指数', timestamp='20261116150000', pct='1.25'):
    fields = [''] * 38
    for index, value in {1: name, 3: '3200.5', 30: timestamp, 31: '39.5',
                         32: pct, 33: '3210', 34: '3180'}.items():
        fields[index] = value
    return f'v_{code}="' + '~'.join(fields) + '";'


class InlineThread:
    """Run production synchronously, exercising start_job without race/sleep."""
    def __init__(self, target, args=(), **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class IsolatedAgentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='stock-agent-tests-')
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.start_patch('DATA', self.data)
        self.start_patch('now', return_value=FIXED_NOW)
        self.start_patch('RUN_LOCK', __import__('threading').Lock())
        self.network = self.start_patch('request', side_effect=AssertionError('Unexpected outbound API request'))
        # Fail closed even if future implementation bypasses core.request.
        urlopen_patch = patch('urllib.request.urlopen', side_effect=AssertionError('Real HTTP forbidden'))
        self.urlopen = urlopen_patch.start()
        self.addCleanup(urlopen_patch.stop)
        self.renderer = self.start_patch('render_cards', side_effect=self.render_fixture)
        thread_patch = patch.object(core, 'ProducerThread', InlineThread)
        thread_patch.start()
        self.addCleanup(thread_patch.stop)
        core.init()

    def start_patch(self, name, *args, **kwargs):
        p = patch.object(core, name, *args, **kwargs)
        result = p.start()
        self.addCleanup(p.stop)
        return result

    def render_fixture(self, job):
        folder = self.data / 'artifacts' / job['id']
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(1, 5):
            (folder / f'card-{i}.png').write_bytes(PNG)
        return [f'/artifacts/{job["id"]}/card-{i}.png' for i in range(1, 5)]

    def mock_market(self):
        def response(url, *args, **kwargs):
            if url.startswith('https://qt.gtimg.cn/q='):
                self.assertEqual(kwargs.get('encoding'), 'gb18030')
                return ''.join(quote_line(code, name, pct=pct) for code, name, pct in (
                    ('sh000001', '上证指数', '1.25'), ('sz399001', '深证成指', '-2.50'),
                    ('sz399006', '创业板指', '0.50'), ('sh600519', '贵州茅台', '0.75')))
            if url.startswith('https://np-anotice-stock.eastmoney.com/'):
                return json.dumps({'data': {'list': [{
                    'display_time': '2026-11-16 16:00:00', 'codes': [{'stock_code': '600519'}],
                    'art_code': 'AN123456', 'title': '董事会公告（测试索引）'}]}})
            raise AssertionError('Unexpected fixture URL: ' + url)
        self.network.side_effect = response

    def generate(self, kind='daily', **kwargs):
        self.mock_market()
        initial = core.start_job(kind, **kwargs)
        self.assertEqual(initial['status'], 'running')
        return core.get(initial['id'])

    def configure_ai(self):
        """Real two-stage editorial generation; only external research/HTTP is fixture."""
        from tests.test_editorial import EditorialTest
        helper = EditorialTest()
        helper.setUp()
        self.editorial_fixture = helper
        self.addCleanup(helper.doCleanups)
        core.save_config({'provider_mode': 'openai', 'api_key': 'dummy-offline-secret',
                          'model': 'offline-model', 'api_base': 'https://model.example.test/v1'})
        self.mock_market()
        market_response = self.network.side_effect
        self.model_calls = []
        def response(url, *args, **kwargs):
            if url == 'https://model.example.test/v1/chat/completions':
                self.model_calls.append((url, args, kwargs))
                obj = helper.first if len(self.model_calls) % 2 else helper.second
                return json.dumps({'choices': [{'message': {'content': json.dumps(obj, ensure_ascii=False)}}],
                                   'usage': {'prompt_tokens': 10, 'completion_tokens': 20}})
            return market_response(url, *args, **kwargs)
        self.network.side_effect = response
        def enrich(facts, request):
            enriched = copy.deepcopy(facts)
            enriched['article_sources'] = copy.deepcopy(helper.facts['article_sources'])
            enriched['signals'] = []
            return enriched, []
        p = patch('stock_agent.research.enrich', side_effect=enrich)
        self.research = p.start()
        self.addCleanup(p.stop)

    def generate_ai(self, kind='daily', **kwargs):
        self.configure_ai()
        initial = core.start_job(kind, **kwargs)
        job = core.get(initial['id'])
        self.assertEqual(job['status'], 'review_required', job.get('error'))
        self.assertTrue(job['content_ready'])
        self.assertTrue(core.public_job(job)['publication']['ready'])
        self.assertEqual(len(self.model_calls), 2)
        return job

    def api(self, method, path, payload=None, headers=None, raw_body=None):
        """Exercise real Handler routing/validation without opening any socket."""
        handler = object.__new__(server.Handler)
        handler.server = SimpleNamespace(server_port=8765)
        handler.command, handler.path = method, path
        handler.headers = Message()
        for key, value in {'Host': '127.0.0.1:8765', 'Content-Type': 'application/json', **(headers or {})}.items():
            handler.headers[key] = value
        body = raw_body if raw_body is not None else json.dumps(payload or {}).encode()
        handler.headers['Content-Length'] = str(len(body))
        handler.rfile = io.BytesIO(body)
        handler.send = Mock()
        handler.route(method)
        self.assertEqual(handler.send.call_count, 1)
        args = handler.send.call_args.args
        return args[0], args[1], args[2:]


class ConfigTests(IsolatedAgentTest):
    def test_secret_not_returned_preserved_and_explicitly_cleared(self):
        self.assertNotIn('api_key', core.config())
        self.assertFalse(core.config()['has_api_key'])
        response = core.save_config({'api_key': '  dummy-offline-secret  '})
        self.assertNotIn('api_key', response)
        self.assertTrue(response['has_api_key'])
        for payload in ({'brand': '测试品牌'}, {'api_key': ''}, {}):
            core.save_config(payload)
            self.assertEqual(core.config(True)['api_key'], 'dummy-offline-secret')
        response = core.save_config({'clear_api_key': True, 'api_key': 'ignored'})
        self.assertFalse(response['has_api_key'])
        self.assertEqual(core.config(True)['api_key'], '')

    def test_config_validation_does_not_persist_invalid_input(self):
        before = core.config(True)
        invalid = [[], {'schedule_time': '24:00'}, {'schedule_enabled': 1},
                   {'weekly_day': True}, {'weekly_day': 7}, {'provider_mode': 'unknown'},
                   {'stock_list': 'bad'}, {'brand': 7}, {'brand': 'x' * 4001},
                   {'provider_mode': 'openai', 'model': 'offline'},
                   {'provider_mode': 'openai', 'model': 'offline', 'api_key': 'dummy',
                    'api_base': 'https://example.test/v1?key=secret'}]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                core.save_config(payload)
            self.assertEqual(core.config(True), before)

    def test_codes_normalize_deduplicate_and_reject_invalid_or_too_many(self):
        self.assertEqual(core.codes('600519， 300750\nsz300750 bj830001 900001'),
                         ['sh600519', 'sz300750', 'bj830001', 'sh900001'])
        self.assertEqual(core.codes(''), [])
        for raw in ('AAPL', 'SH600519', '12345', '../600519', ','.join(f'{n:06d}' for n in range(21))):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                core.codes(raw)

    def test_public_job_hides_configuration_snapshot(self):
        core.save_config({'api_key': 'dummy-offline-secret'})
        job = self.generate()
        self.assertNotIn('api_key', job['config_snapshot'])
        self.assertNotIn('config_snapshot', core.public_job(job))
        self.assertNotIn('dummy-offline-secret', json.dumps(core.public_job(job)))


class MarketAndContentTests(IsolatedAgentTest):
    def test_quote_parser_filters_malformed_and_future_rows(self):
        self.network.side_effect = None
        self.network.return_value = '\n'.join([
            quote_line(), 'v_bad="too~short";', quote_line('sz399001', pct='NaN-not-a-number'),
            quote_line('sz399006', timestamp='20261116180600'),
            quote_line('sh600519', timestamp='invalid')])
        rows, source = core.quote_data('600519,600519')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['date'], '2026-11-16')
        self.assertEqual(rows[0]['timestamp'], '2026-11-16T15:00:00+08:00')
        self.assertEqual(rows[0]['price'], 3200.5)
        self.assertEqual(rows[0]['pct'], 1.25)
        self.assertTrue(rows[0]['is_index'])
        self.assertEqual(source['kind'], 'market')
        self.assertEqual(source['url'].count('sh600519'), 1)
        self.urlopen.assert_not_called()

    def test_no_valid_quote_raises_instead_of_mock_fallback(self):
        self.network.side_effect = None
        for text in ('', 'v_sh000001="~";', quote_line(timestamp='20991116150000')):
            self.network.return_value = text
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, '未使用模拟数据'):
                core.quote_data('')

    def test_announcement_parser_filters_age_future_and_invalid_identifiers(self):
        rows = []
        for date, code, article in [('2026-11-16 16:00:00', '600519', 'AN123'),
                                    ('2026-11-17 16:00:00', '600519', 'AN124'),
                                    ('2026-11-01 16:00:00', '600519', 'AN125'),
                                    ('invalid', '600519', 'AN126'),
                                    ('2026-11-16 16:00:00', '../bad', 'AN127'),
                                    ('2026-11-16 16:00:00', '600519', '../bad')]:
            rows.append(dict(display_time=date, codes=[dict(stock_code=code)], art_code=article, title='公告'))
        self.network.side_effect = None
        self.network.return_value = json.dumps({'data': {'list': rows}})
        result = core.announcements()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['url'], 'https://data.eastmoney.com/notices/detail/600519/AN123.html')
        self.assertIn('尚未逐条核验', result[0]['note'])

    def test_collect_and_compose_actual_date_sources_and_no_invented_news(self):
        self.mock_market()
        with patch.object(core, 'now', return_value=FIXED_NOW + dt.timedelta(days=1)), \
             patch.object(core, 'announcements', side_effect=OSError('offline')):
            facts, checks = core.collect(core.config(True), 'daily')
        self.assertEqual(facts['market_date'], '2026-11-16')
        self.assertEqual(facts['announcements'], [])
        self.assertTrue(any('不是今日' in c['message'] for c in checks))
        self.assertTrue(any('不补造事件' in c['message'] for c in checks))
        content = core.compose_facts(core.config(True), facts, 'daily')
        self.assertEqual(set(content), set(CONTENT_FIELDS))
        self.assertIn('2026-11-16', content['wechat_title'])
        self.assertIn('深证成指', content['wechat_markdown'])
        self.assertIn('-2.50%', content['wechat_markdown'])
        self.assertIn('https://qt.gtimg.cn/', content['wechat_markdown'])
        self.assertIn('不据此声称当天没有公告', content['wechat_markdown'])
        self.assertFalse(any(c['level'] == 'error' for c in core.validate_content(content, facts)))

    def test_ai_disclaimer_does_not_block_approval(self):
        job = self.generate_ai()
        self.assertIn('AI辅助', job['wechat_markdown'])
        core.update_content(job['id'], {'wechat_markdown': job['wechat_markdown']
                            + '\n本文不提供具体证券交易指令或保证收益。'})
        self.assertEqual(core.review(job['id'], {'action': 'approve', 'confirm_cards': True})['status'], 'approved')

    def test_intraday_and_mixed_dates_are_flagged(self):
        self.mock_market()
        quotes, source = core.quote_data('')
        quotes[-1]['date'] = '2026-11-13'
        with patch.object(core, 'now', return_value=FIXED_NOW.replace(hour=10)), \
             patch.object(core, 'quote_data', return_value=(quotes, source)):
            _, checks = core.collect(core.config(True), 'daily')
        self.assertTrue(any('尚未收盘' in c['message'] for c in checks))
        self.assertTrue(any('日期不完全一致' in c['message'] for c in checks))

    def test_weekly_only_uses_existing_daily_snapshots_without_inventing_week_return(self):
        self.generate()
        self.generate()  # Same market date must be deduplicated.
        weekly = self.generate('weekly')
        self.assertEqual(len(weekly['facts']['history']), 1)
        self.assertIn('缺失日期不补造', weekly['wechat_markdown'])
        self.assertIn('不能当成整周涨跌', weekly['wechat_markdown'])

    def test_content_validation_date_and_blocking_fields(self):
        job = self.generate()
        content = {key: job[key] for key in CONTENT_FIELDS}
        for key in CONTENT_FIELDS:
            altered = {**content, key: content[key] + ' 建议买入'}
            with self.subTest(field=key):
                self.assertTrue(any(c['level'] == 'error' for c in core.validate_content(altered, job['facts'])))
        missing_date = {key: value.replace('2026-11-16', '日期待补') for key, value in content.items()}
        self.assertTrue(any('缺少实际行情日期' in c['message'] for c in core.validate_content(missing_date, job['facts'])))


class JobLifecycleTests(IsolatedAgentTest):
    def test_complete_material_generation_is_persisted_but_not_ready(self):
        job = self.generate()
        self.assertEqual(job['status'], 'materials_ready')
        self.assertFalse(job['content_ready'])
        self.assertFalse(core.public_job(job)['publication']['ready'])
        self.assertEqual(job['engine_mode'], 'facts')
        self.assertEqual(len(job['cards']), 4)
        self.assertEqual(len(job['facts']['quotes']), 4)
        self.assertEqual(len(job['sources']), 2)
        self.assertEqual(job['versions'][0]['action'], 'materials_collected')
        self.assertEqual(core.jobs()[0]['id'], job['id'])
        self.assertFalse(core.RUN_LOCK.locked())
        for field in CONTENT_FIELDS:
            self.assertTrue(job[field].strip())

    def test_reject_approve_and_publish_require_manual_valid_url(self):
        job = self.generate_ai()
        jid = job['id']
        with self.assertRaisesRegex(ValueError, '必须先审核通过'):
            core.review(jid, {'action': 'publish', 'publish_url': 'https://example.test/article'})
        self.assertEqual(core.review(jid, {'action': 'reject', 'note': '需核对'})['status'], 'rejected')
        approved = core.review(jid, {'action': 'approve', 'confirm_cards': True, 'note': '人工已核对'})
        self.assertEqual(approved['status'], 'approved')
        self.assertEqual(len(approved['approved_hash']), 64)
        for url in ('', 'javascript:alert(1)', 'file:///tmp/article', '/article'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                core.review(jid, {'action': 'publish', 'publish_url': url})
        published = core.review(jid, {'action': 'publish', 'publish_url': 'https://example.test/article'})
        self.assertEqual(published['status'], 'published')
        self.assertEqual(published['publish_method'], 'manual_record')
        for action in ('approve', 'reject', 'publish'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                core.review(jid, {'action': action})
        with self.assertRaises(ValueError):
            core.update_content(jid, {'wechat_title': 'new'})

    def test_edit_revokes_approval_and_tracks_previous_content(self):
        job = self.generate_ai()
        core.review(job['id'], {'action': 'approve', 'confirm_cards': True})
        edited = core.update_content(job['id'], {'wechat_title': '2026-11-16｜人工改稿'})
        self.assertEqual(edited['status'], 'review_required')
        self.assertEqual(edited['versions'][-1]['action'], 'edited')
        self.assertEqual(edited['versions'][-1]['previous']['wechat_title'], job['wechat_title'])
        self.assertEqual(self.renderer.call_count, 2)
        with self.assertRaises(ValueError):
            core.review(job['id'], {'action': 'publish', 'publish_url': 'https://example.test/article'})
        self.assertEqual(core.review(job['id'], {'action': 'approve', 'confirm_cards': True})['status'], 'approved')

    def test_every_public_content_field_can_block_approval_and_be_corrected(self):
        for field in CONTENT_FIELDS:
            with self.subTest(field=field):
                job = self.generate_ai()
                core.update_content(job['id'], {field: job[field] + '\n保证收益'})
                with self.assertRaisesRegex(ValueError, '阻断项'):
                    core.review(job['id'], {'action': 'approve', 'confirm_cards': True})
                core.update_content(job['id'], {field: job[field]})
                self.assertEqual(core.review(job['id'], {'action': 'approve', 'confirm_cards': True})['status'], 'approved')

    def test_invalid_edits_are_atomic(self):
        job = self.generate_ai()
        for bad in ('', ' ', None, 3, 'x' * 30001):
            with self.subTest(bad=str(bad)[:20]), self.assertRaises(ValueError):
                core.update_content(job['id'], {'xhs_text': bad})
            self.assertEqual(core.get(job['id']), job)

    def test_generation_failure_delivers_no_fake_quotes_or_content(self):
        self.network.side_effect = OSError('provider-secret-must-not-appear')
        initial = core.start_job()
        job = core.get(initial['id'])
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['cards'], [])
        self.assertNotIn('facts', job)
        self.assertNotIn('wechat_markdown', job)
        self.assertNotIn('provider-secret-must-not-appear', job['error'])
        self.assertFalse(core.RUN_LOCK.locked())
        for action in ('approve', 'reject', 'publish'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                core.review(job['id'], {'action': action})
        with self.assertRaises(ValueError):
            core.export_zip(job['id'])
        with self.assertRaises(ValueError):
            core.update_content(job['id'], {'wechat_title': 'fake'})
        self.assertEqual(self.generate()['status'], 'materials_ready')

    def test_card_failure_marks_job_failed_not_ready(self):
        self.mock_market()
        self.renderer.side_effect = OSError('font/render detail')
        jid = core.start_job()['id']
        self.assertEqual(core.get(jid)['status'], 'failed')
        self.assertFalse(core.RUN_LOCK.locked())
        with self.assertRaises(ValueError):
            core.export_zip(jid)

    def test_llm_failure_preserves_materials_without_fake_ready_or_secret_leak(self):
        self.configure_ai()
        core.save_config({'provider_mode': 'openai', 'api_key': 'dummy-offline-secret', 'model': 'offline'})
        with patch.object(core, 'llm_compose', side_effect=ValueError('dummy-offline-secret upstream-body')):
            job = core.get(core.start_job()['id'])
        self.assertEqual(job['status'], 'needs_ai')
        self.assertEqual(job['engine_mode'], 'facts')
        self.assertTrue(job['facts']['quotes'])
        self.assertFalse(core.public_job(job)['publication']['ready'])
        with self.assertRaises(ValueError):
            core.review(job['id'], {'action': 'approve', 'confirm_cards': True})
        self.assertNotIn('dummy-offline-secret', json.dumps(job))
        self.assertNotIn('upstream-body', json.dumps(job))

    def test_llm_json_parser_uses_two_mock_stages_and_rejects_incomplete_output(self):
        job = self.generate_ai()
        self.assertEqual(job['editorial']['version'], 2)
        self.assertEqual(job['engine_mode'], 'ai_editorial')
        for url, args, kwargs in self.model_calls:
            self.assertEqual(kwargs['headers']['Authorization'], 'Bearer dummy-offline-secret')
            self.assertNotIn('dummy-offline-secret', json.dumps(args[0]))
        self.network.side_effect = None
        self.network.return_value = json.dumps({'choices': [{'message': {'content': '{"wechat_title":"only title"}'}}]})
        with self.assertRaises(ValueError):
            core.llm_compose(core.config(True), job['facts'])
        incomplete_second = self.editorial_fixture.requester(second={'wechat_title': 'only title'})
        self.network.side_effect = incomplete_second
        with self.assertRaises(ValueError):
            core.llm_compose(core.config(True), job['facts'])
        self.assertEqual(incomplete_second.call_count, 2)
        self.urlopen.assert_not_called()

    def test_export_zip_contains_text_sources_review_and_all_cards_not_config(self):
        core.save_config({'api_key': 'dummy-offline-secret'})
        job = self.generate_ai()
        with zipfile.ZipFile(io.BytesIO(core.export_zip(job['id']))) as archive:
            self.assertEqual(set(archive.namelist()), {'wechat.md', 'wechat.html', 'xiaohongshu.txt', 'sources.json',
                'review.json', 'READ-ME.txt', 'wechat-copy.html', 'editorial.json', '发布步骤.md', *(f'cards/card-{i}.png' for i in range(1, 5))})
            self.assertEqual(archive.read('wechat.md').decode(), job['wechat_markdown'])
            self.assertEqual(json.loads(archive.read('sources.json')), job['sources'])
            self.assertEqual(json.loads(archive.read('editorial.json')), job['editorial'])
            self.assertNotIn('<h1', archive.read('wechat-copy.html').decode().lower())
            self.assertTrue(archive.read('发布步骤.md').decode().strip())
            self.assertEqual(json.loads(archive.read('review.json'))['status'], 'review_required')
            self.assertIn('不是已完成的平台发布', archive.read('READ-ME.txt').decode())
            for name in archive.namelist():
                self.assertNotIn(b'dummy-offline-secret', archive.read(name))
            self.assertEqual(archive.read('cards/card-1.png'), PNG)

    def test_job_id_kind_and_single_running_guard(self):
        for jid in ('../agent.db', 'a' * 15, 'A' * 16, 'a' * 16):
            with self.subTest(jid=jid), self.assertRaises(KeyError):
                core.get(jid)
        with self.assertRaises(ValueError):
            core.start_job('monthly')
        core.RUN_LOCK.acquire()
        try:
            with self.assertRaisesRegex(ValueError, '已有任务运行'):
                core.start_job()
        finally:
            core.RUN_LOCK.release()


class ServerTests(IsolatedAgentTest):
    def test_config_api_secret_not_returned_and_empty_field_preserves(self):
        status, data, _ = self.api('PUT', '/api/config', {'api_key': 'dummy-offline-secret'})
        self.assertEqual(status, 200)
        self.assertTrue(data['has_api_key'])
        self.assertNotIn('api_key', data)
        self.api('PUT', '/api/config', {'api_key': '', 'brand': 'API品牌'})
        self.assertEqual(core.config(True)['api_key'], 'dummy-offline-secret')
        status, data, _ = self.api('GET', '/api/config')
        self.assertEqual(status, 200)
        self.assertNotIn('dummy-offline-secret', json.dumps(data))
        self.api('PUT', '/api/config', {'clear_api_key': True})
        self.assertEqual(core.config(True)['api_key'], '')

    def test_job_api_generate_list_get_edit_review_export_and_regenerate_parent(self):
        self.configure_ai()
        status, first, _ = self.api('POST', '/api/jobs', {'kind': 'daily'})
        self.assertEqual(status, 202)
        jid = first['id']
        status, job, _ = self.api('GET', f'/api/jobs/{jid}')
        self.assertEqual(status, 200)
        self.assertEqual(job['status'], 'review_required')
        self.assertNotIn('config_snapshot', job)
        self.assertEqual(self.api('GET', '/api/jobs')[1][0]['id'], jid)
        self.assertEqual(self.api('POST', f'/api/jobs/{jid}/review', {'action': 'publish'})[0], 400)
        self.assertEqual(self.api('PUT', f'/api/jobs/{jid}/content', {'xhs_title': '2026-11-16｜人工标题'})[0], 200)
        self.assertEqual(self.api('POST', f'/api/jobs/{jid}/review', {'action': 'approve', 'confirm_cards': True})[1]['status'], 'approved')
        status, data, extra = self.api('GET', f'/api/jobs/{jid}/export')
        self.assertEqual(status, 200)
        self.assertEqual(extra[0], 'application/zip')
        self.assertIn(jid, extra[1]['Content-Disposition'])
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(data)))
        status, child, _ = self.api('POST', f'/api/jobs/{jid}/regenerate', {'note': '重新核对来源'})
        self.assertEqual(status, 202)
        self.assertNotEqual(child['id'], jid)
        generated = core.get(child['id'])
        self.assertEqual(generated['parent_id'], jid)
        self.assertEqual(generated['kind'], job['kind'])
        self.assertEqual(generated['review_note'], '重新核对来源')
        self.assertEqual(generated['status'], 'review_required')
        self.assertEqual(core.get(jid)['status'], 'approved')

    def test_security_host_origin_content_type_and_body_validation(self):
        for headers in ({'Host': 'attacker.test:8765'}, {'Origin': 'https://attacker.test'},
                        {'Content-Type': 'text/plain'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.api('PUT', '/api/config', {}, headers=headers)[0], 403)
        for body in (b'[]', b'null', b'{bad json'):
            with self.subTest(body=body):
                self.assertEqual(self.api('PUT', '/api/config', raw_body=body)[0], 400)
        self.assertEqual(self.api('PUT', '/api/config', raw_body=b'x' * 300001)[0], 400)
        self.assertEqual(self.api('GET', '/api/health')[0], 200)

    def test_missing_routes_job_and_artifact_traversal_return_404(self):
        for path in ('/missing', '/api/jobs/' + 'a' * 16, '/artifacts/../../agent.db',
                     '/artifacts/' + 'a' * 16 + '/card-1.png'):
            with self.subTest(path=path):
                self.assertEqual(self.api('GET', path)[0], 404)
        job = self.generate()
        status, image, extra = self.api('GET', job['cards'][0])
        self.assertEqual(status, 200)
        self.assertEqual(image, PNG)
        self.assertEqual(extra[0], 'image/png')

    def test_internal_error_does_not_expose_exception_body(self):
        with patch.object(core, 'config', side_effect=RuntimeError('DO-NOT-LEAK-secret')):
            status, body, _ = self.api('GET', '/api/config')
        self.assertEqual(status, 500)
        self.assertNotIn('DO-NOT-LEAK-secret', json.dumps(body))


if __name__ == '__main__':
    unittest.main()
