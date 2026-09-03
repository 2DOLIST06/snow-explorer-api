"""Secure ANMSM logo ingestion. This service only creates review candidates."""
import hashlib
import io
import ipaddress
import json
import os
import re
import socket
import tempfile
import warnings
from urllib.parse import urljoin, urlparse

import requests
from flask import current_app, has_app_context
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.utils import secure_filename

from app.datetime_utils import utcnow
from app.models.anmsm_station_mapping import AnmsmStationMapping
from app.models.station_logo_candidate import StationLogoCandidate
from app.services import s3

FEED_URL = "https://api-v3.tourinsoft.com/api/syndications/anmsm.tourinsoft.com/343718C6-9088-4732-AA05-26695D1E3059?refreshCache=0&format=json"
ALLOWED_MEDIA_HOSTS = frozenset({"anmsm.media.tourinsoft.eu"})
MAX_PIXELS = 40_000_000
OUTPUT_SIZE = 512
OUTPUT_LIMIT = 50 * 1024

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

def download(url, session=requests):
    configured = current_app.config.get("ANMSM_LOGO_MAX_DOWNLOAD_BYTES") if has_app_context() else None
    limit = int(configured or os.getenv("ANMSM_LOGO_MAX_DOWNLOAD_BYTES", str(10 * 1024 * 1024)))
    current = url
    for _ in range(4):
        _assert_public_https(current)
        response = session.get(current, stream=True, allow_redirects=False, timeout=(3.05, 15))
        if response.status_code in {301, 302, 303, 307, 308}:
            current = urljoin(current, response.headers.get("Location", "")); response.close(); continue
        if response.status_code != 200:
            response.close(); raise LogoImportError("download_http_error", f"Media returned HTTP {response.status_code}")
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
    source_width, source_height = image.size
    image = ImageOps.exif_transpose(image).convert("RGBA")
    alpha_box = image.getchannel("A").getbbox()
    if not alpha_box: raise LogoImportError("empty_image", "Image has no visible pixels")
    image = image.crop(alpha_box)
    content_width, content_height = image.size
    scale = min(1.0, OUTPUT_SIZE / content_width, OUTPUT_SIZE / content_height)
    quality = 82
    while True:
        width = max(1, round(content_width * scale)); height = max(1, round(content_height * scale))
        resized = image.resize((width, height), Image.Resampling.LANCZOS) if image.size != (width, height) else image
        canvas = Image.new("RGBA", (OUTPUT_SIZE, OUTPUT_SIZE), (0, 0, 0, 0))
        canvas.alpha_composite(resized, ((OUTPUT_SIZE-width)//2, (OUTPUT_SIZE-height)//2))
        output = io.BytesIO(); canvas.save(output, "WEBP", quality=quality, method=6)
        encoded = output.getvalue()
        if len(encoded) <= OUTPUT_LIMIT: break
        if quality > 48: quality -= 6
        elif scale > 0.35: scale *= 0.88; quality = 70
        else: raise LogoImportError("optimization_limit", "No usable WebP fits within 50 KiB")
    warnings = []
    ratio = content_width / content_height
    if max(source_width, source_height) < 256: warnings.append("low_resolution")
    if ratio > 6 or ratio < 1/6: warnings.append("extreme_aspect_ratio")
    if width / OUTPUT_SIZE < .2 or height / OUTPUT_SIZE < .2: warnings.append("low_visual_occupancy")
    return encoded, {"source_format": source_format, "source_width": source_width, "source_height": source_height,
        "content_width": content_width, "content_height": content_height, "aspect_ratio": ratio,
        "visual_occupancy_width": width / OUTPUT_SIZE, "visual_occupancy_height": height / OUTPUT_SIZE,
        "optimized_width": OUTPUT_SIZE, "optimized_height": OUTPUT_SIZE, "warnings": warnings}

def _records(payload):
    if isinstance(payload, list): return payload
    if isinstance(payload, dict):
        for key in ("value", "items", "results"):
            if isinstance(payload.get(key), list): return payload[key]
    raise LogoImportError("invalid_feed", "ANMSM feed does not contain a record list")

def sync(session=requests):
    configured = current_app.config.get("ANMSM_STATIONS_FEED_URL") if has_app_context() else None
    feed_url = configured or os.getenv("ANMSM_STATIONS_FEED_URL", FEED_URL)
    if "refreshCache=1" in feed_url or "refreshCache=2" in feed_url:
        raise LogoImportError("unsafe_feed_configuration", "Only refreshCache=0 is permitted")
    response = session.get(feed_url, timeout=(5, 30)); response.raise_for_status()
    stats = {"created": 0, "unchanged": 0, "unmatched": 0, "errors": 0}
    for record in _records(response.json()):
        external_id = str(record.get("SyndicObjectID") or "").strip(); logo = record.get("LOGO")
        if isinstance(logo, list): logo = logo[0] if logo else None
        if not external_id or not isinstance(logo, dict) or not isinstance(logo.get("Url"), str): continue
        mapping, _ = AnmsmStationMapping.get_or_create(source="anmsm", external_station_id=external_id)
        if not mapping.station_id or not mapping.verified:
            stats["unmatched"] += 1; continue
        raw = None
        try:
            raw = download(logo["Url"], session); checksum = hashlib.sha256(raw).hexdigest()
            if StationLogoCandidate.get_or_none((StationLogoCandidate.station == mapping.station_id) & (StationLogoCandidate.source_checksum == checksum)):
                stats["unchanged"] += 1; continue
            encoded, metadata = optimize(raw)
            prior = (StationLogoCandidate.select().where(StationLogoCandidate.station == mapping.station_id)
                     .order_by(StationLogoCandidate.detected_at.desc()).first())
            safe_station_id = secure_filename(str(mapping.station_id)) or hashlib.sha256(str(mapping.station_id).encode()).hexdigest()[:24]
            key = f"station-logos/candidates/{safe_station_id}/{checksum}.webp"
            url = s3.put_webp(key, encoded)
            StationLogoCandidate.create(station=mapping.station_id, external_station_id=external_id,
                anmsm_media_id=logo.get("MediaID"), anmsm_title=logo.get("Titre"), anmsm_credit=logo.get("Credit"),
                source_url=logo["Url"], source_checksum=checksum, source_size_bytes=len(raw), optimized_s3_key=key,
                optimized_url=url, optimized_size_bytes=len(encoded), status="updated" if prior else "pending",
                warnings=json.dumps(metadata.pop("warnings")), **metadata)
            stats["created"] += 1
        except Exception as exc:
            stats["errors"] += 1
            code = exc.code if isinstance(exc, LogoImportError) else "processing_error"
            error_checksum = hashlib.sha256(raw or logo["Url"].encode()).hexdigest()
            candidate, _ = StationLogoCandidate.get_or_create(
                station=mapping.station_id, source_checksum=error_checksum,
                defaults={"external_station_id": external_id, "anmsm_media_id": logo.get("MediaID"),
                    "anmsm_title": logo.get("Titre"), "anmsm_credit": logo.get("Credit"),
                    "source_url": logo["Url"], "source_format": "unknown", "source_width": 0,
                    "source_height": 0, "source_size_bytes": len(raw or b""), "status": "error"})
            candidate.status = "error"; candidate.error_code = code
            candidate.error_message = str(exc)[:1000]; candidate.checked_at = utcnow(); candidate.updated_at = utcnow()
            candidate.save()
    return stats
