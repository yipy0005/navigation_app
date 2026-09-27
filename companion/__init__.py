"""Perugia Companion: a calm, age-friendly travel companion (Flask app factory)."""

from __future__ import annotations

import logging
import os
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix


def _secret_key(instance: Path) -> str:
    """SECRET_KEY must stay the same across restarts: each phone's settings are
    stored in a cookie signed with it. In production, set it as an env var."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    key_file = instance / "secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    instance.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    key_file.write_text(key)
    key_file.chmod(0o600)
    return key


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    production = os.environ.get("COMPANION_ENV") == "production"
    app.config.update(
        SECRET_KEY=_secret_key(Path(app.instance_path)),
        SESSION_COOKIE_NAME="companion",
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=production,
        PERMANENT_SESSION_LIFETIME=timedelta(days=400),  # browsers cap cookies at 400 days
        JSON_AS_ASCII=False,
        PREFERRED_URL_SCHEME="https" if production else "http",
    )
    if test_config:
        app.config.update(test_config)
    if production and not os.environ.get("SECRET_KEY"):
        logging.getLogger(__name__).warning(
            "SECRET_KEY is not set: phones will lose their settings when the server restarts.")
    if production:
        # Behind the hosting platform's HTTPS proxy.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        logging.basicConfig(level=logging.INFO)

    from .views import bp

    app.register_blueprint(bp)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app
