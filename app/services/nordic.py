"""Optional Nordic data stored independently of existing Alpine data."""
import math

COUNT_FIELDS = {"pistes_count", "green", "blue", "red", "black"}
KM_FIELDS = {"ski_area_km", "classic_km", "skating_km"}
BOOL_FIELDS = {"enabled", "classic", "skating"}
FIELDS = COUNT_FIELDS | KM_FIELDS | BOOL_FIELDS


def validate_nordic(data):
    if not isinstance(data, dict):
        return "Le ski nordique doit être un objet."
    for key, value in data.items():
        if key not in FIELDS:
            return f"Champ nordique inconnu : {key}"
        if key in BOOL_FIELDS:
            if type(value) is not bool:
                return f"{key} doit être un booléen."
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
        for key, value in data.items()
    )
