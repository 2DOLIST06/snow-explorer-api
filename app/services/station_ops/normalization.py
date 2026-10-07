"""Non-destructive comparison values. Never persist these representations."""
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit

from .scan import CONTENT_FIELDS

NUMERIC_FIELDS = frozenset({"latitude", "longitude", "ski_area_km", "altitude_base_m",
                            "altitude_top_m", "altitude_min_m", "altitude_max_m",
                            "lifts_count", "pistes_count"})
INTEGER_FIELDS = NUMERIC_FIELDS - {"latitude", "longitude", "ski_area_km"}
DATE_FIELDS = frozenset({"season_open_date", "season_close_date"})
URL_FIELDS = frozenset({"website_url", "cover_image_url", "logo_url", "pistes_small_map_url",
                        "pistes_large_map_url", "snowpark_map_url"})


def number(value):
    if type(value) not in (int, float, str, Decimal) or (isinstance(value, str) and len(value) > 1024):
        raise ValueError("Expected a finite number")
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError("Expected a finite number") from exc
    if not result.is_finite():
        raise ValueError("Expected a finite number")
    if not -308 <= result.as_tuple().exponent <= 308:
        raise ValueError("Number exponent exceeds the comparison range")
    return result


def normalized_url(value):
    if not isinstance(value, str):
        raise ValueError("Expected an HTTP(S) URL")
    value = value.strip()
    if any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError("URL cannot contain whitespace")
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Expected an absolute HTTP(S) URL without credentials")
    host = parts.hostname.encode("idna").decode("ascii").lower()
    if ":" in host:
        host = "[" + host + "]"
    port = parts.port
    netloc = host + (":" + str(port) if port is not None and (scheme, port) not in {("http", 80), ("https", 443)} else "")
    # Preserve path case, query order, fragments and non-root trailing slashes.
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, parts.fragment))


def normalize(field, value):
    if value is None:
        return None
    if field in NUMERIC_FIELDS:
        result = number(value)
        if field in INTEGER_FIELDS and result != result.to_integral_value():
            raise ValueError("Expected an integer")
        if field == "latitude" and not -90 <= result <= 90:
            raise ValueError("Latitude must be between -90 and 90")
        if field == "longitude" and not -180 <= result <= 180:
            raise ValueError("Longitude must be between -180 and 180")
        return result
    if field == "is_active":
        if type(value) is bool:
            return value
        if type(value) is int and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in {"true", "false", "0", "1"}:
            return value.strip().lower() in {"true", "1"}
        raise ValueError("Expected a boolean, true/false or 0/1")
    if field in DATE_FIELDS:
        if not isinstance(value, (str, date)):
            raise ValueError("Expected an ISO date")
        return date.fromisoformat(value.strip()).isoformat() if isinstance(value, str) else value.isoformat()
    if not isinstance(value, str):
        raise ValueError("Expected a string")
    value.encode("utf-8")  # Reject malformed Unicode per candidate, not per batch.
    if field in CONTENT_FIELDS:
        return value  # Hash exact editorial text, including whitespace.
    if field in URL_FIELDS:
        if field in {"cover_image_url", "logo_url"} and value.startswith("data:image/") and "," in value:
            return value
        return normalized_url(value)
    if field == "id":
        return value  # Primary identifiers are literal, never fuzzy or trimmed.
    value = value.strip()
    if field == "country_code":
        return value.upper()
    if field == "name":
        return " ".join(value.casefold().split())
    return value


def json_normalized(value):
    if isinstance(value, Decimal):
        # JSON-safe decimal strings without rounding or exponent notation.
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, dict):
        return {key: json_normalized(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_normalized(item) for item in value]
    return value
