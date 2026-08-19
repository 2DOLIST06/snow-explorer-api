"""Validation, transactional replacement and serialization of ski-pass grids."""
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from app.datetime_utils import utcnow
from app.models.base import db
from app.models.resort import Resort
from app.models.ski_pass import SkiPassPeriod, SkiPassPrice, SkiPassProduct, SkiPassSeason


def _required_text(value, path, errors):
    if not isinstance(value, str) or not value.strip():
        errors.append({"path": path, "message": "champ texte obligatoire"})
        return None
    return value.strip()


def _date(value, path, errors):
    try:
        if not isinstance(value, str):
            raise ValueError
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        pass
    errors.append({"path": path, "message": "date ISO YYYY-MM-DD invalide"})
    return None


def _money(value, path, errors, required=True):
    if value is None and not required:
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError):
        errors.append({"path": path, "message": "montant positif obligatoire"})
        return None


def validate_grid(payload, resort_lookup=None):
    errors = []
    if not isinstance(payload, dict):
        return None, [{"path": "$", "message": "objet JSON obligatoire"}]
    slug = _required_text(payload.get("station_slug"), "station_slug", errors)
    season_name = _required_text(payload.get("season"), "season", errors)
    currency = payload.get("currency", "EUR")
    if not isinstance(currency, str) or len(currency.strip()) != 3:
        errors.append({"path": "currency", "message": "code devise à 3 lettres obligatoire"})
    else:
        currency = currency.strip().upper()
    source_url = payload.get("source_url")
    if source_url is not None and (not isinstance(source_url, str) or urlparse(source_url).scheme not in ("http", "https")):
        errors.append({"path": "source_url", "message": "URL HTTP(S) invalide"})

    resort_lookup = resort_lookup or (lambda value: Resort.get_or_none(Resort.slug == value))
    resort = resort_lookup(slug) if slug else None
    if slug and resort is None:
        errors.append({"path": "station_slug", "message": "station inexistante"})

    raw_periods = payload.get("periods")
    raw_passes = payload.get("passes")
    if not isinstance(raw_periods, list):
        errors.append({"path": "periods", "message": "tableau obligatoire"}); raw_periods = []
    elif not raw_periods:
        errors.append({"path": "periods", "message": "au moins une période est obligatoire"})
    if not isinstance(raw_passes, list):
        errors.append({"path": "passes", "message": "tableau obligatoire"}); raw_passes = []
    elif not raw_passes:
        errors.append({"path": "passes", "message": "au moins un forfait est obligatoire"})
    periods, period_ids = [], set()
    for i, raw in enumerate(raw_periods):
        path = f"periods[{i}]"; raw = raw if isinstance(raw, dict) else {}
        external_id = _required_text(raw.get("id"), f"{path}.id", errors)
        name = _required_text(raw.get("name"), f"{path}.name", errors)
        start = _date(raw.get("start_date"), f"{path}.start_date", errors)
        end = _date(raw.get("end_date"), f"{path}.end_date", errors)
        if external_id in period_ids: errors.append({"path": f"{path}.id", "message": "identifiant de période dupliqué"})
        if external_id: period_ids.add(external_id)
        if start and end and start > end: errors.append({"path": path, "message": "start_date doit précéder end_date"})
        periods.append({"external_id": external_id, "name": name, "start_date": start, "end_date": end, "sort_order": i})

    products, keys, price_count, product_ids = [], set(), 0, set()
    for i, raw in enumerate(raw_passes):
        path = f"passes[{i}]"; raw = raw if isinstance(raw, dict) else {}
        external_id = _required_text(raw.get("id"), f"{path}.id", errors)
        if external_id in product_ids: errors.append({"path": f"{path}.id", "message": "identifiant de forfait dupliqué"})
        if external_id: product_ids.add(external_id)
        name = _required_text(raw.get("name"), f"{path}.name", errors)
        label = _required_text(raw.get("duration_label"), f"{path}.duration_label", errors)
        duration = raw.get("duration_days")
        if duration is not None and (isinstance(duration, bool) or not isinstance(duration, int) or duration < 1):
            errors.append({"path": f"{path}.duration_days", "message": "entier positif ou null attendu"})
        raw_prices = raw.get("prices")
        if not isinstance(raw_prices, list): errors.append({"path": f"{path}.prices", "message": "tableau obligatoire"}); raw_prices = []
        prices = []
        for j, value in enumerate(raw_prices):
            ppath = f"{path}.prices[{j}]"; value = value if isinstance(value, dict) else {}
            period_id = _required_text(value.get("period_id"), f"{ppath}.period_id", errors)
            category = _required_text(value.get("category"), f"{ppath}.category", errors)
            category_label = _required_text(value.get("category_label"), f"{ppath}.category_label", errors)
            kind = value.get("price_type")
            if period_id and period_id not in period_ids: errors.append({"path": f"{ppath}.period_id", "message": "period_id inexistant"})
            key = (external_id, period_id, category)
            if key in keys: errors.append({"path": ppath, "message": "couple forfait/période/catégorie dupliqué"})
            keys.add(key)
            price = low = high = None
            if kind == "fixed":
                price = _money(value.get("price"), f"{ppath}.price", errors)
                if value.get("price_min") is not None or value.get("price_max") is not None: errors.append({"path": ppath, "message": "un tarif fixe ne contient pas de fourchette"})
            elif kind == "dynamic":
                low = _money(value.get("price_min"), f"{ppath}.price_min", errors)
                high = _money(value.get("price_max"), f"{ppath}.price_max", errors)
                if value.get("price") is not None: errors.append({"path": ppath, "message": "un tarif dynamique ne contient pas price"})
                if low is not None and high is not None and low > high: errors.append({"path": ppath, "message": "price_min doit être inférieur ou égal à price_max"})
            else: errors.append({"path": f"{ppath}.price_type", "message": "fixed ou dynamic attendu"})
            prices.append({"period_external_id": period_id, "category": category, "category_label": category_label, "price_type": kind, "price": price, "price_min": low, "price_max": high, "dynamic_label": value.get("dynamic_label"), "sort_order": j})
            price_count += 1
        products.append({"external_id": external_id, "name": name, "duration_days": duration, "duration_label": label, "sort_order": i, "prices": prices})
    if isinstance(raw_passes, list) and price_count == 0:
        errors.append({"path": "passes", "message": "au moins un tarif est obligatoire"})
    normalized = {"resort": resort, "station_slug": slug, "season": season_name, "currency": currency, "source_url": source_url, "periods": periods, "products": products, "prices_count": price_count}
    return normalized, errors


