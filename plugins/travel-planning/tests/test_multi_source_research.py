from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'skills' / 'travel-planning'


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render = load_module('render_itinerary')
audit = load_module('audit_itinerary')
assembly = load_module('assemble_itinerary')
workspace = load_module('research_workspace')
evidence = render.research_evidence


def web_only_plan():
    """Synthetic acceptance fixture; no claims about actual establishments."""
    data = json.loads((ROOT / 'assets' / 'example-itinerary.json').read_text())
    planning = data['planning']
    planning['restaurant_research_version'] = 4
    def clean(value):
        if isinstance(value, dict):
            for key in list(value):
                if key in {'coordinates', 'coordinate_system', 'poi_id', 'poi_provider', 'poi_source_id', 'poi_verified_at', 'map_route'}:
                    del value[key]
                else:
                    value[key] = clean(value[key])
        elif isinstance(value, list):
            value = [clean(item) for item in value]
        elif isinstance(value, str):
            value = value.replace('高德', '公开资料').replace('小红书', '公开体验')
            if value.startswith('https://uri.amap.com/') or value.startswith('https://www.xiaohongshu.com/') or value.startswith('https://lbs.amap.com/'):
                value = 'https://fixture.invalid/original/' + str(abs(hash(value)))
        return value
    data = clean(data)
    planning = data['planning']
    for source in data['sources']:
        source.update(acquisition='web_search', accessed='full', publisher='测试资料发布者')
        if source['id'] == 's-restaurant-platform':
            source['kind'] = 'merchant'
        elif source['id'] == 's-restaurant-map':
            source['kind'] = 'travel_guide'
        else:
            source.setdefault('kind', 'official')
        source.pop('provider', None)
        source.pop('provider_poi_ids', None)
    for restaurant in planning['restaurants']:
        restaurant['location']['verification'] = {'checked_at': '2026-09-20', 'source_ids': ['s-restaurant-platform']}
    def item(aspect, sid='s-restaurant-platform', **extra):
        return {'aspect': aspect, 'summary': f'测试{aspect}证据', 'status': 'verified',
                'source_ids': [sid], 'checked_at': '2026-09-20', **extra}
    for snapshot in planning['restaurant_snapshots']:
        snapshot['schema_version'] = 'restaurant-source-snapshot/v2'
        snapshot.pop('community_consensus', None)
        snapshot.pop('platform_signals', None)
        snapshot['evidence'] = [item(k) for k in ['identity', 'menu', 'operations']]
        snapshot['evidence'].append(item('experience', 's-restaurant-community', status='community_reported'))
    def estimated(route, minutes=None):
        n = minutes if minutes is not None else route['door_to_door_minutes']
        route.update(status='estimated', basis='测试公开路线指引，耗时仅为保守估算',
                     duration_range_minutes=[max(n - 4, 0), n - 2], buffer_minutes=2,
                     door_to_door_minutes=n, checked_at='2026-09-20', source_ids=['s-restaurant-map'])
        route.pop('distance_meters', None)
        route.pop('duration_minutes', None)
    for baseline in planning['meal_baseline_routes']:
        estimated(baseline)
    for evaluation in planning['meal_route_evaluations']:
        for key in ['from_previous', 'to_next']:
            estimated(evaluation[key])
    for route in planning['transport_edges']:
        estimated(route, 10)
    decisions = []
    for attraction in planning['attractions']:
        decisions.append({'target_id': attraction['id'], 'user_value': '符合测试用户文化游偏好',
                          'tradeoff': '减少无关购物停留', 'evidence': [item(k) for k in ['identity', 'admission', 'experience']]})
    for meal in planning['meal_options']:
        decisions.append({'target_id': meal['selected_candidate_id'], 'user_value': '特色菜适配测试偏好',
                          'tradeoff': '少量绕行换取正餐与备选', 'evidence': [item('experience', 's-restaurant-community', status='community_reported')]})
    planning['research_contract'] = {'version': 1,
        'capabilities': {'web_search': 'ready', 'xiaohongshu': 'unavailable', 'amap': 'unavailable'},
        'queries': [{'channel': 'web_search', 'scope': scope, 'query': f'测试目的地{scope}公开研究',
                     'checked_at': '2026-09-20', 'status': 'success', 'source_ids': ['s-restaurant-platform']} for scope in ['play', 'eat']],
        'decisions': decisions}
    return data


