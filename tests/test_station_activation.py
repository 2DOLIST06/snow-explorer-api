import unittest
from unittest.mock import patch
from flask import Flask

from app.routes.public_resorts import bp_public
from app.routes.admin_stations import bp_admin_st
from app.routes.stations_widgets import bp_widgets


class DummyResort:
    def __init__(self, slug, is_active=True):
        self.id = "1"
        self.name = slug
        self.slug = slug
        self.is_active = is_active

    def to_dict(self):
        return {"id": self.id, "name": self.name, "slug": self.slug, "is_active": self.is_active}


class StationActivationTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(bp_public)
        app.register_blueprint(bp_admin_st)
        app.register_blueprint(bp_widgets)
        self.client = app.test_client()

    def test_public_list_excludes_inactive(self):
        with patch('app.routes.public_resorts._base_query') as query_builder:
            class Q:
                def where(self, *args, **kwargs):
                    return self
                def order_by(self, *args, **kwargs):
                    return self
                def limit(self, *args, **kwargs):
                    return [DummyResort('active-1', True)]
            query_builder.return_value = Q()
            resp = self.client.get('/api/resorts/')
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]['slug'], 'active-1')

    def test_public_detail_inactive_is_404(self):
        with patch('app.routes.public_resorts.get_public_active_resort_or_404', side_effect=lambda slug: (_ for _ in ()).throw(__import__('werkzeug.exceptions').exceptions.NotFound())):
            resp = self.client.get('/api/resorts/inactive')
        self.assertEqual(resp.status_code, 404)

    def test_widgets_inactive_is_404(self):
        with patch('app.routes.stations_widgets.get_public_active_resort_or_404', side_effect=lambda slug: (_ for _ in ()).throw(__import__('werkzeug.exceptions').exceptions.NotFound())):
            resp = self.client.get('/api/stations/inactive/widgets')
        self.assertEqual(resp.status_code, 404)

    def test_admin_patch_requires_bool_is_active(self):
        class R:
            slug = 'a'
            is_active = True
            def save(self):
                pass
            def to_dict(self):
                return {'slug': 'a'}

        with patch('app.routes.admin_stations.Resort.get_or_none', return_value=R()):
            resp = self.client.patch('/api/admin/stations/a', json={'is_active': 'false'})
        self.assertEqual(resp.status_code, 400)

    def test_reactivation_restores_access(self):
        with patch('app.routes.public_resorts.get_public_active_resort_or_404', return_value=DummyResort('reactivated', True)):
            resp = self.client.get('/api/resorts/reactivated')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['slug'], 'reactivated')


if __name__ == '__main__':
    unittest.main()
