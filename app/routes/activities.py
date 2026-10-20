import json
import logging
from flask import Blueprint, g, jsonify, request
from app.models.base import db
from app.models.resort import Resort
from app.models.activities import ActivityBatch, ActivityBatchResult, ActivityTemplate
from app.services.activities import snapshot, station, validate_block, write

bp_activities = Blueprint("activities", __name__)
log = logging.getLogger(__name__)


@bp_activities.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@bp_activities.errorhandler(ValueError)
def invalid(error):
    return jsonify(error="validation_error", message=str(error)), 400


def body():
    value = request.get_json(silent=True)
    if not isinstance(value, dict): raise ValueError("Objet JSON requis")
    return value


def template_json(row):
    return {"id": row.id, "name": row.name, "revision": row.revision, "block": json.loads(row.block_json)}


def admin():
    # Existing session/CSRF middleware runs for every /api/admin path. This
    # additional check also excludes future non-admin/OAuth write credentials.
    return getattr(getattr(g, "admin_user", None), "role", None) == "admin"


@bp_activities.before_request
def authorize():
    if request.path.startswith("/api/admin/") and request.method != "OPTIONS" and not admin():
        return jsonify(error="admin_required"), 403


@bp_activities.get("/api/stations/<slug>/activities")
def public_block(slug):
    resort = station(slug)
    if not resort or not resort.is_active: return jsonify(error="not_found"), 404
    return jsonify(snapshot(resort.id)["block"])


@bp_activities.route("/api/admin/stations/<slug>/activities", methods=["GET", "PUT"])
def station_block(slug):
    if request.method == "GET":
        resort = station(slug)
        if not resort: return jsonify(error="not_found"), 404
        return jsonify(snapshot(resort.id))
    payload = body(); block = validate_block(payload.get("block"))
    with db.atomic():
        resort = station(slug, lock=True)
        if not resort: return jsonify(error="not_found"), 404
        if not write(resort.id, block, payload.get("revision")):
            return jsonify(error="conflict", message="Le bloc a changé. Rechargez-le avant de réessayer."), 409
        return jsonify(snapshot(resort.id))


@bp_activities.route("/api/admin/activity-templates", methods=["GET", "POST"])
def templates():
    if request.method == "GET":
        return jsonify(items=[template_json(row) for row in ActivityTemplate.select().order_by(ActivityTemplate.name)])
    payload = body(); name = payload.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200: raise ValueError("Nom requis (200 caractères maximum)")
    block = validate_block(payload.get("block"))
    row = ActivityTemplate.create(name=name.strip(), block_json=json.dumps(block, ensure_ascii=False))
    return jsonify(template_json(row)), 201


@bp_activities.put("/api/admin/activity-templates/<ident>")
def update_template(ident):
    payload = body(); block = validate_block(payload.get("block")); name = payload.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200: raise ValueError("Nom requis (200 caractères maximum)")
    revision = payload.get("revision")
    if type(revision) is not int: raise ValueError("Révision requise")
    count = ActivityTemplate.update(name=name.strip(), block_json=json.dumps(block, ensure_ascii=False), revision=revision + 1).where(
        (ActivityTemplate.id == ident) & (ActivityTemplate.revision == revision)).execute()
    if not count: return jsonify(error="conflict", message="Le modèle a changé. Rechargez-le."), 409
    return jsonify(template_json(ActivityTemplate.get_by_id(ident)))


