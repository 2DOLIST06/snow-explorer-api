"""Prepare researched station batches without publishing any station.

The transport pins public DNS addresses, the existing isolated decoders produce
WebP files, and signed receipts bind the review to the exact prepared bytes.
"""
import copy
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unicodedata
from urllib.parse import urljoin, urlsplit

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.services import anmsm_logos, anmsm_piste_maps, s3
from app.services.resort_json import BLOCK_FIELDS, STATION_FIELDS, ValidationProblem, checksum, validate_document, validate_references
from app.models.ski_area import SkiArea

ROLES = {"cover", "logo", "piste_map"}
MAX_BATCH = 10
MAX_BYTES = 20 * 1024 * 1024
BATCH_STATION_FIELDS = {
    "id", "slug", "name", "is_active", "country_code", "region_id", "region_name", "department",
    "website_url", "latitude", "longitude", "altitude_min_m", "altitude_max_m",
    "pistes_count", "ski_area_km", "lifts_count", "season_open_date", "season_close_date",
    "description_md",
}
BATCH_BLOCK_FIELDS = {
    "pistes": {"green", "blue", "red", "black"},
    "remontees": {"tire_fesses", "telesieges", "telepheriques"},
    "snowpark": {"count"},
    "forfaits_avances": {"seasons"},
    "domaines_skiables": {"items"},
}


def fail(path, message):
    raise ValidationProblem([{"path": path, "message": message}])


def signer():
    secret = next((current_app.config.get(key) or os.getenv(key) for key in
                   ("RESORT_IMPORT_SECRET", "SECRET_KEY", "ADMIN_SESSION_SECRET")
                   if current_app.config.get(key) or os.getenv(key)), None)
    if not secret:
        raise RuntimeError("An import signing secret is required")
    return URLSafeTimedSerializer(secret, salt="station-batch-media-v1")


def signed(value):
    return signer().dumps(value)


def verified(token, kind):
    try:
        value = signer().loads(token, max_age=3600)
    except (BadSignature, SignatureExpired, TypeError):
        fail("receipt", "La préparation a expiré ou a été modifiée. Relancez l’analyse.")
    if not isinstance(value, dict) or value.get("kind") != kind:
        fail("receipt", "Type de reçu invalide.")
    return value


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", value or "")
                  .encode("ascii", "ignore").decode().lower())


