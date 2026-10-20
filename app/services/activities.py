import copy
import json
from html.parser import HTMLParser
from urllib.parse import urlsplit
import bleach
from app.models.activities import StationActivities
from app.models.resort import Resort

DEFAULT_BLOCK = {"enabled": False, "title": "Activités à faire", "items": []}
RICH_TAGS = {"p", "br", "h2", "h3", "h4", "strong", "em", "ul", "ol", "li", "blockquote", "a", "img"}


def url(value):
    if not isinstance(value, str) or len(value) > 8000 or any(c.isspace() or ord(c) < 32 or c in '\\<>"' for c in value):
        raise ValueError("URL invalide")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Les URL doivent être absolues et HTTPS")
    _ = parsed.port  # Reject malformed port numbers without changing the URL.
    return value  # Never normalize/re-encode affiliate query parameters.


def rich_html(value):
    return bleach.clean(value, tags=RICH_TAGS,
        attributes={"a": ["href", "title", "target", "class"], "img": ["src", "alt", "title"]},
        protocols=["https"], strip=True)


class EmbedParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.script = False
    def handle_starttag(self, tag, attrs):
        if tag not in {"div", "span", "p", "a", "img", "iframe", "script", "br", "strong", "em"}:
            raise ValueError(f"Widget incompatible : balise {tag}")
        attrs = dict(attrs)
        if tag == "script":
            if not attrs.get("src") or attrs.get("type", "text/javascript") not in {"text/javascript", "module"}:
                raise ValueError("Widget incompatible : seuls les scripts externes HTTPS sont acceptés")
            self.script = True
        for key, value in attrs.items():
            if key.lower().startswith("on") or key in {"srcdoc", "nonce", "integrity"}:
                raise ValueError(f"Widget incompatible : attribut {key}")
            if key in {"src", "href"}: url(value)
    handle_startendtag = handle_starttag
    def handle_endtag(self, tag):
        if tag == "script": self.script = False
    def handle_data(self, data):
        if self.script and data.strip():
            raise ValueError("Widget incompatible : initialisation JavaScript inline non prise en charge")


def validate_block(block):
    if not isinstance(block, dict) or set(block) - {"enabled", "title", "items"}:
        raise ValueError("Bloc invalide")
    if type(block.get("enabled")) is not bool or not isinstance(block.get("title"), str) or not 1 <= len(block["title"].strip()) <= 200:
        raise ValueError("Activation et titre (1 à 200 caractères) requis")
    items = block.get("items")
    if not isinstance(items, list) or len(items) > 50:
        raise ValueError("Un bloc accepte au maximum 50 éléments")
    result = copy.deepcopy(block)
    seen = set()
    for item in result["items"]:
        if not isinstance(item, dict) or type(item.get("enabled")) is not bool:
            raise ValueError("Élément invalide")
        ident = item.get("id")
        if not isinstance(ident, str) or not 1 <= len(ident) <= 100 or ident in seen:
            raise ValueError("Identifiants uniques requis")
        seen.add(ident)
        kind = item.get("type")
        fields = {"id", "type", "enabled"}
        if kind == "image":
            fields |= {"src", "alt", "href", "newTab"}
            url(item.get("src")); url(item.get("href"))
            if not isinstance(item.get("alt"), str) or len(item["alt"]) > 2000 or type(item.get("newTab")) is not bool:
                raise ValueError("Texte alternatif et choix d’ouverture requis")
        elif kind in {"rich", "embed"}:
            fields |= {"html"}
            if not isinstance(item.get("html"), str) or len(item["html"]) > 100000:
                raise ValueError("Contenu limité à 100 000 caractères")
            if kind == "rich": item["html"] = rich_html(item["html"])
            else:
                fields |= {"origins"}
                origins = item.get("origins", [])
                if not isinstance(origins, list) or len(origins) > 20:
                    raise ValueError("20 origines réseau maximum")
                for origin in origins:
                    url(origin)
                    if origin != 'https://' + urlsplit(origin).netloc:
                        raise ValueError("Une origine doit être de la forme https://partenaire.com")
                parser = EmbedParser(); parser.feed(item["html"]); parser.close()
        else: raise ValueError("Format inconnu")
        if set(item) - fields: raise ValueError("Champs d’élément inconnus")
    if len(json.dumps(result)) > 1000000: raise ValueError("Bloc trop volumineux")
    return result


def station(slug, lock=False):
    query = Resort.select().where(Resort.slug == slug)
    if lock and Resort._meta.database.__class__.__name__ != "SqliteDatabase": query = query.for_update()
    return query.first()


def snapshot(station_id):
    row = StationActivities.get_or_none(StationActivities.station == station_id)
    return {"revision": row.revision if row else 0,
            "block": json.loads(row.block_json) if row else copy.deepcopy(DEFAULT_BLOCK), "has_block": row is not None}


def write(station_id, block, expected):
    current = snapshot(station_id)
    if type(expected) is not int or expected != current["revision"]:
        return False
    data = json.dumps(block, ensure_ascii=False)
    if current["has_block"]:
        StationActivities.update(block_json=data, revision=expected + 1).where(StationActivities.station == station_id).execute()
    else: StationActivities.create(station=station_id, block_json=data)
    return True
