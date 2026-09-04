from collections import Counter
from urllib.parse import unquote, urlparse

from flask import Blueprint, current_app, g, jsonify, request

from app.datetime_utils import utcnow
from app.models.anmsm_station_mapping import AnmsmStationMapping
from app.models.resort import Resort
from app.models.station_piste_map_candidate import StationPisteMapCandidate
from app.services import s3
from app.services.public_cache import invalidate_station

bp_admin_piste_maps=Blueprint("admin_piste_maps",__name__,url_prefix="/api/admin/anmsm/piste-maps")

def _normalized_external_id(value):
    """Normalize only the comparison key; persisted/source values stay intact."""
    return str(value or "").strip().casefold()

def _urls(candidate):
    return (s3.preview_url(candidate.display_s3_key) if candidate and candidate.display_s3_key else None,
            s3.preview_url(candidate.original_s3_key) if candidate and candidate.original_s3_key else None)

def _candidate_row(candidate):
    preview,original=_urls(candidate)
    return {"candidate_id":candidate.id,"candidate_status":candidate.status,
            "candidate_preview_url":preview,"candidate_original_url":original,
            "warnings":candidate.warning_codes(),"error":candidate.error_message}

def workspace_data(stations):
    mappings={_normalized_external_id(m.external_station_id):m for m in AnmsmStationMapping.select().where((AnmsmStationMapping.source=="anmsm")&(AnmsmStationMapping.verified==True))}
    resorts={str(r.id):r for r in Resort.select()}
    candidates={}
    for c in StationPisteMapCandidate.select().order_by(StationPisteMapCandidate.updated_at.desc()):
        candidates.setdefault((_normalized_external_id(c.external_station_id),c.anmsm_media_id),c)
    rows=[]; station_rows=[]
    for source in stations:
        mapping=mappings.get(_normalized_external_id(source["external_station_id"])); resort=resorts.get(str(mapping.station_id)) if mapping else None
        station_rows.append({"external_station_id":source["external_station_id"],
            "anmsm_station_name":source["external_name"],"plans_detected":len(source["piste_maps"]),
            "station_id":resort.id if resort else None,"station_name":resort.name if resort else None,
            "mapping_status":"matched" if resort else "unmatched"})
        for media in source["piste_maps"]:
            candidate=candidates.get((_normalized_external_id(source["external_station_id"]),media["media_id"]))
            row={"external_station_id":source["external_station_id"],"anmsm_station_name":source["external_name"],
                 "anmsm_media_id":media["media_id"],"anmsm_title":media.get("title"),"anmsm_credit":media.get("credit"),
                 "plan_type":media.get("plan_type"),"source_modified_at":media.get("modified_at"),
                 "source_url":media["url"],"source_format":media.get("format"),
                 "station_id":resort.id if resort else None,"station_name":resort.name if resort else None,
                 "mapping_status":"matched" if resort else "unmatched","current_plan_url":resort.pistes_large_map_url if resort else None,
                 "candidate_id":None,"candidate_status":None,"candidate_preview_url":None,"candidate_original_url":None,
                 "warnings":[],"preparation_required":True,"error":None}
            if candidate: row.update(_candidate_row(candidate)); row["preparation_required"]=False
            rows.append(row)
    statuses=Counter(r["candidate_status"] for r in rows)
    return {"ok":True,"stations":station_rows,"rows":rows,"stats":{"stations_detected":len(station_rows),"plans_detected":len(rows),
        "stations_matched":sum(r["mapping_status"]=="matched" for r in station_rows),
        "stations_unmatched":sum(r["mapping_status"]=="unmatched" for r in station_rows),
        "plans_ready":sum(bool(r["candidate_id"] and not r["error"]) for r in rows),
        "plans_to_prepare":sum(r["preparation_required"] for r in rows),"plans_approved":statuses["approved"],
        "errors":sum(bool(r["error"]) for r in rows)}}

@bp_admin_piste_maps.get("/workspace")
def workspace():
    from app.services.anmsm_piste_maps import fetch_maps, LogoImportError
    try: return jsonify(workspace_data(fetch_maps()))
    except LogoImportError as exc:
        payload={"ok":False,"error":exc.code,"message":str(exc)}
        source_status=getattr(exc,"source_http_status",None)
        if source_status is not None: payload["source_http_status"]=source_status
        return jsonify(payload),502

