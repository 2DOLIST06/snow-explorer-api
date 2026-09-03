"""Secure ANMSM logo ingestion. This service only creates review candidates."""
import hashlib
import io
import ipaddress
import json
import os
import re
import socket
import tempfile
import time
import warnings
from urllib.parse import urljoin, urlparse

import requests
from flask import current_app, has_app_context
from PIL import Image, ImageOps, UnidentifiedImageError
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
DEFAULT_BATCH_SIZE = 2
MAX_BATCH_SIZE = 3
ALLOWED_CONTENT_TYPES = frozenset({
    "image/jpeg", "image/png", "image/gif", "image/svg+xml",
})

class LogoImportError(Exception):
    def __init__(self, code, message):
        self.code = code; super().__init__(message)

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
        if declared and int(declared) > limit:
            response.close(); raise LogoImportError("download_too_large", "Media exceeds configured size limit")
        temporary = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
        try:
            total = 0
            for chunk in response.iter_content(64 * 1024):
                total += len(chunk)
                if total > limit: raise LogoImportError("download_too_large", "Media exceeds configured size limit")
                temporary.write(chunk)
            temporary.seek(0)
            return temporary.read()
        finally:
            response.close()
            temporary.close()
    raise LogoImportError("too_many_redirects", "Too many media redirects")