class MultiSourceResearchTest(unittest.TestCase):
    def setUp(self):
        self.data = web_only_plan()
        self.contract = self.data['planning']['research_contract']

    def enable_query_design(self):
        self.contract['query_design_version'] = 1
        for index, query in enumerate(self.contract['queries']):
            query.update(id=f'web-discovery-{index}', stage='discovery',
                         purpose=f"发现测试目的地的{query['scope']}选择", provider='web',
                         tool='web.search', parameters={'q': query['query']},
                         outcome='已读来源提供候选；按适配与执行条件进一步核验')

    def test_new_queries_preserve_inputs_and_legacy_contract_stays_readable(self):
        self.assertEqual(evidence.audit_research(self.data)[0], [])
        self.enable_query_design()
        self.assertEqual(evidence.audit_research(self.data)[0], [])
        query = self.contract['queries'][0]
        query['query'] += ' 另一条查询'
        self.assertTrue(any('实际参数不一致' in b for b in evidence.audit_research(self.data)[0]))

    def test_new_queries_require_single_call_metadata(self):
        self.enable_query_design()
        for field in ['id', 'stage', 'purpose', 'provider', 'tool', 'parameters', 'outcome']:
            with self.subTest(field=field):
                query = self.contract['queries'][0]
                value = query.pop(field)
                self.assertTrue(evidence.audit_research(self.data)[0])
                query[field] = value
        self.contract['queries'][1]['id'] = self.contract['queries'][0]['id']
        self.assertTrue(any('ID 不能重复' in b for b in evidence.audit_research(self.data)[0]))

    def test_successful_discovery_is_distinct_from_reading_evidence(self):
        self.enable_query_design()
        query = self.contract['queries'][0]
        query['source_ids'] = []
        query['result_count'] = 20
        query['outcome'] = '搜索返回候选，尚未读取；下一步挑相关原文核读'
        self.assertEqual(evidence.audit_research(self.data)[0], [])
        query['stage'] = 'detail'
        self.assertTrue(any('实际读过的来源' in b for b in evidence.audit_research(self.data)[0]))

    def test_mcp_source_cannot_hide_in_mixed_web_sources(self):
        self.enable_query_design()
        source = {**self.data['sources'][0], 'id': 'xhs-note', 'acquisition': 'mcp',
                  'provider': '小红书', 'url': 'https://www.xiaohongshu.com/explore/fixture',
                  'origin_url': 'https://www.xiaohongshu.com/explore/fixture'}
        self.data['sources'].append(source)
        query = self.contract['queries'][0]
        query['source_ids'].append('xhs-note')
        blocking = evidence.audit_research(self.data)[0]
        self.assertTrue(any('获取渠道不一致' in b for b in blocking))
        self.assertTrue(any('缺少对应 xiaohongshu' in b for b in blocking))
        query['source_ids'].remove('xhs-note')
        self.contract['queries'].append({**query, 'id': 'xhs-detail', 'stage': 'detail',
            'channel': 'mcp', 'provider': 'xiaohongshu', 'tool': 'get_feed_detail',
            'query': 'fixture', 'parameters': {'feed_id': 'fixture'}, 'source_ids': ['xhs-note']})
        self.assertEqual(evidence.audit_research(self.data)[0], [])
        self.contract['queries'][-1]['provider'] = 'amap'
        self.assertTrue(any('平台归属不一致' in b for b in evidence.audit_research(self.data)[0]))

    def test_sensitive_parameters_are_rejected_without_echoing_values(self):
        self.enable_query_design()
        self.contract['queries'][0]['parameters']['nested'] = [{'xsecToken': 'test-sensitive-value'}]
        blocking = evidence.audit_research(self.data)[0]
        self.assertTrue(any('敏感字段' in b for b in blocking))
        self.assertNotIn('test-sensitive-value', str(blocking))

    def test_followup_and_result_counts_are_validated(self):
        self.enable_query_design()
        query = self.contract['queries'][1]
        query['parent_query_id'] = self.contract['queries'][0]['id']
        self.assertEqual(evidence.audit_research(self.data)[0], [])
        query['parent_query_id'] = 'missing-query'
        query['result_count'] = -1
        blocking = evidence.audit_research(self.data)[0]
        self.assertTrue(any('补查引用无效' in b for b in blocking))
        self.assertTrue(any('非负整数' in b for b in blocking))

    def test_new_workspace_cannot_drop_query_trace_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace.init_workspace(Namespace(root=str(root), trip_id='query-fixture', destination='测试目的地',
                date_range='2026-10-15 至 2026-10-19', travelers='2', origin='测试出发地',
                budget='5000', preferences=[], constraints=[], brief_file=None))
            trip_root = root / 'query-fixture'
            manifest = json.loads((trip_root / 'manifest.json').read_text())
            context = json.loads((trip_root / 'route-context.json').read_text())
            self.assertEqual(manifest['query_design_version'], 1)
            self.assertEqual(context['research_contract']['query_design_version'], 1)
            with self.assertRaisesRegex(assembly.AssemblyError, '逐次真实查询'):
                assembly.validate_plan(trip_root, {'schema_version': 'itinerary-plan/v1', 'trip': {},
                    'workflow': {}, 'collections': {}, 'days': [], 'planning': {'research_contract': self.contract}})

    def test_web_only_plan_renders_without_helper_fields(self):
        render.validate_data(self.data)
        html = render.build(self.data)
        self.assertIn('推荐与核验依据', html)
        self.assertIn('估算含缓冲', html)
        self.assertIn('示例餐厅平台详情', html)
        self.assertNotIn('在高德打开地点', html)
        self.assertEqual(evidence.audit_research(self.data)[0], [])

    def test_both_helpers_ready_still_require_web_search(self):
        self.contract['capabilities'].update(xiaohongshu='ready', amap='ready')
        self.assertEqual(evidence.audit_research(self.data)[0], [])
        self.contract['queries'] = []
        with self.assertRaisesRegex(ValueError, '不能仅使用小红书和高德'):
            render.validate_data(self.data)

    def test_web_query_must_cover_food_and_play(self):
        self.contract['queries'] = self.contract['queries'][:1]
        self.assertTrue(any('玩什么与吃什么' in b for b in evidence.audit_research(self.data)[0]))

    def test_snippets_cannot_support_adopted_recommendations(self):
        self.data['sources'][0]['accessed'] = 'snippet'
        with self.assertRaisesRegex(ValueError, '搜索摘要'):
            render.validate_data(self.data)

    def test_same_original_via_multiple_channels_is_not_consensus(self):
        original = next(s for s in self.data['sources'] if s['id'] == 's-restaurant-community')
        copy = {**original, 'id': 'copied-note', 'acquisition': 'mcp', 'independence_key': 'another-key'}
        self.data['sources'].append(copy)
        decision = self.contract['decisions'][-1]
        decision['consensus'] = True
        decision['evidence'][0]['source_ids'].append('copied-note')
        self.assertTrue(any('独立原始证据' in b for b in evidence.audit_research(self.data)[0]))

    def test_identity_sources_do_not_count_as_experience_consensus(self):
        decision = self.contract['decisions'][0]
        decision['consensus'] = True
        decision['evidence'][0]['source_ids'] = ['s-restaurant-community']
        self.assertTrue(any('独立原始证据' in b for b in evidence.audit_research(self.data)[0]))

    def test_conflict_requires_resolution_and_critical_unknown_blocks(self):
        decision = self.contract['decisions'][0]
        decision['conflicts'] = ['测试资料开放时间冲突']
        self.assertTrue(any('裁决' in b for b in evidence.audit_research(self.data)[0]))
        decision['resolution'] = '以运营方适用日期公告为准'
        decision['evidence'][1].update(status='partial', reason='测试营业未确认', critical=True)
        self.assertTrue(any('关键证据未解决' in b for b in evidence.audit_research(self.data)[0]))

    def test_estimates_cannot_remove_buffer_or_be_called_verified(self):
        baseline = self.data['planning']['meal_baseline_routes'][0]
        baseline['buffer_minutes'] = 100
        with self.assertRaisesRegex(ValueError, '上界加缓冲'):
            render.validate_data(self.data)
        baseline['buffer_minutes'] = 2
        baseline['status'] = 'verified'
        with self.assertRaisesRegex(ValueError, '地图或运营方'):
            render.validate_data(self.data)

    def test_nonfinite_route_numbers_are_rejected(self):
        self.data['planning']['meal_route_evaluations'][0]['detour_minutes'] = float('nan')
        with self.assertRaisesRegex(ValueError, '总耗时和绕行必须是数值'):
            render.validate_data(self.data)

    def test_metadata_survives_archive_and_assembly(self):
        source = {**self.data['sources'][0], 'task_id': 'main', 'freshness': 'dynamic'}
        record = workspace.source_evidence_record(source)
        self.assertEqual(record['acquisition'], 'web_search')
        self.assertEqual(record['accessed'], 'full')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'sources').mkdir()
            (root / 'sources' / 'main.jsonl').write_text(json.dumps(source))
            sources = assembly.collect_sources(root)
            evidence.validate_source(sources[0])
            self.assertEqual(sources[0]['publisher'], source['publisher'])

    def test_no_poi_discovery_accepts_real_address_evidence(self):
        planning = self.data['planning']
        workspace.validate_restaurant_poi_sources({'stage': 'restaurant_discovery'},
            {'status': 'complete', 'restaurant_research_version': 4, 'entities': planning}, self.data['sources'])
        planning['restaurants'][0]['location']['verification']['source_ids'] = ['s-restaurant-community']
        with self.assertRaises(workspace.WorkspaceError):
            workspace.validate_restaurant_poi_sources({'stage': 'restaurant_discovery'},
                {'status': 'complete', 'entities': planning}, self.data['sources'])

    def test_web_unavailable_requires_explanation_and_does_not_fake_queries(self):
        self.contract['capabilities']['web_search'] = 'unavailable'
        self.contract['queries'] = []
        self.assertTrue(evidence.audit_research(self.data)[0])
        self.contract['web_unavailable_reason'] = '测试宿主未提供联网能力；采用此前归档公开资料'
        blocking, warnings = evidence.audit_research(self.data)
        self.assertEqual(blocking, [])
        self.assertTrue(warnings)

    def test_new_workspace_cannot_drop_research_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'manifest.json').write_text(json.dumps({'research_contract_version': 1}))
            with self.assertRaisesRegex(assembly.AssemblyError, '不能退回旧契约'):
                assembly.validate_plan(root, {'schema_version': 'itinerary-plan/v1', 'trip': {}, 'workflow': {}, 'collections': {}, 'days': []})

    def test_web_only_research_assembly_audit_and_html(self):
        self.enable_query_design()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ['sources', 'state', 'results', 'snapshots']:
                (root / folder).mkdir()
            (root / 'manifest.json').write_text(json.dumps({'research_contract_version': 1}))
            route_id = self.data['workflow']['selected_route_id']
            (root / 'selected-route.json').write_text(json.dumps({'id': route_id}))
            research_path = root / 'state' / 'research.json'
            research_path.write_text(json.dumps({'tasks': []}))
            (root / 'sources' / 'main.jsonl').write_text('\n'.join(json.dumps(s) for s in self.data['sources']))
            (root / 'results' / 'play.json').write_text(json.dumps({'entities': {'attractions': self.data['planning']['attractions']}}))
            plan = {'schema_version': 'itinerary-plan/v1', 'trip': self.data['trip'], 'workflow': self.data['workflow'],
                    'collections': {'attractions': {'task': 'play', 'path': 'entities.attractions'}},
                    'research_state_sha256': hashlib.sha256(research_path.read_bytes()).hexdigest(),
                    'planning': self.data['planning'], 'generate_attraction_booking_tasks': False,
                    'days': self.data['days'], 'attraction_events': {}}
            for day in plan['days']:
                for event in day['events']:
                    if event['type'] == 'attraction':
                        plan['attraction_events'][event['attraction_id']] = {
                            'event_id': event['id'], 'title': event['title'], 'start': event['time'],
                            'end': event['end_time'], 'weather_id': event['weather_id']}
                        event['overrides'] = {key: value for key, value in event.items()}
            path = root / 'state' / 'itinerary-plan.json'
            path.write_text(json.dumps(plan))
            result = assembly.assemble(root, path)
            self.assertEqual(result['planning']['research_contract'], self.contract)
            self.assertEqual(audit.audit(result)['blocking'], [])
            html = render.build(result)
            self.assertIn('推荐与核验依据', html)
            self.assertIn('估算含缓冲', html)

    def test_missing_operations_cannot_pass_by_omitting_critical_flag(self):
        snapshot = self.data['planning']['restaurant_snapshots'][0]
        snapshot['evidence'][2].update(status='partial', reason='测试没有营业依据')
        with self.assertRaisesRegex(ValueError, '营业适用性'):
            render.validate_data(self.data)

    def test_user_arranged_exemption_requires_confirmation_archive(self):
        day = self.data['days'][0]
        day['events'].append({'id': 'late-rest', 'type': 'rest', 'title': '测试晚间休息', 'time': '18:00', 'end_time': '20:00'})
        exemption = {'kind': 'user_arranged', 'reason': '用户另有安排', 'plan': '家庭晚餐', 'recheck_at': '2026-10-17', 'source_ids': ['s-restaurant-platform']}
        day['meal_exemptions'] = {'晚餐': exemption}
        self.assertTrue(any('豁免必须' in b for b in audit.audit(self.data)['blocking']))
        exemption['user_confirmation'] = {'archive_ref': 'evidence/main/user-dinner.json', 'quoted_instruction': '晚餐我已安排家庭聚餐', 'confirmed_at': '2026-09-20'}
        self.assertFalse(any('豁免必须' in b for b in audit.audit(self.data)['blocking']))

    def test_malformed_contract_reports_failure(self):
        self.contract['decisions'] = ['bad']
        self.assertTrue(evidence.audit_research(self.data)[0])

    def test_free_text_meal_exemption_is_rejected(self):
        day = self.data['days'][0]
        day['events'].append({'id': 'late-rest', 'type': 'rest', 'title': '测试晚间休息', 'time': '18:00', 'end_time': '20:00'})
        day['meal_exemptions'] = {'晚餐': '附近自选'}
        self.assertTrue(any('豁免必须' in b for b in audit.audit(self.data)['blocking']))


if __name__ == '__main__':
    unittest.main()
