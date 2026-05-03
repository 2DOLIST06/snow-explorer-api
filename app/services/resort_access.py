from flask import abort
from app.models.resort import Resort


def get_public_active_resort_or_404(slug: str):
    resort = Resort.get_or_none((Resort.slug == slug) & (Resort.is_active == True))
    if not resort:
        abort(404, "Not found")
    return resort
