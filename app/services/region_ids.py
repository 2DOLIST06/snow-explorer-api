"""Canonical region identifiers and compatibility aliases."""

PACA_REGION_ID = "provence-alpes-cote-d-azur"

_REGION_ID_ALIASES = {
    # Historical identifier used before the missing hyphen was corrected.
    "provence-alpes-cote-dazur": PACA_REGION_ID,
}


def canonical_region_id(value):
    """Return the public identifier for a possibly historical database value."""
    normalized = (value or "").strip().lower()
    return _REGION_ID_ALIASES.get(normalized, normalized)


def region_id_variants(value):
    """Return every stored identifier that represents the requested region."""
    canonical = canonical_region_id(value)
    aliases = [
        alias
        for alias, alias_canonical in _REGION_ID_ALIASES.items()
        if alias_canonical == canonical
    ]
    return [canonical, *aliases]
