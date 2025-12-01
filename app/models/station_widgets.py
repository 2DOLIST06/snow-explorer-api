from app.models.base import db
from peewee import Model, CharField, TextField
import json

# Si tu es sur Postgres avec playhouse.postgres_ext tu peux utiliser JSONField :
# from playhouse.postgres_ext import JSONField
# class StationWidgets(Model):
#     station_slug = CharField(unique=True, max_length=255)
#     config = JSONField(default=dict)
#     class Meta:
#         database = db
#         table_name = "station_widgets"

class StationWidgets(Model):
    station_slug = CharField(unique=True, max_length=255)
    # Stockage JSON en texte si pas de JSONField natif
    config = TextField(default="{}")

    class Meta:
        database = db
        table_name = "station_widgets"

    @staticmethod
    def to_json(cfg: dict) -> str:
        return json.dumps(cfg, ensure_ascii=False)

    @staticmethod
    def from_json(txt: str | None) -> dict:
        if not txt:
            return {}
        try:
            return json.loads(txt)
        except Exception:
            return {}

