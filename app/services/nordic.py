"""Optional Nordic data stored independently of existing Alpine data."""
import math
from urllib.parse import urlsplit

COUNT_FIELDS = {"pistes_count", "green", "blue", "red", "black"}
KM_FIELDS = {"ski_area_km", "classic_km", "skating_km"}
BOOL_FIELDS = {"enabled", "classic", "skating"}
URL_FIELDS = {"small_map_url", "large_map_url", "official_map_url"}
TEXT_FIELDS = {"caption"}
FIELDS = COUNT_FIELDS | KM_FIELDS | BOOL_FIELDS | URL_FIELDS | TEXT_FIELDS


def validate_nordic(data):
    if not isinstance(data, dict):
        return "Le ski nordique doit être un objet."
    for key, value in data.items():
        if key not in FIELDS:
            return f"Champ nordique inconnu : {key}"
        if key in BOOL_FIELDS:
            if type(value) is not bool:
                return f"{key} doit être un booléen."
        elif key in URL_FIELDS and value is not None:
            if not isinstance(value, str):
                return f"{key} doit être une URL http(s)."
            try:
                url = urlsplit(value)
                if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or any(c.isspace() for c in value):
                    return f"{key} doit être une URL http(s)."
            except ValueError:
                return f"{key} doit être une URL http(s)."
        elif key in TEXT_FIELDS:
            if value is not None and not isinstance(value, str):
                return f"{key} doit être un texte."
        elif value is not None:
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                return f"{key} doit être un nombre positif ou nul."
            if key in COUNT_FIELDS and type(value) is not int:
                return f"{key} doit être un entier."
    return None


def has_nordic_data(data):
    return isinstance(data, dict) and any(
        (key in COUNT_FIELDS | KM_FIELDS and type(value) in (int, float) and value > 0)
        or (key in {"classic", "skating"} and value is True)
        or (key in URL_FIELDS and isinstance(value, str) and bool(value.strip()))
        for key, value in data.items()
    )