def preview(payload, resort_lookup=None):
    grid, errors = validate_grid(payload, resort_lookup)
    return {"valid": not errors, "station": grid["station_slug"] if grid else None, "season": grid["season"] if grid else None, "periods_count": len(grid["periods"]) if grid else 0, "passes_count": len(grid["products"]) if grid else 0, "prices_count": grid["prices_count"] if grid else 0, "errors": errors}


def replace_grid(payload, target_season=None):
    grid, errors = validate_grid(payload)
    if errors: return None, errors
    with db.atomic():
        season = target_season
        if season is None:
            season, _ = SkiPassSeason.get_or_create(resort=grid["resort"], season=grid["season"], defaults={"currency": grid["currency"], "source_url": grid["source_url"]})
        season.season, season.currency = grid["season"], grid["currency"]
        season.source_url, season.updated_at = grid["source_url"], utcnow()
        season.save()
        # Children are intentionally deleted inside the same transaction: any
        # later insert failure restores the complete previous grid.
        SkiPassPeriod.delete().where(SkiPassPeriod.season == season).execute()
        SkiPassProduct.delete().where(SkiPassProduct.season == season).execute()
        period_rows = {}
        for item in grid["periods"]:
            row = SkiPassPeriod.create(season=season, **item); period_rows[item["external_id"]] = row
        for item in grid["products"]:
            product_values = {key: value for key, value in item.items() if key != "prices"}
            product = SkiPassProduct.create(season=season, **product_values)
            for price in item["prices"]:
                period_id = price["period_external_id"]
                price_values = {key: value for key, value in price.items() if key != "period_external_id"}
                SkiPassPrice.create(product=product, period=period_rows[period_id], **price_values)
    return season, []


def decimal_json(value):
    return None if value is None else float(value)


def serialize_season(season, today=None):
    today = today or date.today()
    periods = sorted(list(season.periods), key=lambda x: (x.sort_order, x.id))
    products = sorted(list(season.products), key=lambda x: (x.sort_order, x.id))
    current = next((p.external_id for p in periods if p.start_date <= today <= p.end_date), None)
    return {"id": season.id, "station_slug": season.resort.slug, "season": season.season, "currency": season.currency, "source_url": season.source_url, "current_period_id": current, "periods": [{"db_id": p.id, "id": p.external_id, "name": p.name, "start_date": p.start_date.isoformat(), "end_date": p.end_date.isoformat(), "sort_order": p.sort_order} for p in periods], "passes": [{"db_id": product.id, "id": product.external_id, "name": product.name, "duration_days": product.duration_days, "duration_label": product.duration_label, "sort_order": product.sort_order, "prices": [{"id": price.id, "period_id": price.period.external_id, "period_db_id": price.period.id, "category": price.category, "category_label": price.category_label, "price_type": price.price_type, "price": decimal_json(price.price), "price_min": decimal_json(price.price_min), "price_max": decimal_json(price.price_max), "dynamic_label": price.dynamic_label, "sort_order": price.sort_order} for price in sorted(list(product.prices), key=lambda x: (x.sort_order, x.id))]} for product in products]}


def import_result(season):
    """Build an explicit result from committed rows, not from preview input."""
    grid = serialize_season(season)
    return {
        "success": True,
        "station_slug": grid["station_slug"],
        "season": grid["season"],
        "season_id": grid["id"],
        "periods_count": len(grid["periods"]),
        "passes_count": len(grid["passes"]),
        "prices_count": sum(len(product["prices"]) for product in grid["passes"]),
        "grid": grid,
    }
