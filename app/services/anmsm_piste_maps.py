"""Safe, one-at-a-time ingestion of ANMSM piste maps (never logos)."""
import hashlib, json, os, signal, subprocess, sys, tempfile
from pathlib import Path

import requests
from flask import current_app, has_app_context

from app.datetime_utils import utcnow
from app.models.anmsm_station_mapping import AnmsmStationMapping
from app.models.station_piste_map_candidate import StationPisteMapCandidate
from app.services import s3
from app.services.anmsm_logos import (_assert_public_https, _records, _timeout,
                                      LogoImportError)

MIME_FORMATS={"image/jpeg":"jpeg","image/png":"png","image/webp":"webp","application/pdf":"pdf"}
EXTENSIONS={"jpeg":"jpg","png":"png","webp":"webp","pdf":"pdf"}
PISTE_MAPS_FEED_URL = ("https://api-v3.tourinsoft.com/api/syndications/"
                       "anmsm.tourinsoft.com/343718C6-9088-4732-AA05-26695D1E3059"
                       "?refreshCache=0&format=json")
PISTE_MAP_COLLECTION = "PLANPISTESs"

def _media_list(value):
    if isinstance(value, dict): return [value]
    return value if isinstance(value, list) else []

def parse_record(record):
    if not isinstance(record, dict):
        raise LogoImportError("invalid_feed", "ANMSM feed records must be JSON objects")
    fields=record.get("Object") if isinstance(record.get("Object"),dict) else record
    external_id=str(record.get("SyndicObjectID") or record.get("SyndicObjectId") or "").strip()
    name=str(fields.get("NOM") or record.get("SyndicObjectName") or "").strip()
    # Donnees Stations exposes a linked collection, not a media value directly.
    # The deliberately odd casing is the one used by Tourinsoft V3:
    # Object.PLANPISTESs[].Plandespistes.
    maps=[]
    for linked_plan in _media_list(fields.get(PISTE_MAP_COLLECTION)):
        if not isinstance(linked_plan, dict):
            continue
        media=linked_plan.get("Plandespistes")
        if isinstance(media, list):
            media=media[0] if media else None
        if not isinstance(media, dict):
            continue
        linked_external_id=str(linked_plan.get("SyndicObjectId") or linked_plan.get("SyndicObjectID") or "").strip()
        if linked_external_id:
            external_id=linked_external_id
        url=media.get("Url")
        if not url: continue
        media_id=str(media.get("MediaID") or media.get("ID") or url)
        fmt=(media.get("Extension") or media.get("Format") or Path(url.split("?",1)[0]).suffix.lstrip(".")).lower()
        if fmt=="jpg": fmt="jpeg"
        maps.append({"media_id":media_id,"url":url,"format":fmt or None,
                     "title":media.get("Titre"),"credit":media.get("Credit"),
                     "modified_at":media.get("DateModification"),
                     "plan_type":media.get("TypePlan") or media.get("Type")})
    return {"external_station_id":external_id,"external_name":name,"piste_maps":maps}

def fetch_maps(session=requests):
    configured=current_app.config.get("ANMSM_PISTE_MAPS_FEED_URL") if has_app_context() else os.getenv("ANMSM_PISTE_MAPS_FEED_URL")
    url=PISTE_MAPS_FEED_URL if configured is None else str(configured).strip()
    if not url:
        raise LogoImportError("missing_feed_url", "ANMSM piste-map feed URL is not configured")
    if "refreshCache=1" in url or "refreshCache=2" in url: raise LogoImportError("unsafe_feed_configuration","Only refreshCache=0 is permitted")
    response=None
    try:
        if has_app_context(): current_app.logger.info("ANMSM piste-map feed request feed=donnees_stations url=%s",url)
        response=session.get(url,timeout=(_timeout("ANMSM_CONNECT_TIMEOUT",3.05),_timeout("ANMSM_FEED_READ_TIMEOUT",10)))
        if has_app_context(): current_app.logger.info("ANMSM piste-map feed response feed=donnees_stations status=%s",response.status_code)
        if not 200 <= response.status_code < 300:
            error=LogoImportError("source_feed_http_error",f"ANMSM piste-map feed returned HTTP {response.status_code}")
            error.source_http_status=response.status_code
            raise error
        try: payload=response.json()
        except (ValueError, requests.exceptions.JSONDecodeError) as exc:
            raise LogoImportError("invalid_feed_json","ANMSM piste-map feed returned invalid JSON") from exc
        records=_records(payload)
        if any(not isinstance(record,dict) for record in records):
            raise LogoImportError("invalid_feed","ANMSM feed records must be JSON objects")
        objects=[record.get("Object") if isinstance(record.get("Object"),dict) else record for record in records]
        if not any(isinstance(obj,dict) and PISTE_MAP_COLLECTION in obj for obj in objects):
            raise LogoImportError("invalid_feed_structure","ANMSM Donnees Stations feed does not contain PLANPISTESs")
        stations=[]; rejected={"missing_station_id":0,"missing_url":0}; detected=0
        for record in records:
            station=parse_record(record)
            fields=record.get("Object") if isinstance(record.get("Object"),dict) else record
            raw=len(_media_list(fields.get(PISTE_MAP_COLLECTION)))
            rejected["missing_url"] += raw-len(station["piste_maps"])
            if not station["external_station_id"]: rejected["missing_station_id"] += len(station["piste_maps"]); continue
            detected += len(station["piste_maps"]); stations.append(station)
        if has_app_context():
            current_app.logger.info("ANMSM piste-map feed parsed feed=donnees_stations objects=%s stations=%s plans=%s rejected=%s reasons=%s",len(records),len(stations),detected,sum(rejected.values()),rejected)
        return stations
    except requests.Timeout as exc: raise LogoImportError("source_feed_timeout","ANMSM feed request timed out") from exc
    except requests.RequestException as exc: raise LogoImportError("source_feed_request_error","ANMSM piste-map feed request failed") from exc
    finally:
        if response is not None: response.close()

