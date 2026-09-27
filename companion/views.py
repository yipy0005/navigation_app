from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode, urlsplit

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, session, url_for

from . import live, settings
from . import router as offline
from .i18n import LANGS, STRINGS, t
from .perugia import NODES
from .phrases import PHRASES
from .router import Target, haversine_m, near_place
from .addresses import lookup as address_lookup
from .search import search as place_search
from .services import ServiceError, geocode, street_at
from .translate import TranslationUnavailable, to_italian

bp = Blueprint("companion", __name__)

CITY_CENTRE = (NODES["p4nov"].lat, NODES["p4nov"].lng)
CITY_RADIUS_M = 6000  # beyond this, walking directions make no sense: suggest a taxi
LONG_WALK_MIN = 35  # suggest a taxi as well when the easiest way takes longer than this
LANG_COOKIE = "lang"
OPEN_PATHS = ("companion.welcome", "companion.set_lang", "companion.setup", "companion.import_setup",
              "companion.service_worker", "static", "healthz")
_modes_pool = ThreadPoolExecutor(max_workers=4)


# --- helpers -----------------------------------------------------------------
def cfg() -> dict:
    return settings.load()


def lang() -> str:
    # ?lang=zh lets a family member share a link that opens in the right language.
    code = request.args.get("lang") or request.cookies.get(LANG_COOKIE, "")
    return code if code in LANGS else "en"


