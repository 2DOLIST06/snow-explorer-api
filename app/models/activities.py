"""Independent station content: bulk operations never serialize a whole resort."""
import uuid
from peewee import CharField, ForeignKeyField, IntegerField, TextField
from app.models.base import BaseModel
from app.models.resort import Resort
from app.datetime_utils import UTCDateTimeField, utcnow


class StationActivities(BaseModel):
    station = ForeignKeyField(Resort, primary_key=True, on_delete="CASCADE")
    revision = IntegerField(default=1)
    block_json = TextField()
    class Meta:
        table_name = "station_activities"


class ActivityTemplate(BaseModel):
    id = CharField(primary_key=True, default=lambda: str(uuid.uuid4()))
    name = CharField(max_length=200)
    revision = IntegerField(default=1)
    block_json = TextField()
    class Meta:
        table_name = "activity_templates"


class ActivityBatch(BaseModel):
    id = CharField(primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id = IntegerField()
    plan_json = TextField()
    created_at = UTCDateTimeField(default=utcnow)
    class Meta:
        table_name = "activity_batches"


class ActivityBatchResult(BaseModel):
    batch = ForeignKeyField(ActivityBatch, on_delete="CASCADE")
    station_id = CharField()
    status = CharField()
    message = TextField(default="")
    class Meta:
        table_name = "activity_batch_results"
        indexes = ((("batch", "station_id"), True),)