def find_media(external_id, media_id, session=requests):
    for station in fetch_maps(session):
        if station["external_station_id"].casefold()==external_id.casefold():
            for media in station["piste_maps"]:
                if media["media_id"]==media_id: return station,media
    raise LogoImportError("media_not_found","ANMSM piste map was not found")

def download(url, session=requests):
    limit=int(current_app.config.get("ANMSM_PISTE_MAP_MAX_DOWNLOAD_BYTES") or 40*1024*1024)
    _assert_public_https(url); response=None; path=None
    try:
        response=session.get(url,stream=True,allow_redirects=False,timeout=(_timeout("ANMSM_CONNECT_TIMEOUT",3.05),_timeout("ANMSM_MEDIA_READ_TIMEOUT",10)))
        if response.status_code != 200: raise LogoImportError("download_http_error",f"Media returned HTTP {response.status_code}")
        mime=response.headers.get("Content-Type","").split(";",1)[0].lower()
        if mime not in MIME_FORMATS: raise LogoImportError("invalid_mime","Unsupported piste-map Content-Type")
        declared=response.headers.get("Content-Length")
        if declared and int(declared)>limit: raise LogoImportError("download_too_large","Piste map exceeds configured size limit")
        digest=hashlib.sha256(); total=0
        with tempfile.NamedTemporaryFile(prefix="anmsm-map-",delete=False) as out:
            path=out.name
            for chunk in response.iter_content(64*1024):
                if not chunk: continue
                total+=len(chunk)
                if total>limit: raise LogoImportError("download_too_large","Piste map exceeds configured size limit")
                digest.update(chunk); out.write(chunk)
        return path,total,mime,digest.hexdigest()
    except requests.Timeout as exc: raise LogoImportError("source_download_timeout","Piste-map download timed out") from exc
    except Exception:
        if path:
            try: os.unlink(path)
            except FileNotFoundError: pass
        raise
    finally:
        if response is not None: response.close()

def _convert(source, output, source_format=None):
    cmd=[sys.executable,"-m","app.services.anmsm_piste_map_worker",source,output,
         "--max-pixels",str(int(current_app.config.get("ANMSM_PISTE_MAP_MAX_PIXELS",120_000_000))),
         "--max-dimension",str(int(current_app.config.get("ANMSM_PISTE_MAP_DISPLAY_MAX_DIMENSION",6000))),
         "--output-limit",str(int(current_app.config.get("ANMSM_PISTE_MAP_DISPLAY_MAX_BYTES",12*1024*1024))),
         "--max-pages",str(int(current_app.config.get("ANMSM_PISTE_MAP_PDF_MAX_PAGES",25))),
         "--memory-mb",str(int(current_app.config.get("ANMSM_CONVERSION_MEMORY_MB",768)))]
    if source_format: cmd.extend(["--source-format",source_format])
    process=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    try: stdout,stderr=process.communicate(timeout=float(current_app.config.get("ANMSM_CONVERSION_TIMEOUT",30)))
    except subprocess.TimeoutExpired as exc:
        os.killpg(process.pid,signal.SIGKILL); process.communicate()
        raise LogoImportError("conversion_timeout","Map conversion timed out") from exc
    result=subprocess.CompletedProcess(cmd,process.returncode,stdout,stderr)
    if result.returncode<0: raise LogoImportError("conversion_interrupted","Map conversion was interrupted")
    try: payload=json.loads(result.stdout)
    except ValueError as exc: raise LogoImportError("conversion_interrupted","Invalid converter response") from exc
    if result.returncode or not payload.get("ok"): raise LogoImportError(payload.get("error","conversion_interrupted"),"Map conversion failed")
    return payload["metadata"]

