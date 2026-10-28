import unittest
from flask import Flask
from app.services.nordic import validate_nordic, has_nordic_data
from app.services.resort_json import validate_document

class NordicTests(unittest.TestCase):
    def test_optional_and_automatic_activation(self):
        with Flask(__name__).app_context():
            doc = {"schema_version": "1.0", "station": {"slug": "test", "name": "Test"}}
            self.assertNotIn("nordique", validate_document(doc)[0])
            doc["nordique"] = {"ski_area_km": 12.5, "pistes_count": 3}
            record = validate_document(doc)[0]
            self.assertTrue(record["nordique"]["enabled"])
            self.assertNotIn("ski_area_km", record["station"])
    def test_invalid_values(self):
        for data in ({"pistes_count": 1.5}, {"green": -1}, {"ski_area_km": float('nan')}, {"classic": "yes"}, {"skating_km": True}, {"unexpected": 2}):
            with self.subTest(data=data): self.assertIsNotNone(validate_nordic(data))
    def test_empty_and_zero(self):
        for data in ({}, {"enabled": True}, {"ski_area_km": 0}, {"classic": False}): self.assertFalse(has_nordic_data(data))
        self.assertTrue(has_nordic_data({"classic": True}))

class NordicPersistenceTests(unittest.TestCase):
    def test_round_trip_preserves_alpine_and_partial_nordic_updates(self):
        from peewee import SqliteDatabase
        from app.models.resort import Resort
        from app.models.station_widgets import StationWidgets
        from app.models.piste import Piste
        from app.models.lift import Lift
        from app.services.resort_json import apply_record, serialize_station, differences
        from unittest.mock import patch
        models = [Resort, StationWidgets, Piste, Lift]
        db = SqliteDatabase(':memory:')
        with db.bind_ctx(models):
            db.create_tables(models)
            resort = Resort.create(id='test', slug='test', name='Test', ski_area_km=80, pistes_count=25)
            StationWidgets.create(station_slug='test', config=StationWidgets.to_json({'pistes': {'colors': {'blue': 8}}}))
            with patch('app.services.resort_json._advanced_passes', return_value=[]), patch('app.services.resort_json._ski_areas', return_value=[]):
                changes, _ = differences(resort, {'nordique': {'ski_area_km': 12.5, 'enabled': True}})
                self.assertEqual({x['path'] for x in changes}, {'nordique.ski_area_km', 'nordique.enabled'})
                apply_record(resort, {'nordique': {'ski_area_km': 12.5, 'classic': True}})
                apply_record(resort, {'nordique': {'pistes_count': 4}})
                cfg = StationWidgets.from_json(StationWidgets.get().config)
                exported = serialize_station(resort, widgets=cfg)
                self.assertEqual(exported['nordique'], {'ski_area_km': 12.5, 'classic': True, 'pistes_count': 4, 'enabled': True})
                self.assertEqual(exported['station']['ski_area_km'], 80)
                self.assertEqual(exported['station']['pistes_count'], 25)
                self.assertEqual(exported['pistes']['blue'], 8)
                apply_record(resort, {'station': {'name': 'Test'}})
                self.assertEqual(StationWidgets.from_json(StationWidgets.get().config)['nordique'], cfg['nordique'])
            db.drop_tables(models)

class NordicAdminPublicTests(unittest.TestCase):
    def test_admin_toggle_validation_and_public_transport(self):
        from peewee import SqliteDatabase
        from app.models.resort import Resort
        from app.models.station_widgets import StationWidgets
        from app.routes.admin_stations import bp_admin_st
        from app.routes.stations_widgets import bp_widgets
        models = [Resort, StationWidgets]
        db = SqliteDatabase(':memory:')
        with db.bind_ctx(models):
            db.create_tables(models)
            Resort.create(id='test', slug='test', name='Test', is_active=True)
            StationWidgets.create(station_slug='test', config=StationWidgets.to_json({'pistes': {'colors': {'blue': 8}}}))
            app = Flask(__name__)
            app.register_blueprint(bp_admin_st)
            app.register_blueprint(bp_widgets)
            client = app.test_client()
            self.assertEqual(client.patch('/api/admin/stations/test/widgets', json={'nordique': {'enabled': True, 'ski_area_km': 12.5}}).status_code, 200)
            self.assertEqual(client.get('/api/stations/test/widgets').get_json()['nordique']['ski_area_km'], 12.5)
            self.assertEqual(client.patch('/api/admin/stations/test/widgets', json={'nordique': {'pistes_count': -2}}).status_code, 400)
            self.assertEqual(client.patch('/api/admin/stations/test/widgets', json={'nordique': {'enabled': False}}).status_code, 200)
            config = StationWidgets.from_json(StationWidgets.get().config)
            self.assertFalse(config['nordique']['enabled'])
            self.assertEqual(config['nordique']['ski_area_km'], 12.5)
            self.assertEqual(config['pistes']['colors']['blue'], 8)
            db.drop_tables(models)

class NordicMapTests(unittest.TestCase):
    def test_nordic_map_validation_and_import_auto_activation(self):
        from app.services.resort_json import ValidationProblem
        with Flask(__name__).app_context():
            doc = {'schema_version': '1.0', 'station': {'slug': 'map', 'name': 'Map'},
                   'nordique': {'large_map_url': 'https://example.test/nordic.jpg', 'caption': 'Plan nordique'}}
            record = validate_document(doc)[0]
            self.assertTrue(record['nordique']['enabled'])
            self.assertEqual(record['nordique']['large_map_url'], doc['nordique']['large_map_url'])
            for url in ('javascript:alert(1)', 'https://user:password@example.test/map', 'https://example.test/bad url'):
                doc['nordique']['large_map_url'] = url
                with self.assertRaises(ValidationProblem): validate_document(doc)
            self.assertIsNotNone(validate_nordic({'caption': 4}))

    def test_map_round_trip_and_partial_clear_leave_alpine_unchanged(self):
        from peewee import SqliteDatabase
        from app.models.resort import Resort
        from app.models.station_widgets import StationWidgets
        from app.services.resort_json import apply_record, serialize_station, validate_document
        from unittest.mock import patch
        models = [Resort, StationWidgets]
        db = SqliteDatabase(':memory:')
        with db.bind_ctx(models):
            db.create_tables(models)
            resort = Resort.create(id='map', slug='map', name='Map', pistes_large_map_url='https://example.test/alpine.jpg')
            apply_record(resort, {'nordique': {'large_map_url': 'https://example.test/nordic.jpg', 'caption': 'Nordique'}})
            cfg = StationWidgets.from_json(StationWidgets.get().config)
            with patch('app.services.resort_json._advanced_passes', return_value=[]), patch('app.services.resort_json._ski_areas', return_value=[]):
                exported = serialize_station(resort, widgets=cfg, pistes=[], lifts=[])
                with Flask(__name__).app_context():
                    imported = validate_document({'schema_version': '1.0', **exported})[0]
                self.assertEqual(imported['nordique']['large_map_url'], 'https://example.test/nordic.jpg')
                self.assertEqual(exported['pistes']['large_map_url'], 'https://example.test/alpine.jpg')
            apply_record(resort, {'nordique': {'large_map_url': None}})
            saved = StationWidgets.from_json(StationWidgets.get().config)['nordique']
            self.assertIsNone(saved['large_map_url'])
            self.assertEqual(saved['caption'], 'Nordique')
            self.assertEqual(Resort.get().pistes_large_map_url, 'https://example.test/alpine.jpg')
            db.drop_tables(models)
