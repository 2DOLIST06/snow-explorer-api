"""Secure ANMSM logo ingestion. This service only creates review candidates."""
import hashlib
import ipaddress
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urljoin, urlparse

import requests
from flask import current_app, has_app_context
from werkzeug.utils import secure_filename

from app.datetime_utils import utcnow
from app.models.anmsm_station_mapping import AnmsmStationMapping
from app.models.base import db
from app.models.station_logo_candidate import StationLogoCandidate
from app.services import s3

FEED_URL = "https://api-v3.tourinsoft.com/api/syndications/anmsm.tourinsoft.com/343718C6-9088-4732-AA05-26695D1E3059?refreshCache=0&format=json"
ALLOWED_MEDIA_HOSTS = frozenset({"anmsm.media.tourinsoft.eu"})
MAX_PIXELS = 40_000_000
OUTPUT_SIZE = 512
OUTPUT_LIMIT = 50 * 1024
DEFAULT_BATCH_SIZE = 1
MAX_BATCH_SIZE = 1
ALLOWED_CONTENT_TYPES = frozenset({
    "image/jpeg", "image/png", "image/gif",
})

class LogoImportError(Exception):
    def __init__(self, code, message):
        self.code = code; super().__init__(message)


def _event(name, external_station_id, **fields):
    payload = {"event": name, "external_station_id": external_station_id, **fields}
    current_app.logger.info("anmsm_logo %s", json.dumps(payload, separators=(",", ":")))
    for handler in current_app.logger.handlers:
        try: handler.flush()
        except Exception: pass

