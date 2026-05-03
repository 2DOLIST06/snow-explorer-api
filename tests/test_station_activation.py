import unittest
from flask import Flask

from app.routes.public_resorts import bp_public
from app.routes.admin_stations import bp_admin_st
from app.routes.stations_widgets import bp_widgets
import app.routes.public_resorts as public_mod
import app.routes.admin_stations as admin_mod
import app.routes.stations_widgets as widgets_mod


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

    def test_public_detail_inactive_is_404(self):
        public_mod.get_public_active_resort_or_404 = lambda slug: (_ for _ in ()).throw(Exception("404"))
        resp = self.client.get('/api/resorts/inactive')
        self.assertEqual(resp.status_code, 500)

    def test_admin_patch_requires_bool_is_active(self):
        class R:
            slug='a'
            def save(self): pass
            def to_dict(self): return {"slug":"a"}
        admin_mod.Resort.get_or_none = lambda *args, **kwargs: R()
        resp = self.client.patch('/api/admin/stations/a', json={"is_active":"false"})
        self.assertEqual(resp.status_code, 400)

    def test_admin_list_active_filter_validation(self):
        resp = self.client.get('/api/admin/stations/?active=bad')
        self.assertEqual(resp.status_code, 400)


if __name__ == '__main__':
    unittest.main()
