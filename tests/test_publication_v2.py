"""Publication-v2 contracts using temporary fixtures, no service or real HTTP."""
import copy
import hashlib
import io
import json
import threading
import zipfile
from unittest.mock import Mock, patch

from stock_agent import core, server
from tests.test_agent import IsolatedAgentTest, PNG


class PublicationV2Tests(IsolatedAgentTest):
    def test_task_stays_running_until_all_cards_are_ready(self):
        seen=[]
        def inspect_and_render(job):
            stored=core.get(job['id'])
            self.assertEqual(stored['status'],'running')
            self.assertFalse(core.public_job(stored)['publication']['ready'])
            seen.append(job.get('engine_mode'))
            return self.render_fixture(job)
        with patch.object(core,'render_cards',side_effect=inspect_and_render):
            materials=self.generate()
            ai=self.generate_ai()
        self.assertEqual(materials['status'],'materials_ready')
        self.assertEqual(ai['status'],'review_required')
        self.assertEqual(seen,['facts','ai_editorial'])

    def test_no_key_keeps_materials_and_never_calls_research_or_model(self):
        self.assertFalse(core.config()['has_api_key'])
        with patch('stock_agent.research.enrich') as enrich, patch.object(core, 'llm_compose') as compose:
            job = self.generate()
        enrich.assert_not_called()
        compose.assert_not_called()
        self.assertEqual(job['status'], 'materials_ready')
        self.assertFalse(job['content_ready'])
        self.assertFalse(core.public_job(job)['publication']['ready'])
        self.assertTrue(job['facts']['quotes'])
        self.assertTrue(job['sources'])
        self.assertEqual(len(job['cards']), 4)
        self.assertEqual(self.api('POST', f'/api/jobs/{job["id"]}/review',
                                  {'action': 'approve', 'confirm_cards': True})[0], 400)
        self.assertEqual(self.api('GET', f'/api/jobs/{job["id"]}/publish-html')[0], 400)
        self.assertEqual(self.api('GET', f'/api/jobs/{job["id"]}/preview')[0], 200)

    def test_real_two_stage_ai_pipeline_exposes_v2_publication_contract(self):
        job = self.generate_ai()
        self.research.assert_called_once()
        self.assertEqual(job['engine_mode'], 'ai_editorial')
        self.assertEqual(job['editorial']['version'], 2)
        self.assertEqual(len(job['editorial']['card_plan']), 4)
        self.assertTrue(job['editorial']['source_catalog'])
        self.assertEqual(len(job['usage']['stages']), 2)
        first = json.loads(self.model_calls[0][1][0]['messages'][1]['content'])
        second = json.loads(self.model_calls[1][1][0]['messages'][1]['content'])
        self.assertIn('source_catalog', first)
        self.assertEqual(second['selected_topic']['id'], 't1')
        self.assertNotIn('dummy-offline-secret', json.dumps(core.public_job(job)))
        self.assertEqual(core.review(job['id'], {'action': 'approve', 'confirm_cards': True})['status'], 'approved')

    def test_model_http_failure_and_invalid_json_do_not_fake_manuscript(self):
        self.configure_ai()
        market_response = self.network.side_effect
        for response in (OSError('dummy-offline-secret raw-provider-response'), '{invalid json'):
            with self.subTest(response=type(response).__name__):
                def fail_model(url, *args, **kwargs):
                    if '/chat/completions' in url:
                        if isinstance(response, Exception):
                            raise response
                        return response
                    return market_response(url, *args, **kwargs)
                self.network.side_effect = fail_model
                job = core.get(core.start_job()['id'])
                self.assertEqual(job['status'], 'needs_ai')
                self.assertFalse(job['content_ready'])
                self.assertFalse(core.public_job(job)['publication']['ready'])
                self.assertTrue(job['facts']['quotes'])
                self.assertTrue(job['sources'])
                self.assertEqual(len(job['cards']), 4)
                self.assertNotIn('raw-provider-response', json.dumps(job))
                self.assertNotIn('dummy-offline-secret', json.dumps(job))
                self.assertEqual(self.api('GET', f'/api/jobs/{job["id"]}/publish-html')[0], 400)
                with self.assertRaises(ValueError):
                    core.review(job['id'], {'action': 'approve', 'confirm_cards': True})

    def test_legacy_approved_job_is_not_v2_ready(self):
        job = self.generate()
        # Simulate only a persisted v0.1 row, not an AI-generation success fixture.
        job.pop('content_ready', None)
        job.pop('editorial', None)
        job.update(status='approved', engine_mode='facts')
        job['approved_hash'] = core.approval_digest(job)
        core.put(job)
        self.assertFalse(core.public_job(core.get(job['id']))['publication']['ready'])
        with self.assertRaisesRegex(ValueError,'资料包或旧版'):
            core.review(job['id'], {'action':'publish','publish_url':'https://example.test/offline-fixture'})
        with self.assertRaises(ValueError):
            core.review(job['id'], {'action': 'approve', 'confirm_cards': True})
        self.assertEqual(self.api('GET', f'/api/jobs/{job["id"]}/publish-html')[0], 400)

    def test_publish_html_strips_leading_h1_and_escapes_script(self):
        job = self.generate_ai()
        core.update_content(job['id'], {'wechat_markdown': '# 唯一顶部标题\n\n'
                            + job['wechat_markdown'] + '\n<script>alert(1)</script>'})
        status, html, extra = self.api('GET', f'/api/jobs/{job["id"]}/publish-html')
        self.assertEqual(status, 200)
        self.assertIn('text/html', extra[0])
        self.assertNotIn('<h1', html.lower())
        self.assertNotIn('唯一顶部标题', html)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('style=', html)
        self.assertNotIn('<link', html)
        self.assertIn('事实与证据', html)
        status, preview, _ = self.api('GET', f'/api/jobs/{job["id"]}/preview')
        self.assertEqual(status, 200)
        self.assertIn('唯一顶部标题', preview)

    def test_response_csp_allows_only_same_origin_preview_framing(self):
        handler = object.__new__(server.Handler)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = io.BytesIO()
        handler.send(200, '<p>fixture</p>', 'text/html; charset=utf-8')
        headers = dict(call.args for call in handler.send_header.call_args_list)
        csp = headers['Content-Security-Policy']
        self.assertIn("frame-ancestors 'self'", csp)
        self.assertIn("frame-src 'self'", csp)
        self.assertNotIn("frame-ancestors *", csp)

    def test_images_zip_materials_is_explicit_non_manuscript(self):
        job = self.generate()
        status, raw, extra = self.api('GET', f'/api/jobs/{job["id"]}/images')
        self.assertEqual(status, 200)
        self.assertEqual(extra[0], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertEqual(archive.namelist(), ['01.png', '02.png', '03.png', '04.png', '上传顺序.txt'])
            self.assertEqual(archive.read('01.png'), PNG)
            self.assertIn('不能当作成稿发布', archive.read('上传顺序.txt').decode())

    def test_independent_card_edit_changes_png_revokes_approval_and_marks_stale(self):
        from PIL import Image
        def card_renderer(job):
            folder = self.data / 'artifacts' / job['id']
            folder.mkdir(parents=True, exist_ok=True)
            for i, card in enumerate(job['editorial']['card_plan'], 1):
                digest = hashlib.sha256(json.dumps(card, ensure_ascii=False).encode()).digest()
                Image.new('RGB', (2, 2), tuple(digest[:3])).save(folder / f'card-{i}.png')
            return [f'/artifacts/{job["id"]}/card-{i}.png' for i in range(1, 5)]
        self.renderer.side_effect = card_renderer
        job = self.generate_ai()
        path = self.data / 'artifacts' / job['id'] / 'card-1.png'
        before = path.read_bytes()
        approved = core.review(job['id'], {'action': 'approve', 'confirm_cards': True})
        plan = copy.deepcopy(job['editorial']['card_plan'])
        plan[0]['body'] = '独立修改卡片论点，必须重新核验来源。'
        status, edited, _ = self.api('PUT', f'/api/jobs/{job["id"]}/content', {'card_plan': plan})
        self.assertEqual(status, 200)
        self.assertNotEqual(path.read_bytes(), before)
        self.assertNotEqual(core.approval_digest(core.get(job['id'])), approved['approved_hash'])
        self.assertEqual(edited['status'], 'review_required')
        self.assertTrue(edited['editorial']['analysis_stale'])
        self.assertEqual(edited['wechat_markdown'], job['wechat_markdown'])
        self.assertEqual(edited['versions'][-1]['previous_cards'], job['editorial']['card_plan'])
        with self.assertRaises(ValueError):
            core.review(job['id'], {'action': 'publish', 'publish_url': 'https://example.test/post'})
        with self.assertRaises(ValueError):
            core.review(job['id'], {'action': 'approve'})
        self.assertEqual(core.review(job['id'], {'action': 'approve', 'confirm_cards': True})['status'], 'approved')

    def test_article_edit_does_not_silently_rewrite_card_plan(self):
        job = self.generate_ai()
        plan = copy.deepcopy(job['editorial']['card_plan'])
        core.review(job['id'], {'action': 'approve', 'confirm_cards': True})
        edited = core.update_content(job['id'], {'wechat_markdown': job['wechat_markdown'] + '\n人工补充的条件性说明。'})
        self.assertEqual(edited['editorial']['card_plan'], plan)
        self.assertTrue(edited['editorial']['analysis_stale'])
        self.assertEqual(edited['status'], 'review_required')
        with self.assertRaises(ValueError):
            core.review(job['id'], {'action': 'approve'})

    def test_card_claims_block_approval_until_corrected(self):
        job = self.generate_ai()
        baseline = copy.deepcopy(job['editorial']['card_plan'])
        for field in ('title', 'body'):
            for phrase in ('保证收益', '建议买入', '建议卖出', '稳赚'):
                with self.subTest(field=field, phrase=phrase):
                    plan = copy.deepcopy(baseline)
                    plan[0][field] = phrase
                    edited = core.update_content(job['id'], {'card_plan': plan})
                    self.assertTrue(any(item['level'] == 'error' for item in edited['checks']))
                    with self.assertRaisesRegex(ValueError, '阻断项'):
                        core.review(job['id'], {'action': 'approve', 'confirm_cards': True})
                    corrected = core.update_content(job['id'], {'card_plan': baseline})
                    self.assertFalse(any(item['level'] == 'error' for item in corrected['checks']))
                    self.assertEqual(core.review(job['id'], {'action': 'approve', 'confirm_cards': True})['status'], 'approved')

    def test_generated_card_claim_is_screened_before_manual_approval(self):
        self.configure_ai()
        self.editorial_fixture.second['card_plan'][0]['body'] = '保证收益'
        job = core.get(core.start_job()['id'])
        self.assertEqual(job['status'], 'review_required')
        self.assertTrue(job['content_ready'])
        self.assertTrue(any(item['level'] == 'error' for item in job['checks']))
        with self.assertRaisesRegex(ValueError, '阻断项'):
            core.review(job['id'], {'action': 'approve', 'confirm_cards': True})

    def test_card_source_ids_and_shape_are_validated_atomically(self):
        job = self.generate_ai()
        for refs in ([], ['S999'], ['https://invented.example/source'], [None], 'S1'):
            plan = copy.deepcopy(job['editorial']['card_plan'])
            plan[0]['source_ids'] = refs
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                core.update_content(job['id'], {'card_plan': plan})
            self.assertEqual(core.get(job['id']), job)
        for plan in ([], job['editorial']['card_plan'][:3], None):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                core.update_content(job['id'], {'card_plan': plan})
            self.assertEqual(core.get(job['id']), job)

    def test_manual_platform_and_card_length_boundaries(self):
        job = self.generate_ai()
        for field, limit in (('xhs_title', 20), ('xhs_text', 1000)):
            before = core.get(job['id'])
            with self.subTest(field=field), self.assertRaises(ValueError):
                core.update_content(job['id'], {field: '甲' * (limit + 1)})
            self.assertEqual(core.get(job['id']), before)
            edited = core.update_content(job['id'], {field: '甲' * limit})
            self.assertEqual(len(edited[field]), limit)
        for field, limit in (('title', 28), ('body', 240)):
            before = core.get(job['id'])
            plan = copy.deepcopy(before['editorial']['card_plan'])
            plan[0][field] = '卡' * (limit + 1)
            with self.subTest(field=field), self.assertRaises(ValueError):
                core.update_content(job['id'], {'card_plan': plan})
            self.assertEqual(core.get(job['id']), before)
            plan[0][field] = '卡' * limit
            edited = core.update_content(job['id'], {'card_plan': plan})
            self.assertEqual(len(edited['editorial']['card_plan'][0][field]), limit)

    def test_approved_images_download_checks_text_and_png_digest(self):
        job = self.generate_ai()
        core.review(job['id'], {'action': 'approve', 'confirm_cards': True})
        self.assertEqual(self.api('GET', f'/api/jobs/{job["id"]}/images')[0], 200)
        path = self.data / 'artifacts' / job['id'] / 'card-1.png'
        path.write_bytes(b'tampered image fixture')
        self.assertEqual(self.api('GET', f'/api/jobs/{job["id"]}/images')[0], 400)
        path.write_bytes(PNG)
        changed = core.get(job['id'])
        changed['xhs_text'] += '未审核的文字'
        core.put(changed)
        self.assertEqual(self.api('GET', f'/api/jobs/{job["id"]}/images')[0], 400)
        with self.assertRaises(ValueError):
            core.export_zip(job['id'])

    def test_images_zip_locks_snapshot_against_concurrent_edit(self):
        job = self.generate_ai()
        started, release, edited, finished = (threading.Event() for _ in range(4))
        result = {}
        real_write = zipfile.ZipFile.write
        def paused_write(archive, file, *args, **kwargs):
            if not started.is_set():
                started.set()
                if not release.wait(3):
                    raise AssertionError('fixture release timed out')
            return real_write(archive, file, *args, **kwargs)
        def export():
            try:
                result['zip'] = core.images_zip(job['id'])
            except Exception as exc:
                result['error'] = exc
            finally:
                finished.set()
        def edit():
            try:
                core.update_content(job['id'], {'wechat_title': '人工修订标题'})
            finally:
                edited.set()
        with patch.object(zipfile.ZipFile, 'write', paused_write):
            exporter = threading.Thread(target=export)
            editor = threading.Thread(target=edit)
            exporter.start()
            try:
                self.assertTrue(started.wait(2))
                editor.start()
                self.assertFalse(edited.wait(0.05))
            finally:
                release.set()
                exporter.join(3)
                if editor.ident is not None:
                    editor.join(3)
        self.assertTrue(finished.is_set())
        self.assertNotIn('error', result)
        self.assertTrue(edited.is_set())
        with zipfile.ZipFile(io.BytesIO(result['zip'])) as archive:
            self.assertEqual(archive.read('01.png'), PNG)