def _assert_public_https(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_MEDIA_HOSTS or parsed.username or parsed.password:
        raise LogoImportError("unsafe_url", "Media URL must use HTTPS on an allowed ANMSM host")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise LogoImportError("dns_failure", "Media host could not be resolved") from exc
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise LogoImportError("ssrf_blocked", "Media host resolved to a non-public address")

def _timeout(name, default):
    configured = current_app.config.get(name) if has_app_context() else None
    return float(configured or os.getenv(name, str(default)))


def download(url, session=requests):
    """Stream a response to disk and return ``(path, bytes, mime)``."""
    configured = current_app.config.get("ANMSM_LOGO_MAX_DOWNLOAD_BYTES") if has_app_context() else None
    limit = int(configured or os.getenv("ANMSM_LOGO_MAX_DOWNLOAD_BYTES", str(10 * 1024 * 1024)))
    current = url
    for _ in range(4):
        _assert_public_https(current)
        try:
            response = session.get(
                current, stream=True, allow_redirects=False,
                timeout=(_timeout("ANMSM_CONNECT_TIMEOUT", 3.05),
                         _timeout("ANMSM_MEDIA_READ_TIMEOUT", 10)),
            )
        except requests.Timeout as exc:
            raise LogoImportError(
                "source_download_timeout",
                "Le téléchargement du logo a dépassé le délai autorisé.",
            ) from exc
        if response.status_code in {301, 302, 303, 307, 308}:
            current = urljoin(current, response.headers.get("Location", "")); response.close(); continue
        if response.status_code != 200:
            response.close(); raise LogoImportError("download_http_error", f"Media returned HTTP {response.status_code}")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type not in ALLOWED_CONTENT_TYPES:
            response.close(); raise LogoImportError("invalid_mime", "Media Content-Type is not allowed")
        declared = response.headers.get("Content-Length")
        try: declared_size = int(declared) if declared else None
        except (TypeError, ValueError): declared_size = None
        if declared_size is not None and declared_size > limit:
            response.close(); raise LogoImportError("download_too_large", "Media exceeds configured size limit")
        temporary = tempfile.NamedTemporaryFile(prefix="anmsm-source-", delete=False)
        path = temporary.name
        try:
            total = 0
            for chunk in response.iter_content(64 * 1024):
                if not chunk: continue
                total += len(chunk)
                if total > limit: raise LogoImportError("download_too_large", "Media exceeds configured size limit")
                temporary.write(chunk)
            temporary.flush()
            return path, total, content_type
        except requests.Timeout as exc:
            try: os.unlink(path)
            except FileNotFoundError: pass
            raise LogoImportError("source_download_timeout",
                                  "Le téléchargement du logo a dépassé le délai autorisé.") from exc
        except Exception:
            try: os.unlink(path)
            except FileNotFoundError: pass
            raise
        finally:
            response.close()
            temporary.close()
    raise LogoImportError("too_many_redirects", "Too many media redirects")

def _convert_subprocess(source_path, output_path):
    timeout = _timeout("ANMSM_CONVERSION_TIMEOUT", 15)
    memory = int(_timeout("ANMSM_CONVERSION_MEMORY_MB", 256))
    command = [sys.executable, "-m", "app.services.anmsm_image_worker", source_path, output_path,
               "--max-pixels", str(MAX_PIXELS), "--size", str(OUTPUT_SIZE),
               "--output-limit", str(OUTPUT_LIMIT), "--memory-mb", str(memory)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise LogoImportError("conversion_timeout", "La conversion a dépassé le délai autorisé.") from exc
    if result.returncode < 0:
        raise LogoImportError("conversion_interrupted", f"Conversion interrompue par le signal {-result.returncode}.")
    try: payload = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise LogoImportError("conversion_interrupted", "Le convertisseur n'a pas retourné de résultat valide.") from exc
    if result.returncode or not payload.get("ok"):
        code = payload.get("error", "conversion_interrupted")
        if code not in {"unsupported_format", "excessive_dimensions", "invalid_image",
                        "empty_image", "optimization_limit"}: code = "conversion_interrupted"
        raise LogoImportError(code, "La source ne peut pas être convertie en logo sûr.")
    metadata = payload.get("metadata") or {}
    size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
    try:
        with open(output_path, "rb") as converted:
            header = converted.read(12)
    except OSError as exc:
        raise LogoImportError("conversion_interrupted", "Le fichier converti est absent.") from exc
    if (size <= 0 or size > OUTPUT_LIMIT or metadata.get("optimized_size_bytes") != size
            or len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WEBP"):
        raise LogoImportError("conversion_interrupted", "Le fichier converti est invalide.")
    return metadata


def optimize(raw):
    """Compatibility helper; Pillow still runs only in the isolated child."""
    source = output = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            source = handle.name; handle.write(raw)
        with tempfile.NamedTemporaryFile(prefix="anmsm-output-", suffix=".webp", delete=False) as handle:
            output = handle.name
        metadata = _convert_subprocess(source, output)
        with open(output, "rb") as handle: return handle.read(), metadata
    finally:
        for path in (source, output):
            if path:
                try: os.unlink(path)
                except FileNotFoundError: pass

def _records(payload):
    if isinstance(payload, list): return payload
    if isinstance(payload, dict):
        for key in ("value", "items", "results"):
            if isinstance(payload.get(key), list): return payload[key]
    raise LogoImportError("invalid_feed", "ANMSM feed does not contain a record list")

def parse_station(record):
    """Parse the Tourinsoft station fields used by every ANMSM workflow."""
    external_id = str(record.get("SyndicObjectID") or "").strip()
    # Tourinsoft puts the syndicated fields in ``Object``.  Keep the legacy
    # root fallback because old exports and callers of this shared parser used
    # to pass the unwrapped Object value directly.
    fields = record.get("Object")
    if not isinstance(fields, dict):
        fields = record
    external_name = str(fields.get("NOM") or record.get("SyndicObjectName") or "").strip()
    logo = fields.get("LOGO")
    if isinstance(logo, list):
        logo = logo[0] if logo else None
    if not isinstance(logo, dict):
        logo = None
    return {
        "external_station_id": external_id,
        "external_name": external_name,
        "logo": {
            "url": logo.get("Url") if logo else None,
            "title": logo.get("Titre") if logo else None,
            "credit": logo.get("Credit") if logo else None,
            "media_id": logo.get("MediaID") if logo else None,
        },
    }

def fetch_stations(session=requests):
    configured = current_app.config.get("ANMSM_STATIONS_FEED_URL") if has_app_context() else None
    feed_url = configured or os.getenv("ANMSM_STATIONS_FEED_URL", FEED_URL)
    if "refreshCache=1" in feed_url or "refreshCache=2" in feed_url:
        raise LogoImportError("unsafe_feed_configuration", "Only refreshCache=0 is permitted")
    try:
        response = session.get(
            feed_url,
            timeout=(_timeout("ANMSM_CONNECT_TIMEOUT", 3.05),
                     _timeout("ANMSM_FEED_READ_TIMEOUT", 10)),
        )
        response.raise_for_status()
        return [station for station in map(parse_station, _records(response.json()))
                if station["external_station_id"]]
    except requests.Timeout as exc:
        raise LogoImportError("source_feed_timeout", "ANMSM feed request timed out") from exc
    finally:
        if "response" in locals(): response.close()

def _error_candidate(mapping, station, checksum, raw, code, message):
    """Persist one failure without changing an already usable candidate."""
    logo = station["logo"]
    with StationLogoCandidate._meta.database.atomic():
        candidate = StationLogoCandidate.get_or_none(
            (StationLogoCandidate.station == mapping.station_id) &
            (StationLogoCandidate.source_checksum == checksum))
        if candidate is None:
            candidate = StationLogoCandidate.create(
                station=mapping.station_id, external_station_id=station["external_station_id"],
                anmsm_media_id=logo.get("media_id"), anmsm_title=logo.get("title"),
                anmsm_credit=logo.get("credit"), source_url=logo["url"],
                source_checksum=checksum, source_format="unknown", source_width=0,
                source_height=0, source_size_bytes=len(raw or b""), status="error",
                error_code=code, error_message=message[:1000], checked_at=utcnow())
        elif candidate.status == "error":
            candidate.error_code = code; candidate.error_message = message[:1000]
            candidate.checked_at = utcnow(); candidate.updated_at = utcnow(); candidate.save()
    return candidate


def _milliseconds(started):
    return round((time.monotonic() - started) * 1000)


def _log_station_timing(station, timings, total_started):
    current_app.logger.info(
        "ANMSM logo sync station=%s name=%s fetch_feed_ms=%s download_ms=%s "
        "conversion_ms=%s s3_ms=%s total_ms=%s",
        station["external_station_id"], station.get("external_name") or "",
        timings["fetch_feed_ms"], timings["download_ms"],
        timings["conversion_ms"], timings["s3_ms"], _milliseconds(total_started),
    )


def _process_one(mapping, station, stats, session, fetch_feed_ms=0):
    external_id = station["external_station_id"]
    logo = station["logo"]
    total_started = time.monotonic()
    timings = {"fetch_feed_ms": fetch_feed_ms, "download_ms": 0,
               "conversion_ms": 0, "s3_ms": 0}
    source_path = output_path = None
    source_size = 0
    checksum = hashlib.sha256(logo.get("url", "").encode()).hexdigest()
    stage = "download"
    try:
        if not isinstance(logo.get("url"), str) or not logo["url"].strip():
            stats["stations_without_logo"] += 1
            return {"external_station_id": external_id, "ok": True, "status": "without_logo"}

        _event("download_started", external_id)
        stage_started = time.monotonic()
        downloaded = download(logo["url"], session)
        if isinstance(downloaded, tuple):
            source_path, source_size, content_type = downloaded
        else:  # retained for callers that provide a legacy test adapter
            with tempfile.NamedTemporaryFile(prefix="anmsm-source-", delete=False) as handle:
                source_path = handle.name; handle.write(downloaded)
            source_size, content_type = len(downloaded), "application/octet-stream"
        timings["download_ms"] = _milliseconds(stage_started)
        _event("download_completed", external_id, bytes=source_size, mime_type=content_type)
        digest = hashlib.sha256()
        with open(source_path, "rb") as source:
            for chunk in iter(lambda: source.read(64 * 1024), b""): digest.update(chunk)
        checksum = digest.hexdigest()
        existing = StationLogoCandidate.get_or_none(
            (StationLogoCandidate.station == mapping.station_id) &
            (StationLogoCandidate.source_checksum == checksum))
        if existing is not None and existing.status != "error":
            stats["logos_unchanged"] += 1
            return {"external_station_id": external_id, "ok": True, "status": "unchanged"}

        stage = "conversion"
        stage_started = time.monotonic()
        with tempfile.NamedTemporaryFile(prefix="anmsm-output-", suffix=".webp", delete=False) as handle:
            output_path = handle.name
        _event("conversion_started", external_id, format=content_type, width=None, height=None)
        metadata = _convert_subprocess(source_path, output_path)
        timings["conversion_ms"] = _milliseconds(stage_started)
        _event("conversion_completed", external_id, format=metadata["source_format"],
               width=metadata["source_width"], height=metadata["source_height"])
        stats["conversions_succeeded"] += 1
        safe_station_id = secure_filename(str(mapping.station_id)) or hashlib.sha256(
            str(mapping.station_id).encode()).hexdigest()[:24]
        key = f"station-logos/candidates/{safe_station_id}/{checksum}.webp"
        stage = "s3"
        stage_started = time.monotonic()
        _event("s3_upload_started", external_id)
        with open(output_path, "rb") as optimized_file:
            url = s3.put_webp(key, optimized_file)
        timings["s3_ms"] = _milliseconds(stage_started)
        _event("s3_upload_completed", external_id)
        stage = "database"
        with StationLogoCandidate._meta.database.atomic():
            prior = (StationLogoCandidate.select()
                     .where((StationLogoCandidate.station == mapping.station_id) &
                            (StationLogoCandidate.source_checksum != checksum))
                     .order_by(StationLogoCandidate.detected_at.desc()).first())
            values = dict(
                external_station_id=external_id, anmsm_media_id=logo.get("media_id"),
                anmsm_title=logo.get("title"), anmsm_credit=logo.get("credit"),
                source_url=logo["url"], source_size_bytes=source_size, optimized_s3_key=key,
                optimized_url=url, optimized_size_bytes=metadata.pop("optimized_size_bytes"), status="pending",
                warnings=json.dumps(metadata.pop("warnings")), error_code=None,
                error_message=None, checked_at=utcnow(), updated_at=utcnow(), **metadata)
            if existing is None:
                StationLogoCandidate.create(
                    station=mapping.station_id, source_checksum=checksum, **values)
            else:
                for name, value in values.items(): setattr(existing, name, value)
                existing.save()
        _event("database_update_completed", external_id)
        stats["logos_updated" if prior or existing else "logos_created"] += 1
        return {"external_station_id": external_id, "ok": True,
                "status": "updated" if prior or existing else "created"}
    except Exception as exc:
        stats["errors"] += 1
        if isinstance(exc, LogoImportError):
            code = exc.code
        elif stage == "conversion":
            code = "logo_conversion_error"
        elif stage == "s3":
            code = "s3_upload_error"
        elif stage == "download":
            code = "source_download_error"
        else:
            code = "processing_error"
        try:
            _error_candidate(mapping, station, checksum, None, code, str(exc))
        except Exception:
            current_app.logger.exception("Could not persist ANMSM logo failure for %s", external_id)
        return {"external_station_id": external_id, "ok": False,
                "error_code": code, "error_message": str(exc)[:1000]}
    finally:
        # Also emit measurements for failed stages; zero means the stage was
        # not reached, while a failure in progress records its elapsed time.
        if stage + "_ms" in timings and timings[stage + "_ms"] == 0 and "stage_started" in locals():
            timings[stage + "_ms"] = _milliseconds(stage_started)
        _log_station_timing(station, timings, total_started)
        for path in (source_path, output_path):
            if path:
                try: os.unlink(path)
                except FileNotFoundError: pass


def sync(cursor=None, batch_size=DEFAULT_BATCH_SIZE, session=requests):
    """Process one stable, bounded page of verified mappings."""
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    started = time.monotonic()
    stats = {key: 0 for key in ("logos_created", "logos_updated", "logos_unchanged", "stations_without_logo",
        "conversions_succeeded", "errors")}
    base = (AnmsmStationMapping.select()
            .where((AnmsmStationMapping.source == "anmsm") &
                   AnmsmStationMapping.verified &
                   AnmsmStationMapping.station.is_null(False)))
    total_matched = base.count()
    query = base
    if cursor is not None:
        query = query.where(AnmsmStationMapping.external_station_id > cursor)
    mappings = list(query.order_by(AnmsmStationMapping.external_station_id).limit(batch_size + 1))
    has_more = len(mappings) > batch_size
    mappings = mappings[:batch_size]
    feed_started = time.monotonic()
    feed = fetch_stations(session) if mappings else []
    if mappings:
        from app.services.anmsm_snapshots import record_logo_snapshot
        record_logo_snapshot(feed)
    by_external_id = {item["external_station_id"]: item for item in feed}
    fetch_feed_ms = _milliseconds(feed_started) if mappings else 0
    results = []
    for mapping in mappings:
        station = by_external_id.get(mapping.external_station_id)
        if station is None:
            missing_station = {"external_station_id": mapping.external_station_id,
                               "external_name": mapping.station.name}
            timing_started = time.monotonic()
            stats["errors"] += 1
            results.append({"external_station_id": mapping.external_station_id, "ok": False,
                            "error_code": "station_not_in_feed",
                            "error_message": "La station vérifiée est absente du flux ANMSM."})
            _log_station_timing(missing_station, {"fetch_feed_ms": fetch_feed_ms,
                "download_ms": 0, "conversion_ms": 0, "s3_ms": 0}, timing_started)
            continue
        results.append(_process_one(mapping, station, stats, session, fetch_feed_ms))
    stats["duration_seconds"] = round(time.monotonic() - started, 3)
    processed_ids = [mapping.external_station_id for mapping in mappings]
    return {"batch": {"processed": len(mappings), "total_matched": total_matched,
                       "current_cursor": cursor,
                       "next_cursor": processed_ids[-1] if has_more and processed_ids else None,
                       "has_more": has_more,
                       "processed_external_station_ids": processed_ids},
            "stats": stats, "results": results}


def prepare(external_station_id, session=requests):
    """Prepare exactly one feed station and return its persisted candidate."""
    _event("prepare_started", external_station_id)
    station = next((item for item in fetch_stations(session)
                    if item["external_station_id"] == external_station_id), None)
    if station is None:
        raise LogoImportError("unknown_external_station", "Station ANMSM introuvable.")
    mapping = AnmsmStationMapping.get_or_none(
        (AnmsmStationMapping.source == "anmsm") &
        (AnmsmStationMapping.external_station_id == external_station_id) &
        AnmsmStationMapping.verified & AnmsmStationMapping.station.is_null(False))
    if mapping is None:
        raise LogoImportError("station_not_mapped", "La station ANMSM doit être associée.")
    _event("source_resolved", external_station_id)
    logo = station["logo"]
    if not logo.get("url"):
        raise LogoImportError("source_logo_missing", "Aucun logo source n'est disponible.")

    # Tourinsoft media identity and URL are stable. This fast path is what lets
    # an interrupted browser job resume without downloading an existing logo.
    existing_query = StationLogoCandidate.select().where(
        (StationLogoCandidate.station == mapping.station_id) &
        (StationLogoCandidate.external_station_id == external_station_id) &
        (StationLogoCandidate.source_url == logo["url"]) &
        (StationLogoCandidate.status != "error"))
    if logo.get("media_id"):
        existing_query = existing_query.where(
            StationLogoCandidate.anmsm_media_id == logo["media_id"])
    existing = existing_query.order_by(StationLogoCandidate.updated_at.desc()).first()
    if existing and existing.optimized_s3_key:
        _event("prepare_completed", external_station_id, unchanged=True)
        return existing, True

    stats = {key: 0 for key in ("logos_created", "logos_updated", "logos_unchanged",
        "stations_without_logo", "conversions_succeeded", "errors")}
    outcome = _process_one(mapping, station, stats, session)
    candidate = (StationLogoCandidate.select().where(
        (StationLogoCandidate.station == mapping.station_id) &
        (StationLogoCandidate.external_station_id == external_station_id))
        .order_by(StationLogoCandidate.updated_at.desc()).first())
    if not outcome["ok"]:
        raise LogoImportError(outcome["error_code"], outcome["error_message"])
    _event("prepare_completed", external_station_id, unchanged=outcome["status"] == "unchanged")
    return candidate, outcome["status"] == "unchanged"