@bp_activities.get("/api/admin/activity-stations")
def station_options():
    try: page = int(request.args.get("page", 1))
    except ValueError: raise ValueError("Page invalide")
    if page < 1: raise ValueError("Page invalide")
    query = Resort.select()
    q = request.args.get("q", "").strip()
    if q: query = query.where((Resort.name ** f"%{q}%") | (Resort.slug ** f"%{q}%"))
    for key in ("country_code", "department", "region_id"):
        if request.args.get(key): query = query.where(getattr(Resort, key) == request.args[key])
    if request.args.get("active") in {"true", "false"}: query = query.where(Resort.is_active == (request.args["active"] == "true"))
    from app.models.activities import StationActivities
    has = request.args.get("has_block")
    if has in {"true", "false"}:
        ids = StationActivities.select(StationActivities.station)
        query = query.where(Resort.id.in_(ids) if has == "true" else Resort.id.not_in(ids))
    total = query.count()
    return jsonify(items=[{"id": row.id, "slug": row.slug, "name": row.name, "country_code": row.country_code,
        "department": row.department, "region_id": row.region_id, "active": row.is_active,
        "has_block": snapshot(row.id)["has_block"]} for row in query.order_by(Resort.name, Resort.id).paginate(page, 30)],
        pages=(total + 29)//30, total=total)


@bp_activities.post("/api/admin/activity-templates/<ident>/prepare")
def prepare(ident):
    payload = body(); ids = payload.get("station_ids"); mode = payload.get("mode", "add")
    if mode not in {"add", "replace"}: raise ValueError("Mode invalide")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 200 or any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
        raise ValueError("Sélectionnez de 1 à 200 stations distinctes")
    row = ActivityTemplate.get_or_none(ActivityTemplate.id == ident)
    if not row: return jsonify(error="not_found"), 404
    if payload.get("revision") != row.revision:
        return jsonify(error="conflict", message="Le modèle a changé. Rechargez-le avant de préparer le lot."), 409
    # Freeze the reviewed template; editing it later cannot alter this batch.
    plan = {"mode": mode, "template_name": row.name, "block": json.loads(row.block_json), "stations": []}
    for ident in ids:
        resort = Resort.get_or_none(Resort.id == ident)
        if not resort: raise ValueError("Station introuvable : " + ident)
        current = snapshot(ident)
        plan["stations"].append({"id": ident, "name": resort.name, "slug": resort.slug, "revision": current["revision"],
            "action": "ignored" if mode == "add" and current["has_block"] else "replaced" if current["has_block"] else "added"})
    batch = ActivityBatch.create(owner_id=g.admin_user.id, plan_json=json.dumps(plan, ensure_ascii=False))
    return jsonify(id=batch.id, plan=plan), 201


def owned_batch(ident):
    return ActivityBatch.get_or_none((ActivityBatch.id == ident) & (ActivityBatch.owner_id == g.admin_user.id))


def result_json(batch):
    plan = json.loads(batch.plan_json)
    results = {row.station_id: {"status": row.status, "message": row.message} for row in ActivityBatchResult.select().where(ActivityBatchResult.batch == batch.id)}
    items = [{**row, **results.get(row["id"], {"status": "pending", "message": ""})} for row in plan["stations"]]
    return {"id": batch.id, "plan": plan, "items": items, "pending": sum(row["status"] == "pending" for row in items)}


@bp_activities.get("/api/admin/activity-batches/<ident>")
def batch_status(ident):
    batch = owned_batch(ident)
    if not batch: return jsonify(error="not_found"), 404
    return jsonify(result_json(batch))


@bp_activities.post("/api/admin/activity-batches/<ident>/execute")
def execute(ident):
    batch = owned_batch(ident)
    if not batch: return jsonify(error="not_found"), 404
    plan = json.loads(batch.plan_json)
    # Small requests avoid proxy timeouts. Each result and content write commit
    # together. A lost HTTP response can safely be retried with the same batch ID.
    attempted = 0
    previous = {row.station_id: row.status for row in ActivityBatchResult.select().where(ActivityBatchResult.batch == ident)}
    # Pending stations take priority over retries, so repeated failures cannot
    # starve the rest of a large run.
    targets = sorted(plan["stations"], key=lambda row: row["id"] in previous)
    for target in targets:
        if attempted >= 25: break
        try:
            with db.atomic():
                query = ActivityBatch.select().where(ActivityBatch.id == ident)
                if db.__class__.__name__ != "SqliteDatabase": query = query.for_update()
                query.get()  # Serialize concurrent retries of the same run.
                result = ActivityBatchResult.get_or_none((ActivityBatchResult.batch == ident) & (ActivityBatchResult.station_id == target["id"]))
                if result and result.status != "error": continue
                attempted += 1
                resort = station(target["slug"], lock=True)
                status, message = "conflict", "Le bloc ou l’identité de la station a changé ; préparez un nouveau lot."
                if target["action"] == "ignored": status, message = "ignored", "Bloc déjà présent lors de la préparation."
                elif resort and resort.id == target["id"] and write(resort.id, plan["block"], target["revision"]):
                    status, message = target["action"], "Bloc enregistré."
                ActivityBatchResult.insert(batch=ident, station_id=target["id"], status=status, message=message).on_conflict(
                    conflict_target=[ActivityBatchResult.batch, ActivityBatchResult.station_id], update={ActivityBatchResult.status: status, ActivityBatchResult.message: message}).execute()
        except Exception:
            log.exception("Activity batch %s failed for station %s", ident, target["id"])
            # The station transaction rolled back. A subsequent execute retries errors.
            with db.atomic():
                query = ActivityBatch.select().where(ActivityBatch.id == ident)
                if db.__class__.__name__ != "SqliteDatabase": query = query.for_update()
                query.get()
                previous_result = ActivityBatchResult.get_or_none((ActivityBatchResult.batch == ident) & (ActivityBatchResult.station_id == target["id"]))
                if not previous_result or previous_result.status == "error":
                    ActivityBatchResult.insert(batch=ident, station_id=target["id"], status="error", message="Échec temporaire ; relancez ce lot.").on_conflict(
                        conflict_target=[ActivityBatchResult.batch, ActivityBatchResult.station_id], update={ActivityBatchResult.status: "error", ActivityBatchResult.message: "Échec temporaire ; relancez ce lot."}).execute()
    return jsonify(result_json(batch))