def place_from_args() -> dict | None:
    """A one-off destination found with the search box: /route/place?to_lat=…&to_lng=…&to_name=…"""
    try:
        lat, lng = float(request.args["to_lat"]), float(request.args["to_lng"])
    except (KeyError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    name = (request.args.get("to_name") or "").strip()[:80] or f"{lat:.5f}, {lng:.5f}"
    addr = (request.args.get("to_addr") or "").strip()[:200]
    qs = urlencode({"to_lat": f"{lat:.6f}", "to_lng": f"{lng:.6f}", "to_name": name, "to_addr": addr})
    return {"id": "place", "icon": "pin", "name": {"en": name, "zh": name},
            "address": f"{name}, {addr}" if addr else name, "lat": lat, "lng": lng, "hint": "", "qs": qs}


def destination(c: dict, dest_id: str) -> dict | None:
    if dest_id == "place":
        return place_from_args()
    if dest_id == "home":
        h = c["home"]
        return {"id": "home", "icon": "home", "name": {"en": h["name"], "zh": h["name"]},
                "address": h["address"], "lat": h["lat"], "lng": h["lng"], "hint": h.get("hint", ""),
                "is_home": True}
    for d in c["destinations"]:
        if d["id"] == dest_id:
            d = dict(d)
            if d.get("node"):
                n = NODES[d["node"]]
                d.setdefault("lat", n.lat)
                d.setdefault("lng", n.lng)
                d.setdefault("address", n.name["it"] + ", Perugia")
            return d
    return None


def user_point(c: dict) -> tuple[tuple[float, float] | None, bool]:
    """(position, is_test_location). None when the phone has not given a position.

    We never guess a position for real use: wrong directions are worse than none.
    """
    if c.get("pretend_location"):
        fb = c["fallback_location"]
        return (fb["lat"], fb["lng"]), True
    try:
        lat, lng = float(request.args["lat"]), float(request.args["lng"])
    except (KeyError, ValueError):
        return None, False
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None, False
    return (lat, lng), False


def extra_places(c: dict) -> list:
    out = [({"en": c["home"]["name"], "zh": c["home"]["name"], "it": c["home"]["name"]},
            c["home"]["lat"], c["home"]["lng"])]
    for d in c["destinations"]:
        if "lat" in d and not d.get("node"):
            out.append(({**d["name"], "it": d.get("address", d["name"]["en"])}, d["lat"], d["lng"]))
    return out


@bp.app_context_processor
def inject():
    lg = lang()
    c = cfg()
    return {
        "lang": lg,
        "T": t(lg),
        "cfg": c,
        "client_cfg": {
            "lang": lg,
            "taxi": c["taxi_phone"],
            "contact": c["contact"],
            "emergency": c["emergency_phone"],
            "pretend": bool(c.get("pretend_location")),
        },
    }


@bp.before_app_request
def require_language():
    if request.endpoint in OPEN_PATHS or (request.endpoint or "").startswith("companion.api_"):
        return None
    if request.args.get("lang") not in LANGS and request.cookies.get(LANG_COOKIE) not in LANGS:
        return redirect(url_for("companion.welcome"))
    return None


@bp.after_app_request
def security_headers(resp):
    if request.args.get("lang") in LANGS:
        _set_lang_cookie(resp, request.args["lang"])
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(self), microphone=(self)")
    if request.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


def _set_lang_cookie(resp, code):
    resp.set_cookie(LANG_COOKIE, code, max_age=60 * 60 * 24 * 400, samesite="Lax",
                    secure=current_app.config.get("SESSION_COOKIE_SECURE", False))


# --- pages -------------------------------------------------------------------
@bp.get("/welcome")
def welcome():
    return render_template("welcome.html", S=STRINGS)


@bp.post("/lang/<code>")
def set_lang(code: str):
    if code not in LANGS:
        abort(400)
    resp = redirect(url_for("companion.index"))
    _set_lang_cookie(resp, code)
    return resp


@bp.get("/sw.js")
def service_worker():
    resp = current_app.send_static_file("js/sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.get("/")
def index():
    return render_template("home.html")


@bp.get("/go")
def choose():
    c = cfg()
    dests = [destination(c, "home")] + [destination(c, d["id"]) for d in c["destinations"]]
    return render_template("choose.html", dests=dests)


@bp.get("/route/<dest_id>")
def route(dest_id: str):
    d = destination(cfg(), dest_id)
    if not d:
        abort(404)
    return render_template("route.html", dest=d)


@bp.get("/navigate/<dest_id>")
def navigate(dest_id: str):
    d = destination(cfg(), dest_id)
    if not d:
        abort(404)
    mode = request.args.get("mode")
    if mode not in live.MODES:
        mode = cfg().get("default_mode", "easiest")
    return render_template("navigate.html", dest=d, mode=mode)


@bp.get("/lost")
def lost():
    return render_template("lost.html")


@bp.get("/translate")
def translate_page():
    return render_template("translate.html", phrases=PHRASES)


@bp.get("/show")
def show():
    c = cfg()
    d = destination(c, request.args.get("dest", "home")) or destination(c, "home")
    purpose = "taxi" if request.args.get("purpose") == "taxi" else "help"
    return render_template("show.html", dest=d, purpose=purpose)


# --- family setup ------------------------------------------------------------
PHONE_RE = re.compile(r"^[+0-9 ()\-]{0,24}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _f(name: str, lo: float, hi: float) -> float:
    v = float(request.form[name].replace(",", "."))
    if not lo <= v <= hi:
        raise ValueError(name)
    return v


def _txt(name: str, limit: int = 160) -> str:
    return (request.form.get(name) or "").strip()[:limit]


def _same_origin() -> bool:
    """CSRF protection by checking where the form was posted from.

    A per-session token broke on Brave for iOS, where the cookie from the
    setup page did not come back with the form. Browsers always send Origin
    (or at least Referer) on a form POST, and a forged cross-site form would
    carry the attacker's origin.
    """
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source or source == "null":
        return False
    return urlsplit(source).netloc == request.host


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    c = cfg()
    errors: list[str] = []
    saved = False
    if request.method == "POST":
        if not _same_origin():
            abort(400)
        try:
            c["home"] = {
                "name": _txt("home_name", 80) or "My hotel",
                "address": _txt("home_address", 200),
                "lat": _f("home_lat", -90, 90), "lng": _f("home_lng", -180, 180),
                "hint": _txt("home_hint", 120),
            }
            c["fallback_location"] = {"lat": _f("fb_lat", -90, 90), "lng": _f("fb_lng", -180, 180)}
            for d in c["destinations"]:
                if d.get("node"):
                    continue
                p = f"d_{d['id']}_"
                d["name"] = {"en": _txt(p + "en", 60) or d["name"]["en"], "zh": _txt(p + "zh", 60) or d["name"]["zh"]}
                d["address"] = _txt(p + "address", 200)
                d["lat"], d["lng"] = _f(p + "lat", -90, 90), _f(p + "lng", -180, 180)
                d["hint"] = _txt(p + "hint", 120)
        except (KeyError, ValueError):
            errors.append("Please check the coordinates: they must be numbers, e.g. 43.1122 and 12.3888. "
                          "Use the Find button to fill them in. / 请检查坐标，可用“查找”按钮自动填写。")
        phones = {"contact_phone": _txt("contact_phone", 24), "taxi_phone": _txt("taxi_phone", 24)}
        for k, v in phones.items():
            if not PHONE_RE.match(v):
                errors.append(f"{k.replace('_', ' ').title()} may only contain digits, spaces and +.")
        dates = [x.strip() for x in _txt("closed_dates", 400).replace("\n", ",").split(",") if x.strip()]
        if any(not DATE_RE.match(x) for x in dates):
            errors.append("Minimetrò closed days must look like 2026-08-03, separated by commas.")
        if not errors:
            c["contact"] = {"name": _txt("contact_name", 40) or "Family", "phone": phones["contact_phone"]}
            c["taxi_phone"] = phones["taxi_phone"] or c["taxi_phone"]
            c["pretend_location"] = request.form.get("pretend") == "on"
            c["default_mode"] = request.form.get("default_mode") if request.form.get("default_mode") in live.MODES else "easiest"
            c["metro_hours"]["closed_dates"] = sorted(set(dates))
            c["configured"] = True
            settings.save(c)
            saved = True
    link = url_for("companion.import_setup", token=settings.export_token(lang()), _external=True) \
        if session.get("cfg") else ""
    return render_template("setup.html", c=c, errors=errors, saved=saved, link=link)


@bp.get("/s/<token>")
def import_setup(token: str):
    code = settings.import_token(token)
    if code is None:
        abort(400, "This setup link is not valid.")
    resp = redirect(url_for("companion.index"))
    if code in LANGS:
        _set_lang_cookie(resp, code)
    return resp


# --- JSON API ----------------------------------------------------------------
def _near(c: dict, pt, lg: str) -> dict | None:
    """Friendly name for where he is: a known place, else the street he is on."""
    close = near_place(pt, extra_places(c), lg)
    if close and close["metres"] <= 120:
        return close
    if haversine_m(pt, CITY_CENTRE) < CITY_RADIUS_M:
        try:
            street = street_at(*pt)
        except ServiceError:
            street = None
        if street:
            return {"name": street, "it": street, "metres": 0}
    return close


@bp.get("/api/where")
def api_where():
    c = cfg()
    lg = lang()
    pt, test = user_point(c)
    if pt is None:
        return jsonify({"no_location": True})
    home = (c["home"]["lat"], c["home"]["lng"])
    return jsonify({
        "lat": pt[0], "lng": pt[1], "test": test,
        "near": _near(c, pt, lg),
        "in_perugia": haversine_m(pt, CITY_CENTRE) < CITY_RADIUS_M,
        "home_m": round(haversine_m(pt, home)),
        "maps_url": f"https://maps.google.com/?q={pt[0]:.6f},{pt[1]:.6f}",
    })


def _plan(pt, target: Target, mode: str, lg: str, hours: dict, allow_metro: bool) -> dict:
    try:
        return live.plan(pt, target, mode, lg, hours, allow_metro=allow_metro)
    except ServiceError as exc:
        current_app.logger.warning("live routing failed, using offline directions: %s", exc)
        p = offline.plan(pt, target, "fastest" if mode == "fastest" else "easiest", lg)
        p.update(engine="offline", mode=mode, crossings={"signals": 0, "zebra": 0, "crossing": 0, "unmarked": 0},
                 main_pct=None)
        return p


@bp.get("/api/route")
def api_route():
    c = cfg()
    lg = lang()
    d = destination(c, request.args.get("dest", ""))
    if not d:
        return jsonify({"error": "unknown destination"}), 404
    pt, test = user_point(c)
    if pt is None:
        return jsonify({"no_location": True})
    base = {"dest": {"id": d["id"], "name": d["name"].get(lg) or d["name"]["en"], "address": d.get("address", "")},
            "test": test, "start": {"lat": pt[0], "lng": pt[1]}}
    far = haversine_m(pt, CITY_CENTRE) > CITY_RADIUS_M or haversine_m((d["lat"], d["lng"]), CITY_CENTRE) > CITY_RADIUS_M
    if d.get("taxi_only") or far:
        return jsonify({**base, "taxi": True})
    target = Target(name=d["name"], lat=d["lat"], lng=d["lng"], node=d.get("node"), hint=d.get("hint", ""))
    allow_metro = request.args.get("no_metro") != "1"
    hours = c["metro_hours"]
    wanted = request.args.get("mode")
    modes = [wanted] if wanted in live.MODES else list(live.MODES)
    app = current_app._get_current_object()

    def run(mode):
        with app.app_context():
            return _plan(pt, target, mode, lg, hours, allow_metro)

    plans = dict(zip(modes, _modes_pool.map(run, modes)))
    out = {**base, "taxi": False, **plans, "default_mode": c.get("default_mode", "easiest")}
    if len(plans) > 1:
        # which options are really the same walk, so the page can show them once
        out["same_as"] = {m: next((o for o in modes[:modes.index(m)] if plans[o]["path"] == plans[m]["path"]), None)
                          for m in modes}
        out["same"] = plans["easiest"]["path"] == plans["fastest"]["path"]
    first = plans[modes[0]]
    out["engine"] = first.get("engine", "live")
    out["taxi_suggested"] = first["minutes"] > LONG_WALK_MIN and "metro" not in first["uses"]
    return jsonify(out)


@bp.get("/api/search")
def api_search():
    return jsonify({"results": place_search(request.args.get("q", ""), lang())})


@bp.get("/api/geocode")
def api_geocode():
    q = (request.args.get("q") or "").strip()
    if len(q) < 3:
        return jsonify({"results": []})
    official = [{"name": f"{r['name']}, Perugia", "lat": r["lat"], "lng": r["lng"], "precise": r["precise"]}
                for r in address_lookup(q)]
    try:
        found = geocode(q)
    except ServiceError:
        if not official:
            return jsonify({"error": "unavailable"}), 503
        found = []
    return jsonify({"results": (official + found)[:6]})


@bp.post("/api/translate")
def api_translate():
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", ""))
    src = "zh" if body.get("from") == "zh" else "en"
    if not text.strip():
        return jsonify({"error": "empty"}), 400
    try:
        it, source = to_italian(text, src)
    except TranslationUnavailable:
        return jsonify({"error": "unavailable"}), 503
    return jsonify({"it": it, "source": source})