def _temporary(prefix, suffix=""):
    handle=tempfile.NamedTemporaryFile(prefix=prefix,suffix=suffix,delete=False); handle.close()
    return handle.name

def _resume_pdf(candidate):
    """Fill only the missing display object, using the immutable S3 original."""
    source=_temporary("anmsm-map-s3-", ".pdf"); display=_temporary("anmsm-map-display-", ".webp")
    try:
        limit=int(current_app.config.get("ANMSM_PISTE_MAP_MAX_DOWNLOAD_BYTES") or 40*1024*1024)
        try: s3.download_file(candidate.original_s3_key,source,limit)
        except (ValueError, OSError) as exc: raise LogoImportError("stored_original_unavailable","Stored PDF original is unusable") from exc
        metadata=_convert(source,display,"pdf"); prefix=candidate.original_s3_key.rsplit("/",1)[0]
        display_key=f"{prefix}/display.webp"; s3.put_file(display_key,display,"image/webp")
        candidate.display_s3_key=display_key; candidate.display_width=metadata["display_width"]
        candidate.display_height=metadata["display_height"]; candidate.display_size_bytes=metadata["display_size_bytes"]
        candidate.source_width=metadata["source_width"]; candidate.source_height=metadata["source_height"]
        candidate.warnings=json.dumps([x for x in candidate.warning_codes() if x!="pdf_display_not_generated"])
        candidate.error_code=None; candidate.error_message=None; candidate.updated_at=utcnow(); candidate.save()
        return candidate
    finally:
        for path in (source,display):
            try: os.unlink(path)
            except FileNotFoundError: pass

def prepare(external_id, media_id, session=requests):
    station,media=find_media(external_id,media_id,session)
    mapping=next((m for m in AnmsmStationMapping.select().where(
        (AnmsmStationMapping.source=="anmsm")&(AnmsmStationMapping.verified==True))
        if m.external_station_id.strip().casefold()==station["external_station_id"].strip().casefold()),None)
    if not mapping: raise LogoImportError("station_unmatched","Confirm the existing station mapping first")
    resumable=(StationPisteMapCandidate.select().where(
        (StationPisteMapCandidate.station==mapping.station_id)&(StationPisteMapCandidate.anmsm_media_id==media_id)&
        (StationPisteMapCandidate.status=="pending")&StationPisteMapCandidate.original_s3_key.is_null(False)&
        StationPisteMapCandidate.display_s3_key.is_null(True)
    ).order_by(StationPisteMapCandidate.updated_at.desc()).first())
    if resumable and resumable.source_format=="pdf": return _resume_pdf(resumable),False
    source=display=None
    try:
        source,size,mime,checksum=download(media["url"],session)
        existing=StationPisteMapCandidate.get_or_none((StationPisteMapCandidate.station==mapping.station_id)&(StationPisteMapCandidate.anmsm_media_id==media_id)&(StationPisteMapCandidate.source_checksum==checksum))
        if existing: return existing,True
        fmt=MIME_FORMATS[mime]; prefix=f"anmsm/piste-maps/{mapping.station_id}/{media_id}/{checksum}"
        original_key=f"{prefix}/original.{EXTENSIONS[fmt]}"
        metadata={"source_format":fmt,"source_width":None,"source_height":None,"display_width":None,"display_height":None,"display_size_bytes":None,"warnings":[]}
        display_key=None
        if fmt in {"jpeg","png","webp","pdf"}:
            with tempfile.NamedTemporaryFile(prefix="anmsm-map-display-",suffix=".webp",delete=False) as out: display=out.name
            metadata=_convert(source,display,fmt); display_key=f"{prefix}/display.webp"
        # Upload only after the isolated decoder has
        # accepted the source. No corrupt source is knowingly placed in S3.
        s3.put_file(original_key,source,mime)
        if display_key: s3.put_file(display_key,display,"image/webp")
        candidate=StationPisteMapCandidate.create(station=mapping.station_id,external_station_id=external_id,
            anmsm_media_id=media_id,anmsm_title=media.get("title"),anmsm_credit=media.get("credit"),plan_type=media.get("plan_type"),
            source_url=media["url"],source_checksum=checksum,source_format=metadata["source_format"],source_width=metadata["source_width"],
            source_height=metadata["source_height"],source_size_bytes=size,original_s3_key=original_key,display_s3_key=display_key,
            display_width=metadata["display_width"],display_height=metadata["display_height"],display_size_bytes=metadata["display_size_bytes"],
            warnings=json.dumps(metadata["warnings"]),status="pending",detected_at=utcnow())
        return candidate,False
    finally:
        for path in (source,display):
            if path:
                try: os.unlink(path)
                except FileNotFoundError: pass
