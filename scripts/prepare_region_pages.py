"""Run with python -m scripts.prepare_region_pages; preview is the default."""
import json

import click

from app.models.base import db
from app.services.region_pages import RegionPagePreparationError, prepare_region_pages


@click.command()
@click.option("--apply", is_flag=True, help="Insert missing pages. Requires approval before production use.")
def main(apply):
    """Prepare the six canonical editorial regions; historical auvergne is excluded."""
    try:
        with db.connection_context():
            result = prepare_region_pages(db, apply=apply)
    except RegionPagePreparationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
