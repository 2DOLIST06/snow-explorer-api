import os

import click
from flask.cli import with_appcontext

from app.models.admin_user import AdminUser
from app.models.base import db
from app.services.admin_auth import hash_password, normalize_email, utcnow, validate_email, validate_password


def _create_admin(email, password):
    email = normalize_email(email)
    if not validate_email(email):
        raise click.ClickException("Adresse e-mail invalide.")
    error = validate_password(password)
    if error:
        raise click.ClickException("Le mot de passe doit contenir entre 12 et 1024 caractères.")
    if AdminUser.get_or_none(AdminUser.email == email):
        raise click.ClickException("Un administrateur utilise déjà cette adresse.")
    return AdminUser.create(email=email, password_hash=hash_password(password), role="admin",
                            is_active=True, created_at=utcnow(), updated_at=utcnow(),
                            password_changed_at=utcnow())


def register_admin_commands(app):
    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @with_appcontext
    def create_admin(email):
        """Create an administrator; the password is never echoed or accepted as an argument."""
        password = click.prompt("Mot de passe", hide_input=True, confirmation_prompt=True)
        user = _create_admin(email, password)
        click.echo(f"Administrateur créé: {user.email}")

    @app.cli.command("bootstrap-admin")
    @with_appcontext
    def bootstrap_admin():
        """One-time non-interactive bootstrap for constrained hosting consoles."""
        if AdminUser.select().exists():
            raise click.ClickException("Bootstrap refusé: un administrateur existe déjà.")
        email = os.environ.get("ADMIN_BOOTSTRAP_EMAIL", "")
        password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "")
        if not email or not password:
            raise click.ClickException("ADMIN_BOOTSTRAP_EMAIL et ADMIN_BOOTSTRAP_PASSWORD sont requis.")
        user = _create_admin(email, password)
        click.echo(f"Administrateur initial créé: {user.email}. Supprimez immédiatement les variables bootstrap.")
