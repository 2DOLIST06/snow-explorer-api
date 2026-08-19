from app.models.base import db
from peewee import Model, CharField, TextField
from app.datetime_utils import utcnow
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

    def save(self, *args, **kwargs):
        result = super().save(*args, **kwargs)
        # Les widgets font partie de la fiche publique : leur modification doit
        # donc invalider la date éditoriale de la station dans le sitemap.
        from app.models.resort import Resort
        Resort.update(updated_at=utcnow()).where(
            Resort.slug == self.station_slug
        ).execute()
        return result

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
