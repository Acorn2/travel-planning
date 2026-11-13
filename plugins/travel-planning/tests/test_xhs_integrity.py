from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

import test_xhs_comments as fixtures
import test_xhs_optional as optional
import test_xhs_sampling as sampling

s = fixtures.sources
c = fixtures.contract


class XhsIntegrityTest(unittest.TestCase):
    def capture(self, root, raw=None):
        return fixtures.XhsCommentsTest().capture(root, raw=raw)

    def decision(self, capture):
        return fixtures.XhsCommentsTest().reviewed(capture)

    def test_excluded_detail_keeps_trace_without_adopting_source_or_quota(self):
        case = optional.XhsOptionalSourceTest(); case.setUp()
        self.addCleanup(case.doCleanups)
        _, captured = self.capture(case.root)
        case.fixture.decisions = [self.decision(captured)]
        s.execution_log.write_json(case.root / 'state/itinerary-plan.json', {'planning': {'xhs_comment_reviews': case.fixture.decisions}})
        data = case.web_data()
        research = data['planning']['research_contract']
        research['capabilities']['xiaohongshu'] = 'ready'
        research['query_design_version'] = 1
        for i, q in enumerate(research['queries']):
            q.update(id=f'web-{i}', stage='discovery', purpose='网页发现', provider='web',
                     tool='web.search', parameters={'q': q['query']}, outcome='已读原文')
        query = {'id': 'discarded-detail', 'stage': 'detail', 'purpose': '核读候选', 'channel': 'mcp',
                 'scope': 'eat', 'query': '测试笔记', 'provider': 'xiaohongshu', 'tool': 'get_feed_detail',
                 'parameters': {'feed_id': 'note1'}, 'status': 'success',
                 'checked_at': '2026-11-13', 'source_ids': [], 'outcome': '已读但不适用，本次排除'}
        research['queries'].append(query)
        optional.multi.render.validate_data(data, case.root)
        self.assertEqual(optional.multi.audit.audit(data, case.root)['blocking'], [])
        self.assertFalse(s.xhs_search_contract.xhs_is_cited(data))
        for change in ({'parameters': {'feed_id': 'not-captured'}}, {'provider': 'amap'},
                       {'tool': 'search_feeds'}, {'outcome': ''}):
            bad = deepcopy(data)
            bad['planning']['research_contract']['queries'][-1].update(change)
            self.assertTrue(optional.multi.evidence.audit_research(bad, case.root)[0], change)

    def test_legacy_required_page_cannot_skip_city_quota(self):
        case = sampling.XhsRouteSamplingTest(); case.setUp()
        self.addCleanup(case.doCleanups)
        case.finish_city('成都')
        data = case.data(); data['planning']['xhs_comment_research_version'] = 2
        s.execution_log.write_json(case.root / 'artifacts/itinerary.json', data)
        html = '<meta name="xhs-review-digest" content="' + c.review_digest(data) + '">'
        with self.assertRaisesRegex(ValueError, '重庆'):
            c.validate_page_evidence(case.root / 'artifacts/itinerary.html', html)

    def test_body_and_each_image_need_explicit_coverage(self):
        raw = fixtures.upstream_response()
        raw['data']['note']['desc'] = '正文。' * 3000 + '末尾限制：禁止夜间进入'
        raw['data']['note']['imageList'] = [{'urlDefault': 'https://sns.xhscdn.com/a'}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, capture = self.capture(root, raw)
            decision = self.decision(capture)
            decision.pop('content_review', None)
            review = c.load_reviews(root, [decision])[0]
            self.assertFalse(c.review_is_complete(review))
            decision['content_review'] = {
                'body_ranges': [[0, 4000]], 'images': [], 'reason': '只读了首段'}
            self.assertFalse(c.review_is_complete(c.load_reviews(root, [decision])[0]))
            decision = self.decision(capture)
            self.assertTrue(c.review_is_complete(c.load_reviews(root, [decision])[0]))
            decision['content_review']['images'] = []
            self.assertFalse(c.review_is_complete(c.load_reviews(root, [decision])[0]))

    def test_large_thread_and_long_comment_are_bounded_and_lossless(self):
        raw = fixtures.upstream_response()
        root_comment = raw['data']['comments']['list'][0]
        root_comment['content'] = '根评论上下文。' * 1500 + '根评论尾部限制'
        root_comment['subComments'] = [{'id': f'r{i}', 'content': '长回复。' * 250 + f'尾部纠正{i}'} for i in range(49)]
        root_comment['subCommentCount'] = '49'
        raw['data']['comments']['list'] = [root_comment]
        expected = {'c1': root_comment['content'], **{r['id']: r['content'] for r in root_comment['subComments']}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.capture(root, raw)
            parts = {key: '' for key in expected}
            cursor = None
            for _ in range(300):
                packet = c.review_packet(root, limit=1, cursor=cursor)
                self.assertLessEqual(len(json.dumps(packet, ensure_ascii=False, indent=2).encode()) + 1, c.MAX_PACKET_BYTES)
                for thread in packet['review_threads']:
                    for comment in thread['comments']:
                        parts[comment['comment_id']] += comment['content'] or ''
                cursor = packet['next_cursor']
                if cursor is None:
                    break
            else:
                self.fail('评论分页未终止')
            self.assertEqual(parts, expected)

    def test_unknown_text_and_image_shapes_fail_without_fake_empty_content(self):
        changes = [('body', {'text': '正文限制'}), ('images', {'items': []}), ('images', [None]),
                   ('comment', {'text': '一级评论'}), ('reply', {'text': '回复纠正'})]
        for field, value in changes:
            raw = fixtures.upstream_response()
            if field == 'body': raw['data']['note']['desc'] = value
            elif field == 'images': raw['data']['note']['imageList'] = value
            elif field == 'comment': raw['data']['comments']['list'][0]['content'] = value
            else: raw['data']['comments']['list'][0]['subComments'][0]['content'] = value
            with self.subTest(field=field), self.assertRaises(s.SourceError):
                fixtures.XhsCommentsTest().detail(raw)

    def test_comment_fragment_coverage_cannot_be_replaced_by_thread_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); _, capture = self.capture(root)
            decision = self.decision(capture)
            decision['content_review']['comment_ranges']['c1'] = [[0, 1]]
            review = c.load_reviews(root, [decision])[0]
            self.assertTrue(any('评论文本分片' in issue for issue in c.review_issues(review)))

    def test_body_ranges_must_cover_tail_without_holes_or_invalid_types(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); _, capture = self.capture(root)
            length = capture['content_manifest']['body_characters']
            for ranges in ([[0, length - 1]], [[1, length]], [[0, 1], [2, length]],
                           [[False, length]], [[0, length + 1]], None):
                decision = self.decision(capture); decision['content_review']['body_ranges'] = ranges
                with self.subTest(ranges=ranges):
                    self.assertFalse(c.review_is_complete(c.load_reviews(root, [decision])[0]))
            decision = self.decision(capture)
            decision['content_review']['body_ranges'] = [[length // 2, length], [0, length // 2]]
            self.assertTrue(c.review_is_complete(c.load_reviews(root, [decision])[0]))

    def test_image_only_note_cannot_skip_all_images(self):
        raw = fixtures.upstream_response(); raw['data']['note']['desc'] = ''
        raw['data']['note']['imageList'] = [{'urlDefault': 'https://sns.xhscdn.com/a'}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); _, capture = self.capture(root, raw)
            for status in ('not_needed', 'unavailable'):
                decision = self.decision(capture)
                decision['content_review']['images'][0].update(status=status, reason='未读图片')
                self.assertFalse(c.review_is_complete(c.load_reviews(root, [decision])[0]))

    def test_image_review_requires_summary_and_original_manifest(self):
        raw = fixtures.upstream_response()
        raw['data']['note']['imageList'] = [{'urlDefault': 'https://sns.xhscdn.com/a'}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); _, capture = self.capture(root, raw)
            for change in ('summary', 'digest', 'index'):
                decision = self.decision(capture)
                content = decision['content_review']
                if change == 'summary': content['images'][0]['summary'] = ''
                elif change == 'digest': content['image_manifest_digest'] = 'stale'
                else: content['images'][0]['image_index'] = 2
                with self.subTest(change=change):
                    self.assertFalse(c.review_is_complete(c.load_reviews(root, [decision])[0]))

    def test_malformed_image_decision_is_reported_instead_of_crashing(self):
        raw = fixtures.upstream_response()
        raw['data']['note']['imageList'] = [{'urlDefault': 'https://sns.xhscdn.com/a'}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); _, capture = self.capture(root, raw)
            for status in ({'new_status': True}, None, False):
                decision = self.decision(capture)
                decision['content_review']['images'][0]['status'] = status
                with self.subTest(status=status):
                    self.assertFalse(c.review_is_complete(c.load_reviews(root, [decision])[0]))

    def test_changed_content_decision_invalidates_assembled_json(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); _, capture = self.capture(root)
            decision = self.decision(capture)
            s.execution_log.write_json(root / 'state/itinerary-plan.json', {'planning': {'xhs_comment_reviews': [decision]}})
            data = {'planning': {'xhs_comment_reviews': c.load_reviews(root, [decision])}}
            self.assertEqual(c.workspace_issues(data, root), [])
            decision['content_review']['reason'] = '重新阅读后结论变化'
            s.execution_log.write_json(root / 'state/itinerary-plan.json', {'planning': {'xhs_comment_reviews': [decision]}})
            self.assertTrue(c.workspace_issues(data, root))
            data['planning']['xhs_comment_reviews'][0].pop('content_manifest')
            self.assertTrue(c.workspace_issues(data, root))

    def test_cursor_rejects_other_observation_or_out_of_range_positions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); _, capture = self.capture(root)
            for cursor in ('bad', '0' * 64 + ':0:0:0', capture['capture_id'] + ':999:0:0',
                           capture['capture_id'] + ':0:999:0', capture['capture_id'] + ':0:0:999999'):
                with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                    c.review_packet(root, cursor=cursor)

    def test_cli_resumes_reply_fragments_without_platform_request(self):
        raw = fixtures.upstream_response()
        raw['data']['comments']['list'] = [raw['data']['comments']['list'][0]]
        raw['data']['comments']['list'][0]['content'] = '长评论。' * 3000 + '关键尾部'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); s.execution_log.write_json(root / 'manifest.json', {'workspace_version': 1})
            self.capture(root, raw)
            first = c.review_packet(root)
            argv = ['research_sources.py', 'xhs-review', '--workspace', str(root), '--cursor', first['next_cursor']]
            with patch.object(sys, 'argv', argv), patch.object(s, 'output') as output, patch.object(s, 'xhs_mcp_call') as upstream:
                self.assertEqual(s.main(), 0)
            upstream.assert_not_called()
            comment = output.call_args.args[0]['review_threads'][0]['comments'][0]
            self.assertGreater(comment['content_reading']['offset'], 0)

    def test_empty_comment_metadata_also_pages_within_packet_budget(self):
        raw = fixtures.upstream_response()
        raw['data']['comments']['list'] = [raw['data']['comments']['list'][0]]
        raw['data']['comments']['list'][0]['subComments'] = [{'id': f'empty-{i}', 'content': ''} for i in range(250)]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.capture(root, raw)
            count = 0; cursor = None
            for _ in range(100):
                packet = c.review_packet(root, cursor=cursor)
                self.assertLessEqual(len(json.dumps(packet, ensure_ascii=False, indent=2).encode()) + 1, c.MAX_PACKET_BYTES)
                count += sum(len(thread['comments']) for thread in packet['review_threads'])
                cursor = packet['next_cursor']
                if cursor is None: break
            self.assertEqual(count, 251)

    def test_image_cache_mismatch_is_rejected_before_downloading(self):
        import xhs_images
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = [{'urlDefault': 'https://sns.xhscdn.com/old'}]
            xhs_images.cache_images(root, 'n', [{'urlDefault': 'https://sns.xhscdn.com/new'}])
            with patch.object(xhs_images, 'fetch_image') as download, self.assertRaisesRegex(ValueError, '不一致'):
                xhs_images.prepare_images(root, 'n', [1], expected_digest=xhs_images.images_digest(old))
            download.assert_not_called()

    def test_legacy_share_and_release_use_same_quota_gate(self):
        case = sampling.XhsRouteSamplingTest(); case.setUp()
        self.addCleanup(case.doCleanups)
        case.finish_city('成都')
        data = case.data(); data['planning']['xhs_comment_research_version'] = 2
        path = case.root / 'artifacts/itinerary.html'
        html = '<html><head><meta name="xhs-review-digest" content="' + c.review_digest(data) + '"></head></html>'
        s.execution_log.write_json(case.root / 'artifacts/itinerary.json', data); path.write_text(html)
        share = fixtures.load('prepare_share'); publisher = fixtures.load('publish_itinerary')
        with self.assertRaisesRegex(ValueError, '重庆'):
            share.prepare_share(path, case.root / 'share')
        with self.assertRaisesRegex(ValueError, '重庆'):
            publisher.prepare(path, 'test-2026-11-13', case.root / 'release')
        self.assertFalse((case.root / 'share').exists())
        self.assertFalse((case.root / 'release').exists())
        case.finish_city('重庆')
        data = case.data(); data['planning']['xhs_comment_research_version'] = 2
        s.execution_log.write_json(case.root / 'artifacts/itinerary.json', data)
        html = '<html><head><meta name="xhs-review-digest" content="' + c.review_digest(data) + '"></head></html>'
        path.write_text(html)
        self.assertTrue(share.prepare_share(path, case.root / 'share').is_file())

    def test_maximum_body_page_is_bounded_and_preserves_tail(self):
        raw = fixtures.upstream_response()
        body = '一段说明。' * 5000 + '最后：这个入口已停用'
        raw['data']['note']['desc'] = body
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.capture(root, raw)
            parts = []; offset = 0
            while True:
                packet = c.review_packet(root, body_offset=offset, body_limit=12000)
                self.assertLessEqual(len(json.dumps(packet, ensure_ascii=False, indent=2).encode()) + 1, c.MAX_PACKET_BYTES)
                parts.append(packet['note']['description'])
                offset = packet['body_reading']['next_offset']
                if offset is None: break
            self.assertEqual(''.join(parts), body)

    def test_null_and_empty_text_remain_valid_empty_observations(self):
        for value in (None, ''):
            raw = fixtures.upstream_response()
            raw['data']['note']['desc'] = value; raw['data']['note']['imageList'] = None
            raw['data']['comments']['list'][0]['content'] = value
            raw['data']['comments']['list'][0]['subComments'][0]['content'] = value
            payload, _ = fixtures.XhsCommentsTest().detail(raw)
            self.assertEqual(payload['note']['description'], '')
            self.assertEqual(payload['image_reading']['image_count'], 0)
            self.assertEqual(payload['comment_analysis']['input_count'], 4)

    def test_image_failure_is_preserved_as_a_final_audit_warning(self):
        raw = fixtures.upstream_response()
        raw['data']['note']['imageList'] = [{'urlDefault': 'https://sns.xhscdn.com/a'}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); _, capture = self.capture(root, raw)
            decision = self.decision(capture)
            decision['content_review']['images'][0].update(status='unavailable', reason='图片源不可访问')
            data = {'workflow': {'phase': 'final'}, 'planning': {'xhs_comment_research_version': 2,
                    'xhs_comment_reviews': c.load_reviews(root, [decision])}}
            blocking, warnings, _ = c.audit_reviews(data, root)
            self.assertEqual(blocking, [])
            self.assertTrue(any('第1图未读取' in value for value in warnings))

    def test_rejected_discovery_source_still_traces_targeted_search(self):
        case = optional.XhsOptionalSourceTest(); case.setUp()
        self.addCleanup(case.doCleanups)
        case.fixture.discover('成都')
        path = case.root / 'sources/nested/rejected.jsonl'; path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'id': 'discarded', 'url': 'https://www.xiaohongshu.com/explore/note1'}) + '\n')
        args = s.build_parser().parse_args(['xhs-search', '--workspace', str(case.root), '--destination', '成都',
            '--topic', 'eat', '--search-stage', 'detail', '--keyword', '具体门店评价', '--target-source-id', 'discarded'])
        design = s.xhs_search_contract.validate_search(args)
        s.xhs_search_contract.store_search(case.root, {'search_design': design,
            'query': {'keyword': args.keyword}, 'checked_at': '2026-11-13T10:00:00Z',
            'search_status': 'no_results', 'returned_count': 0, 'count': 0, 'candidate_note_ids': []})
        data = case.data(); data['planning']['research_contract'] = {'xiaohongshu_fallback_reason': '候选不适用，改用公开资料'}
        self.assertEqual(s.xhs_search_contract.audit_searches(data, case.root)[0], [])
        self.assertEqual(s.xhs_search_contract.audit_searches(data)[0], [])
        self.assertFalse(s.xhs_search_contract.xhs_is_cited(data))
        data['planning']['xhs_search_records'][-1]['target_source']['id'] = 'wrong'
        self.assertTrue(s.xhs_search_contract.audit_searches(data, case.root)[0])


if __name__ == '__main__':
    unittest.main()
