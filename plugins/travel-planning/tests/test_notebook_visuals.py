from __future__ import annotations

import copy
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/travel-planning'
SPEC = importlib.util.spec_from_file_location('notebook_renderer', SKILL / 'scripts/render_itinerary.py')
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


class NotebookVisualTest(unittest.TestCase):
    def example(self):
        return json.loads((SKILL / 'assets/example-itinerary.json').read_text())

    def asset(self, **extra):
        return {'src': 'data:image/jpeg;base64,/9j/2Q==', 'alt': '水彩示意',
                'credit': 'AI 生成水彩插画 · 非景点实景', 'aspect_ratio': 1.5, **extra}

    def test_reused_raster_embedded_once_and_input_untouched(self):
        data = self.example()
        event = data['days'][0]['events'][1]
        visuals = {'cover': self.asset(), 'experiences': {event['id']: self.asset()},
                   'days': {day['date']: self.asset() for day in data['days']}}
        before_data, before_visuals = copy.deepcopy(data), copy.deepcopy(visuals)
        page = renderer.build(data, visuals)
        self.assertEqual(page.count('data:image/jpeg;base64,/9j/2Q=='), 1)
        symbols = set(re.findall(r'<symbol id="([^"]+)"', page))
        references = re.findall(r'<use href="#([^"]+)"', page)
        self.assertGreater(len(references), 5)
        self.assertTrue(set(references) <= symbols)
        self.assertLess(page.index('<symbol'), page.index('<main'))
        self.assertEqual(data, before_data)
        self.assertEqual(visuals, before_visuals)

    def test_event_id_binding_survives_title_edit(self):
        data = self.example()
        event = data['days'][0]['events'][1]
        visuals = {'experiences': {event['id']: self.asset(alt='稳定绑定的场景')}}
        event['title'] = '调整过的景点标题'
        page = renderer.build(data, visuals)
        self.assertIn('稳定绑定的场景', page)
        self.assertIn('调整过的景点标题', page)
        self.assertIn('class="day-postcard"', page)

    def test_two_and_seven_days_keep_destination_and_all_day_links(self):
        for count in (2, 7):
            data = self.example()
            data['trip']['destination'] = '测试目的地'
            if count > len(data['days']):
                data['days'].extend({'date': f'2026-10-{19 + i:02d}', 'theme': f'测试日期{i}', 'events': []}
                                    for i in range(count - len(data['days'])))
            for day in data['days'][2:]:
                data['planning']['daily_routes'].append({'date': day['date'], 'stops': [{'name': '起点'}, {'name': '终点'}]})
            page = renderer.build(data)
            self.assertIn(f'{count}天，一页页走过', page)
            self.assertIn('测试目的地', page)
            self.assertNotIn('宜昌', page)
            for index in range(count):
                self.assertIn(f'data-day-target="day-{index}"', page)
            self.assertEqual('route-many' in page.split('<main', 1)[1], count > 5)

    def test_sprite_crop_uses_original_coordinates_and_one_raster(self):
        assets, library = renderer.prepare_artwork({'food_items': {
            'A': self.asset(crop={'columns': 2, 'rows': 2, 'index': 0}),
            'B': self.asset(crop={'columns': 2, 'rows': 2, 'index': 3})}})
        self.assertEqual(library.count('<image '), 1)
        self.assertIn('viewBox="75 50 75 50"', renderer.render_visual(assets['food_items']['B'], 'sticker-picture'))
        self.assertIn('非景点实景', renderer.render_visual(assets['food_items']['B'], 'sticker-picture'))

    def test_unbound_food_art_does_not_invent_food(self):
        page = renderer.render_trip_focus([{'date': '2026-10-12', 'events': []}], {}, {}, {},
                                          {'food_items': {'无证据的菜': self.asset()}, 'food': self.asset()})
        self.assertNotIn('无证据的菜', page)
        self.assertNotIn('food-collection-media', page)
        self.assertIn('当前未列出餐饮线索', page)

    def test_food_candidate_caveat_is_preserved(self):
        name = '地方小吃（摊位待现场确认）'
        day = {'date': '2026-10-12', 'events': [{'type': 'food_walk', 'food_walk': {'food_items': [{'name': name}]}}]}
        page = renderer.render_trip_focus([day], {}, {}, {}, {'food_items': {name: self.asset()}})
        self.assertIn(name, page)
        self.assertIn('sticker-picture', page)
        self.assertIn('data-day-target="day-0"', page)

    def test_manifest_rejects_invalid_crop_and_ratio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'art.jpg').write_bytes(b'\xff\xd8\xff\xd9')
            manifest = root / 'manifest.json'
            for extra in ({'crop': {'columns': 2, 'rows': 2, 'index': 4}},
                          {'crop': {'columns': True, 'rows': 1, 'index': 0}},
                          {'aspect_ratio': 0}, {'aspect_ratio': '1.5'}):
                manifest.write_text(json.dumps({'cover': {'path': 'art.jpg', 'alt': '图', 'credit': 'AI 图', **extra}}))
                with self.assertRaises(ValueError):
                    renderer.load_visual_manifest(manifest)

    def test_route_image_source_does_not_create_nested_links(self):
        image = {'url': 'https://example.com/photo.jpg', 'source_url': 'https://example.com/source',
                 'alt': '授权图', 'source_label': '作者', 'author': '作者', 'license': 'CC BY 4.0'}
        day = {'date': '2026-10-12', 'events': [{'id': 'scene', 'type': 'attraction', 'title': '景点', 'images': [image]}]}
        page = renderer.render_notebook_route([day], {}, {}, {})
        self.assertEqual(page.count('<a '), 1)
        self.assertIn('https://example.com/photo.jpg', page)
        self.assertIn('CC BY 4.0', page)


if __name__ == '__main__':
    unittest.main()