def _open_source(raw):
    source_format = None
    if raw.lstrip().startswith(b"<"):
        lowered = raw.lower()
        if (b"<!doctype" in lowered or b"<!entity" in lowered
                or re.search(br"(?:href\s*=|url\s*\()\s*['\"]?(?:https?:|//|file:)", lowered)
                or len(raw) > 2 * 1024 * 1024):
            raise LogoImportError("invalid_svg", "Unsafe SVG document")
        try:
            import cairosvg
            raw = cairosvg.svg2png(bytestring=raw)
            source_format = "svg"
        except Exception as exc:
            raise LogoImportError("invalid_svg", "SVG rasterization failed") from exc
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(raw)); image.seek(0); image.load()
    except (UnidentifiedImageError, OSError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise LogoImportError("invalid_image", "Unsupported, invalid, or oversized image") from exc
    if image.format not in {"JPEG", "PNG", "GIF"}:
        raise LogoImportError("invalid_mime", "Decoded media format is not allowed")
    return image, source_format or image.format.lower()

def optimize(raw):
    image, source_format = _open_source(raw)
    converted = cropped = None
    try:
        source_width, source_height = image.size
        if source_width * source_height > MAX_PIXELS:
            raise LogoImportError("invalid_image", "Image pixel count exceeds configured limit")
        converted = ImageOps.exif_transpose(image).convert("RGBA")
        alpha_box = converted.getchannel("A").getbbox()
        if not alpha_box: raise LogoImportError("empty_image", "Image has no visible pixels")
        cropped = converted.crop(alpha_box)
        content_width, content_height = cropped.size
        scale = min(1.0, OUTPUT_SIZE / content_width, OUTPUT_SIZE / content_height)
        quality = 82
        while True:
            width = max(1, round(content_width * scale)); height = max(1, round(content_height * scale))
            resized = cropped.resize((width, height), Image.Resampling.LANCZOS) if cropped.size != (width, height) else cropped
            canvas = Image.new("RGBA", (OUTPUT_SIZE, OUTPUT_SIZE), (0, 0, 0, 0))
            output = io.BytesIO()
            try:
                canvas.alpha_composite(resized, ((OUTPUT_SIZE-width)//2, (OUTPUT_SIZE-height)//2))
                canvas.save(output, "WEBP", quality=quality, method=6)
                encoded = output.getvalue()
            finally:
                output.close(); canvas.close()
                if resized is not cropped: resized.close()
            if len(encoded) <= OUTPUT_LIMIT: break
            if quality > 48: quality -= 6
            elif scale > 0.35: scale *= 0.88; quality = 70
            else: raise LogoImportError("optimization_limit", "No usable WebP fits within 50 KiB")
        warning_codes = []
        ratio = content_width / content_height
        if max(source_width, source_height) < 256: warning_codes.append("low_resolution")
        if ratio > 6 or ratio < 1/6: warning_codes.append("extreme_aspect_ratio")
        if width / OUTPUT_SIZE < .2 or height / OUTPUT_SIZE < .2: warning_codes.append("low_visual_occupancy")
        return encoded, {"source_format": source_format, "source_width": source_width, "source_height": source_height,
            "content_width": content_width, "content_height": content_height, "aspect_ratio": ratio,
            "visual_occupancy_width": width / OUTPUT_SIZE, "visual_occupancy_height": height / OUTPUT_SIZE,
            "optimized_width": OUTPUT_SIZE, "optimized_height": OUTPUT_SIZE, "warnings": warning_codes}
    finally:
        if cropped is not None: cropped.close()
        if converted is not None: converted.close()
        image.close()

def _records(payload):
    if isinstance(payload, list): return payload
    if isinstance(payload, dict):
        for key in ("value", "items", "results"):
            if isinstance(payload.get(key), list): return payload[key]
    raise LogoImportError("invalid_feed", "ANMSM feed does not contain a record list")

def parse_station(record):
    """Parse the Tourinsoft station fields used by every ANMSM workflow."""
    external_id = str(record.get("SyndicObjectID") or "").strip()
    external_name = str(record.get("SyndicObjectName") or "").strip()
    logo = record.get("LOGO")
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
    with db.atomic():
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


def _process_one(mapping, station, stats, session):
    external_id = station["external_station_id"]
    logo = station["logo"]
    if not isinstance(logo.get("url"), str) or not logo["url"].strip():
        stats["stations_without_logo"] += 1
        return {"external_station_id": external_id, "ok": True, "status": "without_logo"}

    raw = encoded = None
    stage = "download"
    try:
        raw = download(logo["url"], session)
        checksum = hashlib.sha256(raw).hexdigest()
        existing = StationLogoCandidate.get_or_none(
            (StationLogoCandidate.station == mapping.station_id) &
            (StationLogoCandidate.source_checksum == checksum))
        if existing is not None and existing.status != "error":
            stats["logos_unchanged"] += 1
            return {"external_station_id": external_id, "ok": True, "status": "unchanged"}

        stage = "conversion"
        encoded, metadata = optimize(raw)
        stats["conversions_succeeded"] += 1
        safe_station_id = secure_filename(str(mapping.station_id)) or hashlib.sha256(
            str(mapping.station_id).encode()).hexdigest()[:24]
        key = f"station-logos/candidates/{safe_station_id}/{checksum}.webp"
        stage = "s3"
        url = s3.put_webp(key, encoded)
        stage = "database"
        with db.atomic():
            prior = (StationLogoCandidate.select()
                     .where((StationLogoCandidate.station == mapping.station_id) &
                            (StationLogoCandidate.source_checksum != checksum))
                     .order_by(StationLogoCandidate.detected_at.desc()).first())
            values = dict(
                external_station_id=external_id, anmsm_media_id=logo.get("media_id"),
                anmsm_title=logo.get("title"), anmsm_credit=logo.get("credit"),
                source_url=logo["url"], source_size_bytes=len(raw), optimized_s3_key=key,
                optimized_url=url, optimized_size_bytes=len(encoded), status="pending",
                warnings=json.dumps(metadata.pop("warnings")), error_code=None,
                error_message=None, checked_at=utcnow(), updated_at=utcnow(), **metadata)
            if existing is None:
                StationLogoCandidate.create(
                    station=mapping.station_id, source_checksum=checksum, **values)
            else:
                for name, value in values.items(): setattr(existing, name, value)
                existing.save()
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
        checksum = hashlib.sha256(raw or logo["url"].encode()).hexdigest()
        try:
            _error_candidate(mapping, station, checksum, raw, code, str(exc))
        except Exception:
            current_app.logger.exception("Could not persist ANMSM logo failure for %s", external_id)
        return {"external_station_id": external_id, "ok": False,
                "error_code": code, "error_message": str(exc)[:1000]}
    finally:
        raw = None
        encoded = None


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
    by_external_id = ({item["external_station_id"]: item for item in fetch_stations(session)}
                      if mappings else {})
    results = []
    for mapping in mappings:
        station = by_external_id.get(mapping.external_station_id)
        if station is None:
            stats["errors"] += 1
            results.append({"external_station_id": mapping.external_station_id, "ok": False,
                            "error_code": "station_not_in_feed",
                            "error_message": "La station vérifiée est absente du flux ANMSM."})
            continue
        results.append(_process_one(mapping, station, stats, session))
    stats["duration_seconds"] = round(time.monotonic() - started, 3)
    processed_ids = [mapping.external_station_id for mapping in mappings]
    return {"batch": {"processed": len(mappings), "total_matched": total_matched,
                       "current_cursor": cursor,
                       "next_cursor": processed_ids[-1] if has_more and processed_ids else None,
                       "has_more": has_more,
                       "processed_external_station_ids": processed_ids},
            "stats": stats, "results": results}
