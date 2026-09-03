"""Safe, one-at-a-time ingestion of ANMSM piste maps (never logos)."""
import hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path

import requests
from flask import current_app, has_app_context

from app.datetime_utils import utcnow
from app.models.anmsm_station_mapping import AnmsmStationMapping
from app.models.station_piste_map_candidate import StationPisteMapCandidate
from app.services import s3
from app.services.anmsm_logos import (_assert_public_https, _records, _timeout,
                                      FEED_URL, LogoImportError)

MIME_FORMATS={"image/jpeg":"jpeg","image/png":"png","image/webp":"webp","application/pdf":"pdf"}
EXTENSIONS={"jpeg":"jpg","png":"png","webp":"webp","pdf":"pdf"}

def _media_list(value):
    if isinstance(value, dict): return [value]
    return value if isinstance(value, list) else []

def parse_record(record):
    fields=record.get("Object") if isinstance(record.get("Object"),dict) else record
    external_id=str(record.get("SyndicObjectID") or "").strip()
    name=str(fields.get("NOM") or record.get("SyndicObjectName") or "").strip()
    # These are explicit Tourinsoft export columns, configurable for another
    # syndication. We deliberately never classify a map from its title.
    configured=(os.getenv("ANMSM_PISTE_MAP_FIELDS") or "PLANPISTES,PLAN_DES_PISTES").split(",")
    maps=[]
    for field in configured:
        for media in _media_list(fields.get(field.strip())):
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
    url=(current_app.config.get("ANMSM_PISTE_MAPS_FEED_URL") if has_app_context() else None) or os.getenv("ANMSM_PISTE_MAPS_FEED_URL") or os.getenv("ANMSM_STATIONS_FEED_URL") or FEED_URL
    if "refreshCache=1" in url or "refreshCache=2" in url: raise LogoImportError("unsafe_feed_configuration","Only refreshCache=0 is permitted")
    response=None
    try:
        response=session.get(url,timeout=(_timeout("ANMSM_CONNECT_TIMEOUT",3.05),_timeout("ANMSM_FEED_READ_TIMEOUT",10)))
        response.raise_for_status()
        return [x for x in map(parse_record,_records(response.json())) if x["external_station_id"]]
    except requests.Timeout as exc: raise LogoImportError("source_feed_timeout","ANMSM feed request timed out") from exc
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

def _convert(source, output):
    cmd=[sys.executable,"-m","app.services.anmsm_piste_map_worker",source,output,
         "--max-pixels",str(int(current_app.config.get("ANMSM_PISTE_MAP_MAX_PIXELS",120_000_000))),
         "--max-dimension",str(int(current_app.config.get("ANMSM_PISTE_MAP_DISPLAY_MAX_DIMENSION",6000))),
         "--output-limit",str(int(current_app.config.get("ANMSM_PISTE_MAP_DISPLAY_MAX_BYTES",12*1024*1024))),
         "--memory-mb",str(int(current_app.config.get("ANMSM_CONVERSION_MEMORY_MB",768)))]
    try: result=subprocess.run(cmd,capture_output=True,text=True,timeout=float(current_app.config.get("ANMSM_CONVERSION_TIMEOUT",30)),check=False)
    except subprocess.TimeoutExpired as exc: raise LogoImportError("conversion_timeout","Map conversion timed out") from exc
    if result.returncode<0: raise LogoImportError("conversion_interrupted","Map conversion was interrupted")
    try: payload=json.loads(result.stdout)
    except ValueError as exc: raise LogoImportError("conversion_interrupted","Invalid converter response") from exc
    if result.returncode or not payload.get("ok"): raise LogoImportError(payload.get("error","conversion_interrupted"),"Map conversion failed")
    return payload["metadata"]

def prepare(external_id, media_id, session=requests):
    station,media=find_media(external_id,media_id,session)
    mapping=AnmsmStationMapping.get_or_none((AnmsmStationMapping.source=="anmsm") & (AnmsmStationMapping.external_station_id==station["external_station_id"]) & (AnmsmStationMapping.verified==True))
    if not mapping: raise LogoImportError("station_unmatched","Confirm the existing station mapping first")
    source=display=None
    try:
        source,size,mime,checksum=download(media["url"],session)
        existing=StationPisteMapCandidate.get_or_none((StationPisteMapCandidate.station==mapping.station_id)&(StationPisteMapCandidate.anmsm_media_id==media_id)&(StationPisteMapCandidate.source_checksum==checksum))
        if existing: return existing,True
        fmt=MIME_FORMATS[mime]; prefix=f"anmsm/piste-maps/{mapping.station_id}/{media_id}/{checksum}"
        original_key=f"{prefix}/original.{EXTENSIONS[fmt]}"
        metadata={"source_format":fmt,"source_width":None,"source_height":None,"display_width":None,"display_height":None,"display_size_bytes":None,"warnings":[]}
        display_key=None
        if fmt in {"jpeg","png","webp"}:
            with tempfile.NamedTemporaryFile(prefix="anmsm-map-display-",suffix=".webp",delete=False) as out: display=out.name
            metadata=_convert(source,display); display_key=f"{prefix}/display.webp"
        # Real-feed inspection must precede PDF rendering; retain PDF original only.
        elif fmt=="pdf":
            with open(source,"rb") as handle:
                if handle.read(5) != b"%PDF-": raise LogoImportError("invalid_pdf","Invalid PDF signature")
            metadata["warnings"]=["pdf_display_not_generated"]
        # Upload only after the isolated decoder (or PDF signature check) has
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