def validate_batch(batch):
    if not isinstance(batch, dict) or batch.get("batch_version") != "1.0":
        fail("batch_version", "Version de lot attendue : 1.0.")
    try:
        if len(json.dumps(batch, allow_nan=False).encode("utf-8")) > 1024 * 1024:
            fail("batch", "Le lot dépasse 1 Mio.")
    except (TypeError, ValueError, RecursionError):
        fail("batch", "Le lot doit être un JSON fini et sérialisable.")
    scope = batch.get("scope")
    if not isinstance(scope, dict) or not re.fullmatch(r"[A-Z]{2}", str(scope.get("country_code", ""))):
        fail("scope.country_code", "Code pays à deux lettres majuscules requis.")
    if scope.get("discipline") not in {"alpine", "nordic", "mixed"}:
        fail("scope.discipline", "Précisez alpine, nordic ou mixed.")
    records = validate_document(batch.get("document"), bulk=True)
    if not 1 <= len(records) <= MAX_BATCH:
        fail("document.stations", "Un lot contient de 1 à 10 stations.")
    slugs = set()
    for i, record in enumerate(records):
        station = record["station"]
        extra = set(station) - BATCH_STATION_FIELDS
        if extra:
            fail(f"document.stations.{i}.station", "Champ hors périmètre : " + ", ".join(sorted(extra)))
        for block in set(record) - {"station"}:
            if block not in BATCH_BLOCK_FIELDS or set(record[block]) - BATCH_BLOCK_FIELDS[block]:
                fail(f"document.stations.{i}.{block}", "Bloc ou champ hors du périmètre de création demandé.")
        slug = station.get("slug")
        if not slug or slug in slugs:
            fail(f"document.stations.{i}.station.slug", "Slug requis et unique dans le lot.")
        slugs.add(slug)
        if station.get("country_code") != scope["country_code"]:
            fail(f"document.stations.{i}.station.country_code", "Le pays doit correspondre au périmètre du lot.")
        if station.get("is_active") is not False:
            fail(f"document.stations.{i}.station.is_active", "Les créations par lots sont préparées désactivées.")
        if any(station.get(key) for key in ("cover_image_url", "logo_url")) or any(
                record.get("pistes", {}).get(key) for key in ("small_map_url", "large_map_url", "official_map_url")):
            fail(f"document.stations.{i}", "Placez les sources des médias dans media, pour les contrôler avant import.")
    media = batch.get("media")
    if not isinstance(media, list) or len(media) != len(records) * len(ROLES):
        fail("media", "Déclarez cover, logo et piste_map pour chaque station, même si non trouvés.")
    seen = set()
    for i, asset in enumerate(media):
        if not isinstance(asset, dict):
            fail(f"media.{i}", "Objet média requis.")
        identity = (asset.get("station_slug"), asset.get("role"))
        if identity[0] not in slugs or identity[1] not in ROLES or identity in seen:
            fail(f"media.{i}", "Station/rôle inconnu ou en double.")
        seen.add(identity)
        if asset.get("status") not in {"found", "not_found"}:
            fail(f"media.{i}.status", "Le média doit être recherché : found ou not_found.")
        if asset.get("format", "file") not in {"file", "interactive"} or (asset.get("format") == "interactive" and asset["role"] != "piste_map"):
            fail(f"media.{i}.format", "Le format interactive est réservé aux plans.")
        if asset["status"] == "found" and not asset.get("source_page_url"):
            fail(f"media.{i}.source_page_url", "La page source du média est requise.")
    return records


def batch_contract():
    return {
        "batch_version": "1.0", "document_schema_version": "1.0", "max_stations": MAX_BATCH,
        "max_bytes": 1024 * 1024, "creation_only": True, "is_active": False,
        "root_fields": ["batch_version", "scope", "document", "media", "research"],
        "scope_fields": ["country_code", "massif", "discipline", "season"],
        "disciplines": ["alpine", "nordic", "mixed"],
        "station_fields": sorted(BATCH_STATION_FIELDS),
        "blocks": {key: sorted(value) for key, value in BATCH_BLOCK_FIELDS.items()},
        "description_field": "station.description_md",
        "description_policy": "Original fact-first prose, no fixed outline, no season years or piste/lift/snowpark counts; sources outside public description.",
        "legacy_price_label": "Preserved as note; never discarded.",
        "media_roles": sorted(ROLES), "media_statuses": ["found", "not_found"],
        "media_fields": ["station_slug", "role", "status", "format", "source_url", "source_page_url",
                         "source_provider", "credit", "anmsm_external_id", "anmsm_media_id"],
        "media_policy": "ANMSM first for logos and unselected FR maps. Preserve an explicitly selected map; official image links stay URLs, never PDF fallbacks. Browser modal test before confirmation.",
        "existing_ski_areas": [{"id": area.id, "slug": area.slug, "name": area.name}
                               for area in SkiArea.select().order_by(SkiArea.slug)],
        "ski_area_policy": "Link existing catalogue areas only via domaines_skiables.items [{id, slug}]; never create an area implicitly.",
        "limits": {"cover_webp_bytes": 80 * 1024, "logo_webp_bytes": 50 * 1024,
                   "piste_map_webp_bytes": 6 * 1024 * 1024},
        "unsupported": ["proof of cross-origin iframe rendering from HTTP alone", "SVG media", "automatic station publication"],
    }