@bp_admin_piste_maps.post("/prepare")
def prepare():
    payload=request.get_json(silent=True) or {}; external_id=payload.get("external_station_id"); media_id=payload.get("anmsm_media_id")
    if not all(isinstance(x,str) and x.strip() for x in (external_id,media_id)): return jsonify({"ok":False,"error":"invalid_request"}),400
    from app.services.anmsm_piste_maps import prepare as prepare_map, LogoImportError
    try:
        candidate,unchanged=prepare_map(external_id.strip(),media_id.strip())
        row={"external_station_id":candidate.external_station_id,"anmsm_station_name":None,"anmsm_media_id":candidate.anmsm_media_id,
             "anmsm_title":candidate.anmsm_title,"anmsm_credit":candidate.anmsm_credit,"plan_type":candidate.plan_type,
             "source_url":candidate.source_url,"source_format":candidate.source_format,"station_id":candidate.station_id,
             "station_name":candidate.station.name,"mapping_status":"matched","current_plan_url":candidate.station.pistes_large_map_url,
             "preparation_required":False,**_candidate_row(candidate)}
        return jsonify({"ok":True,"unchanged":unchanged,"row":row})
    except LogoImportError as exc:
        status=413 if exc.code=="download_too_large" else 504 if "timeout" in exc.code else 422
        return jsonify({"ok":False,"error":exc.code,"message":str(exc)}),status

def _key(url):
    base=s3.setting("AWS_S3_PUBLIC_URL").rstrip("/")
    return unquote(urlparse(url).path.lstrip("/")) if url and base and url.startswith(base+"/") else None

def _approve(candidate_id):
    candidate=StationPisteMapCandidate.get_or_none(StationPisteMapCandidate.id==candidate_id)
    if not candidate:return {"candidate_id":candidate_id,"ok":False,"error":"candidate_not_found"}
    if candidate.status=="approved":return {"candidate_id":candidate_id,"ok":True,"status":"approved","unchanged":True}
    key=candidate.display_s3_key or (candidate.original_s3_key if candidate.source_format!="pdf" else None)
    if not key or not s3.validate_object(key):return {"candidate_id":candidate_id,"ok":False,"error":"invalid_candidate_object"}
    resort=None
    try:
        with StationPisteMapCandidate._meta.database.atomic():
            candidate=StationPisteMapCandidate.get_by_id(candidate_id); resort=Resort.get_by_id(candidate.station_id)
            candidate.previous_plan_url=resort.pistes_large_map_url; candidate.previous_plan_s3_key=_key(resort.pistes_large_map_url)
            resort.pistes_large_map_url=s3.public_url(key); resort.updated_at=utcnow(); resort.save()
            candidate.status="approved"; candidate.approved_at=utcnow(); candidate.approved_by=getattr(g,"admin_user",None); candidate.updated_at=utcnow(); candidate.save()
        invalidate_station(resort.slug)
        return {"candidate_id":candidate_id,"ok":True,"status":"approved","published_plan_url":resort.pistes_large_map_url}
    except Exception as exc:
        current_app.logger.exception("ANMSM piste-map approval failed")
        return {"candidate_id":candidate_id,"ok":False,"error":"approval_failed","message":str(exc)[:500]}

@bp_admin_piste_maps.post("/bulk-approve")
def bulk_approve():
    payload=request.get_json(silent=True) or {}; ids=payload.get("candidate_ids")
    if not isinstance(ids,list) or not ids or any(isinstance(x,bool) or not isinstance(x,int) for x in ids):return jsonify({"ok":False,"error":"invalid_candidate_ids"}),400
    unique=list(dict.fromkeys(ids)); candidates=list(StationPisteMapCandidate.select().where(StationPisteMapCandidate.id.in_(unique)))
    duplicate=[station for station,count in Counter(c.station_id for c in candidates).items() if count>1]
    if duplicate:return jsonify({"ok":False,"error":"multiple_primary_maps_for_station","station_ids":duplicate}),409
    results=[_approve(x) for x in unique]
    return jsonify({"ok":True,"approved_count":sum(x["ok"] for x in results),"failed_count":sum(not x["ok"] for x in results),"results":results})
