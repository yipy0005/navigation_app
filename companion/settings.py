"""Per-phone settings.

Settings live in the phone's own signed session cookie, not on the server.
Each phone keeps its own hotel, family number and destinations, the server
stays stateless (easy to host anywhere), and personal details are not stored
centrally. A family member can fill in Family setup on their own phone and
send the resulting "setup link" to him; opening it copies the settings over.
"""

from __future__ import annotations

import copy

from flask import current_app, session
from itsdangerous import BadSignature, URLSafeSerializer

from .minimetro import DEFAULT_HOURS
from .perugia import DEFAULT_CONFIG

EDITABLE_DEST_FIELDS = ("name", "address", "lat", "lng", "hint")


def load() -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["metro_hours"] = copy.deepcopy(DEFAULT_HOURS)
    saved = session.get("cfg") or {}
    for key in ("home", "contact", "fallback_location"):
        if isinstance(saved.get(key), dict):
            cfg[key].update(saved[key])
    for key in ("taxi_phone", "pretend_location", "configured", "default_mode"):
        if key in saved:
            cfg[key] = saved[key]
    if isinstance(saved.get("closed_dates"), list):
        cfg["metro_hours"]["closed_dates"] = saved["closed_dates"]
    edits = saved.get("dests") or {}
    for d in cfg["destinations"]:
        for k, v in (edits.get(d["id"]) or {}).items():
            if k in EDITABLE_DEST_FIELDS:
                d[k] = v
    return cfg


def save(cfg: dict) -> None:
    """Store only what the family can change, so the cookie stays small."""
    session.permanent = True
    session["cfg"] = {
        "home": cfg["home"],
        "contact": cfg["contact"],
        "fallback_location": cfg["fallback_location"],
        "taxi_phone": cfg["taxi_phone"],
        "pretend_location": cfg["pretend_location"],
        "configured": cfg["configured"],
        "default_mode": cfg.get("default_mode", "easiest"),
        "closed_dates": cfg["metro_hours"]["closed_dates"],
        "dests": {d["id"]: {k: d[k] for k in EDITABLE_DEST_FIELDS if k in d}
                  for d in cfg["destinations"] if not d.get("node")},
    }


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(current_app.secret_key, salt="companion-setup-link")


def export_token(lang: str) -> str:
    return _serializer().dumps({"cfg": session.get("cfg") or {}, "lang": lang})


def import_token(token: str) -> str | None:
    """Apply a setup link. Returns its language, or None if the link is invalid."""
    try:
        data = _serializer().loads(token)
    except BadSignature:
        return None
    session.permanent = True
    session["cfg"] = data.get("cfg") or {}
    return data.get("lang")