def resolve_batch(batch):
    records = validate_batch(batch)
    from app.routes.admin_resort_import import _resolve
    for record in records:
        if _resolve(record):
            fail("document", f"{record['station']['slug']} existe déjà ou présente un conflit d’identité. Retirez cette entrée du lot de créations.")
        errors = validate_references(record)
        if errors:
            raise ValidationProblem(errors)
    # ANMSM is checked once per lot, including when the plugin found nothing.
    logos = anmsm_logos.fetch_stations() if batch["scope"]["country_code"] == "FR" else []
    maps = anmsm_piste_maps.fetch_maps() if batch["scope"]["country_code"] == "FR" else []
    stations = {r["station"]["slug"]: r["station"] for r in records}
    jobs, warnings = [], []
    for asset in batch["media"]:
        slug, role = asset["station_slug"], asset["role"]
        choice = copy.deepcopy(asset)
        choice["source_provider"] = "official"
        if role in {"logo", "piste_map"} and batch["scope"]["country_code"] == "FR":
            matches = [row for row in logos if normalized(row["external_name"]) == normalized(stations[slug]["name"])]
            external_id = asset.get("anmsm_external_id")
            if external_id:
                matches = [row for row in matches if row["external_station_id"].casefold() == str(external_id).casefold()]
                if not matches:
                    fail("media", f"{slug} : identité ANMSM à vérifier, aucune correspondance exacte.")
            if len(matches) > 1:
                fail("media", f"{slug} : plusieurs identités ANMSM, association à vérifier.")
            if matches:
                match = matches[0]
                if role == "logo":
                    candidates = [match["logo"]] if match["logo"].get("url") else []
                else:
                    candidates = [m for row in maps if row["external_station_id"].casefold() == match["external_station_id"].casefold() for m in row["piste_maps"]]
                # A researched explicit plan wins over the default feed choice.
                # Match its actual URL/ID, never trust source_provider supplied by a caller.
                explicit_plan = role == "piste_map" and asset["status"] == "found"
                if explicit_plan:
                    candidates = [m for m in candidates if m.get("url") == asset.get("source_url")
                                  and (not asset.get("anmsm_media_id") or str(m.get("media_id")) == str(asset["anmsm_media_id"]))]
                if len(candidates) > 1:
                    candidates = [m for m in candidates if str(m.get("media_id")) == str(asset.get("anmsm_media_id"))]
                    if len(candidates) != 1:
                        available = [m for row in maps if row["external_station_id"].casefold() == match["external_station_id"].casefold() for m in row["piste_maps"]]
                        details = "; ".join(f"{m.get('media_id')} ({m.get('title') or m.get('plan_type') or 'type inconnu'})" for m in available)
                        fail("media", f"{slug} : sélectionnez anmsm_media_id après vérification du périmètre : {details}")
                if candidates:
                    chosen = candidates[0]
                    choice.update(status="found", source_url=chosen["url"], source_provider="anmsm",
                                  format="file", anmsm_external_id=match["external_station_id"], credit=chosen.get("credit"))
            else:
                warnings.append(f"{slug} : aucune correspondance ANMSM exacte ; source de remplacement à vérifier.")
        if choice["status"] == "not_found":
            warnings.append(f"{slug} : {role} recherché mais non trouvé, champ omis.")
            continue
        if not choice.get("source_url"):
            fail("media", f"{slug} : URL directe requise pour {role}.")
        if role == "piste_map" and choice.get("source_provider") != "anmsm" and urlsplit(choice["source_url"]).path.lower().endswith(".pdf"):
            fail("media", f"{slug} : utilisez le lecteur officiel ou un lien image, pas un PDF de remplacement.")
        # Validate the structure now; DNS/network checks happen in preparation.
        public_target(choice["source_url"])
        payload = {"kind": "job", "batch_checksum": checksum(batch), **choice}
        jobs.append({**choice, "job_token": signed(payload)})
    return {"jobs": jobs, "warnings": warnings, "batch_checksum": checksum(batch)}


def public_target(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
                parsed.password or parsed.port not in (None, 443) or parsed.fragment or
                any(ord(c) <= 32 for c in url)):
            raise ValueError()
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError()
    except (ValueError, TypeError, socket.gaierror):
        fail("source_url", "Une URL HTTPS publique sans identifiants est requise.")
    return parsed, addresses[0][4][0]


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=10, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def download(url, role):
    started = time.monotonic()
    for _ in range(4):
        parsed, address = public_target(url)
        connection = PinnedHTTPSConnection(parsed.hostname, address)
        path = None
        try:
            connection.request("GET", parsed.path + ("?" + parsed.query if parsed.query else ""),
                               headers={"User-Agent": "SnowExplorer-MediaImport/1.0", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                url = urljoin(url, response.getheader("Location", ""))
                continue
            if response.status != 200:
                fail("source_url", f"Le média répond HTTP {response.status}.")
            mime = (response.getheader("Content-Type") or "").split(";")[0].lower().strip()
            allowed = {"image/jpeg", "image/png", "image/webp"}
            if role == "piste_map":
                allowed.add("application/pdf")
            if mime not in allowed:
                fail("source_url", "URL non directe ou format non accepté. Fournissez JPEG, PNG, WebP ou un PDF pour le plan.")
            digest, total = hashlib.sha256(), 0
            with tempfile.NamedTemporaryFile(prefix="station-batch-", delete=False) as output:
                path = output.name
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_BYTES or time.monotonic() - started > 25:
                        fail("source_url", "Téléchargement trop volumineux ou trop lent.")
                    digest.update(chunk)
                    output.write(chunk)
            if not total:
                fail("source_url", "Média vide.")
            return path, mime, digest.hexdigest()
        except Exception:
            if path and os.path.exists(path):
                os.unlink(path)
            raise
        finally:
            connection.close()
    fail("source_url", "Trop de redirections.")


def probe_interactive(url):
    """Reject known embedding failures; a browser review is still required."""
    for _ in range(4):
        parsed, address = public_target(url)
        connection = PinnedHTTPSConnection(parsed.hostname, address)
        try:
            connection.request("GET", parsed.path + ("?" + parsed.query if parsed.query else ""),
                               headers={"User-Agent": "SnowExplorer-MediaImport/1.0"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                url = urljoin(url, response.getheader("Location", ""))
                continue
            if response.status != 200 or "text/html" not in (response.getheader("Content-Type") or "").lower():
                fail("source_url", "Le plan interactif doit retourner une page HTML accessible.")
            if response.getheader("X-Frame-Options"):
                fail("source_url", "Le fournisseur interdit ou restreint l’intégration en iframe. Choisissez son URL embed ou un fichier de plan.")
            csp = response.getheader("Content-Security-Policy") or ""
            ancestors = re.search(r"(?:^|;)\s*frame-ancestors\s+([^;]+)", csp, re.I)
            if ancestors and ancestors.group(1).strip() != "*":
                fail("source_url", "La politique frame-ancestors restreint cette intégration. Utilisez un plan fichier ou une URL embed autorisée.")
            if not response.read(1024):
                fail("source_url", "Le lecteur de plan est vide.")
            return url
        finally:
            connection.close()
    fail("source_url", "Trop de redirections.")


def prepare_media(token):
    job = verified(token, "job")
    if job.get("format") == "interactive":
        url = probe_interactive(job["source_url"])
        value = {**job, "kind": "prepared", "url": url, "render_mode": "interactive",
                 "s3_key": None, "width": None, "height": None, "size_bytes": None}
        return {**value, "preview_url": url, "receipt": signed(value)}
    source = display = None
    try:
        source, mime, _ = download(job["source_url"], job["role"])
        linked_map = job["role"] == "piste_map" and job.get("source_provider") != "anmsm"
        if linked_map and mime == "application/pdf":
            fail("source_url", "Le lien du plan officiel doit être une image ou un lecteur, pas un PDF.")
        if job["role"] == "logo":
            with tempfile.NamedTemporaryFile(suffix=".webp", delete=False) as output:
                display = output.name
            metadata = anmsm_logos._convert_subprocess(source, display)
            width, height = metadata.get("width", 512), metadata.get("height", 512)
        else:
            role = job["role"]
            # The isolated existing decoder also accepts cover photographs.
            command = [sys.executable, "-m", "app.services.anmsm_piste_map_worker", source,
                       "--output-directory", tempfile.gettempdir(), "--max-pixels", "24000000",
                       "--max-dimension", "3000" if role == "piste_map" else "1600",
                       "--output-limit", str(6 * 1024 * 1024 if role == "piste_map" else 80 * 1024),
                       "--quality", "88", "--memory-mb", "512"]
            if mime == "application/pdf":
                command += ["--source-format", "pdf"]
            if role == "cover":
                command += ["--adaptive-photo"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
            import json
            try:
                metadata = json.loads(result.stdout)
            except ValueError:
                fail("media", "Échec du convertisseur isolé.")
            if result.returncode or not metadata.get("ok"):
                fail("media", "Conversion impossible : " + str(metadata.get("error", "decoder_failed")))
            display = metadata.get("path")
            if not isinstance(display, str) or os.path.dirname(os.path.realpath(display)) != os.path.realpath(tempfile.gettempdir()):
                fail("media", "Chemin de conversion invalide.")
            width, height = metadata["width"], metadata["height"]
        with open(display, "rb") as image:
            digest = hashlib.file_digest(image, "sha256").hexdigest()
        key = f"station-batches/{job['station_slug']}/{job['role']}/{digest}.webp"
        # Decode the official image to verify it, but retain its existing URL.
        # This also avoids duplicating plans already hosted on our server.
        url = job["source_url"] if linked_map else s3.put_file(key, display, "image/webp")
        value = {**job, "kind": "prepared", "url": url, "s3_key": key, "render_mode": "image",
                 "sha256": digest, "width": width, "height": height,
                 "size_bytes": os.path.getsize(display)}
        if linked_map:
            value.update(s3_key=None, linked_image=True)
        return {**value, "preview_url": url if linked_map else s3.preview_url(key), "receipt": signed(value)}
    finally:
        for path in {source, display} - {None}:
            if os.path.exists(path):
                os.unlink(path)


def assemble_batch(batch, receipts):
    records = validate_batch(batch)
    resolved = resolve_batch(batch)
    expected = {(j["station_slug"], j["role"]): j for j in resolved["jobs"]}
    assets = {}
    if not isinstance(receipts, list) or len(receipts) != len(expected):
        fail("receipts", "Tous les médias doivent être préparés et revus.")
    for receipt in receipts:
        value = verified(receipt, "prepared")
        identity = (value.get("station_slug"), value.get("role"))
        job = expected.get(identity)
        if (not job or identity in assets or value.get("batch_checksum") != checksum(batch)
                or value.get("source_url") != job["source_url"]):
            fail("receipts", "Le lot ou une source a changé depuis la préparation.")
        if value.get("linked_image") and (value.get("url") != job["source_url"] or job.get("source_provider") == "anmsm"):
            fail("receipts", "Le lien image a changé depuis la préparation.")
        if value.get("render_mode") != "interactive" and not value.get("linked_image") and not s3.validate_object(value["s3_key"]):
            fail("receipts", "Un média préparé n’est plus disponible.")
        assets[identity] = value
    output = {"schema_version": "1.0", "stations": copy.deepcopy(records)}
    for record in output["stations"]:
        slug = record["station"]["slug"]
        for role, field in (("cover", "cover_image_url"), ("logo", "logo_url")):
            if (slug, role) in assets:
                record["station"][field] = assets[(slug, role)]["url"]
        if (slug, "piste_map") in assets:
            plan = assets[(slug, "piste_map")]
            field = "official_map_url" if plan.get("render_mode") == "interactive" or plan.get("linked_image") else "large_map_url"
            record.setdefault("pistes", {}).update({"enabled": True, field: plan["url"]})
    validate_document(output, bulk=True)
    return output, resolved["warnings"]
