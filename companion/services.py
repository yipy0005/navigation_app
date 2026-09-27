"""Clients for the open map services the app relies on.

* Valhalla (walking routes, elevation, walking-time matrix)
* Overpass (only offline, via fetch_landmarks.py)
* Nominatim (address search for Family setup, street name for "You are near")

All calls have short timeouts and a small in-memory cache. Every caller must
cope with ServiceError, because the app falls back to simpler behaviour.
The public servers are run by volunteers (FOSSGIS / OSM); they allow light,
fair use like this. Point the env vars at your own servers for heavier use.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import OrderedDict

import requests

VALHALLA_URL = os.environ.get("COMPANION_VALHALLA_URL", "https://valhalla1.openstreetmap.de").rstrip("/")
OVERPASS_URL = os.environ.get("COMPANION_OVERPASS_URL", "https://overpass-api.de/api/interpreter")
NOMINATIM_URL = os.environ.get("COMPANION_NOMINATIM_URL", "https://nominatim.openstreetmap.org").rstrip("/")
CONTACT = os.environ.get("COMPANION_CONTACT_EMAIL", "")
USER_AGENT = f"PerugiaCompanion/1.0 (+{CONTACT})" if CONTACT else "PerugiaCompanion/1.0"

_http = requests.Session()
_http.headers.update({"User-Agent": USER_AGENT})


class ServiceError(Exception):
    pass


class TTLCache:
    def __init__(self, size: int = 256, ttl: float = 600):
        self.size, self.ttl = size, ttl
        self._d: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            hit = self._d.get(key)
            if not hit or hit[0] < time.monotonic():
                self._d.pop(key, None)
                return None
            self._d.move_to_end(key)
            return hit[1]

    def put(self, key, value):
        with self._lock:
            self._d[key] = (time.monotonic() + self.ttl, value)
            self._d.move_to_end(key)
            while len(self._d) > self.size:
                self._d.popitem(last=False)


_route_cache = TTLCache(512, 1800)
_matrix_cache = TTLCache(256, 1800)
_geo_cache = TTLCache(256, 86400)

# Nominatim asks for at most one request per second.
_nominatim_lock = threading.Lock()
_nominatim_last = [0.0]


def _key_pt(p):
    return (round(p[0], 5), round(p[1], 5))


def decode_polyline6(s: str) -> list[tuple[float, float]]:
    coords, idx, lat, lng = [], 0, 0, 0
    while idx < len(s):
        for which in (0, 1):
            shift = result = 0
            while True:
                b = ord(s[idx]) - 63
                idx += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += delta
            else:
                lng += delta
        coords.append((lat / 1e6, lng / 1e6))
    return coords


# The free public server allows 1 request per second and blocks clients that
# go faster. Our own server (docker-compose.yml) has no limit.
PUBLIC_VALHALLA = "openstreetmap.de" in VALHALLA_URL
_valhalla_gate = threading.Lock()
_valhalla_last = [0.0]
_valhalla_down_until = [0.0]
DOWN_PAUSE_S = 60  # after a refusal, use the offline directions for a minute instead of waiting


def _post_valhalla(endpoint: str, body: dict, timeout: float = 8) -> dict:
    if time.monotonic() < _valhalla_down_until[0]:
        raise ServiceError("valhalla recently unreachable")
    if PUBLIC_VALHALLA:
        with _valhalla_gate:
            wait = 1.05 - (time.monotonic() - _valhalla_last[0])
            if wait > 0:
                time.sleep(wait)
            _valhalla_last[0] = time.monotonic()
    try:
        r = _http.post(f"{VALHALLA_URL}/{endpoint}", json=body, timeout=timeout)
    except requests.RequestException as exc:
        if isinstance(exc, requests.ConnectionError):
            _valhalla_down_until[0] = time.monotonic() + DOWN_PAUSE_S
        raise ServiceError(f"valhalla unreachable: {exc}") from exc
    if r.status_code == 429:
        _valhalla_down_until[0] = time.monotonic() + DOWN_PAUSE_S
        raise ServiceError("valhalla: too many requests")
    if r.status_code == 400:
        # e.g. "No path could be found"
        raise ServiceError(f"valhalla: {r.text[:200]}")
    if not r.ok:
        raise ServiceError(f"valhalla HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as exc:
        raise ServiceError("valhalla: bad JSON") from exc


def walk_route(a, b, costing: dict) -> dict:
    """Walking route with maneuvers, decoded shape and elevation samples."""
    key = (_key_pt(a), _key_pt(b), tuple(sorted(costing.items())))
    hit = _route_cache.get(key)
    if hit:
        return hit
    body = {
        "locations": [{"lat": a[0], "lon": a[1]}, {"lat": b[0], "lon": b[1]}],
        "costing": "pedestrian",
        "costing_options": {"pedestrian": costing},
        "units": "km",
        "language": "en-GB",
        "elevation_interval": 10,
    }
    data = _post_valhalla("route", body)
    leg = data["trip"]["legs"][0]
    out = {
        "time_s": data["trip"]["summary"]["time"],
        "length_m": data["trip"]["summary"]["length"] * 1000,
        "maneuvers": leg["maneuvers"],
        "shape": decode_polyline6(leg["shape"]),
        "elevation": leg.get("elevation") or [],
        "elevation_interval": leg.get("elevation_interval") or 10,
    }
    _route_cache.put(key, out)
    return out


_trace_cache = TTLCache(512, 1800)
TRACE_ATTRS = ["edge.use", "edge.road_class", "edge.length", "edge.lit", "edge.begin_shape_index",
               "edge.end_shape_index", "edge.end_node.traffic_signal"]


def trace_edges(shape: list[tuple[float, float]]) -> list[dict]:
    """What each piece of a walking route is (footway, road, crossing, steps…),
    with shape indexes so it can be matched back to the route."""
    key = (len(shape), _key_pt(shape[0]), _key_pt(shape[-1]), _key_pt(shape[len(shape) // 2]))
    hit = _trace_cache.get(key)
    if hit is not None:
        return hit
    body = {"shape": [{"lat": a, "lon": b} for a, b in shape], "shape_match": "edge_walk", "costing": "pedestrian",
            "filters": {"attributes": TRACE_ATTRS, "action": "include"}}
    try:
        edges = _post_valhalla("trace_attributes", body)["edges"]
    except KeyError as exc:
        raise ServiceError("valhalla: no edges") from exc
    _trace_cache.put(key, edges)
    return edges


def walk_matrix(sources, targets, costing: dict) -> list[list[float | None]]:
    """Walking times in seconds, sources x targets (None = unreachable)."""
    key = (tuple(map(_key_pt, sources)), tuple(map(_key_pt, targets)), tuple(sorted(costing.items())))
    hit = _matrix_cache.get(key)
    if hit:
        return hit
    body = {
        "sources": [{"lat": p[0], "lon": p[1]} for p in sources],
        "targets": [{"lat": p[0], "lon": p[1]} for p in targets],
        "costing": "pedestrian",
        "costing_options": {"pedestrian": costing},
    }
    data = _post_valhalla("sources_to_targets", body)
    out = [[(c or {}).get("time") for c in row] for row in data["sources_to_targets"]]
    _matrix_cache.put(key, out)
    return out


def _nominatim(path: str, params: dict) -> object:
    with _nominatim_lock:
        wait = 1.05 - (time.monotonic() - _nominatim_last[0])
        if wait > 0:
            time.sleep(wait)
        _nominatim_last[0] = time.monotonic()
    try:
        r = _http.get(f"{NOMINATIM_URL}/{path}", params={**params, "format": "jsonv2"}, timeout=6)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as exc:
        raise ServiceError(f"nominatim: {exc}") from exc


_FLAT_DETAILS = re.compile(r"\b(piano|interno|int\.?|scala|sc\.?|floor|flat|apt\.?)\s*\w+", re.I)


def geocode(query: str) -> list[dict]:
    """Search an address. Hotel addresses often carry extras ("Casa Valentina 3,
    Via Cortonese 27 piano 6 interno 607") that the search can't match, so we
    retry with the flat details removed and then with leading parts dropped."""
    cleaned = re.sub(r"\s+", " ", _FLAT_DETAILS.sub("", query)).strip(" ,")
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    tries = [query.strip(), cleaned] + [", ".join(parts[k:]) for k in range(1, len(parts) - 1)]
    seen, results = set(), []
    for q in tries:
        if len(q) < 3 or q.lower() in seen:
            continue
        seen.add(q.lower())
        for r in _geocode_once(q):
            if all(abs(r["lat"] - o["lat"]) > 1e-4 or abs(r["lng"] - o["lng"]) > 1e-4 for o in results):
                results.append(r)
        if any(r["precise"] for r in results):
            break
    # Exact buildings first; a bare street can be long (Via Cortonese is 2 km).
    return sorted(results, key=lambda r: not r["precise"])[:5]


def _geocode_once(query: str) -> list[dict]:
    query = query.strip()[:200]
    key = ("q", query.lower())
    hit = _geo_cache.get(key)
    if hit is not None:
        return hit
    # Only around Perugia: "Perugia" is also the province, and the same street
    # name exists in other towns (Via Cortonese 27 matched Umbertide, 30 km away).
    data = _nominatim("search", {"q": query, "limit": 5, "viewbox": "12.25,43.20,12.52,43.00", "bounded": 1,
                                 "addressdetails": 0, "accept-language": "it"})
    out = [{"name": d.get("display_name", ""), "lat": float(d["lat"]), "lng": float(d["lon"]),
            "precise": d.get("addresstype") not in ("road", "suburb", "quarter", "neighbourhood", "city",
                                                     "town", "village", "postcode", "county", "state")}
           for d in data or []]
    _geo_cache.put(key, out)
    return out


_GOOGLE_HOST = re.compile(r"^(maps\.app\.goo\.gl|goo\.gl|(www\.|maps\.)?google\.[a-z.]{2,6})$", re.I)


def resolve_maps_link(url: str) -> str | None:
    """Follow a shared Google Maps short link (maps.app.goo.gl/…) to the full URL,
    which contains the coordinates. Only Google hosts are contacted, hop by hop,
    so a pasted link cannot make the server fetch anything else."""
    from urllib.parse import parse_qs, urljoin, urlsplit

    for hop in range(6):
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host == "consent.google.com":
            # EU cookie-consent page: the real address is in ?continue=
            nxt = parse_qs(parts.query).get("continue", [""])[0]
            if not nxt:
                return None
            url = nxt
            continue
        if parts.scheme != "https" or not _GOOGLE_HOST.match(host):
            return None
        # Once we have a full Google Maps address we have what we need;
        # fetching it again would only lead to the consent page.
        if hop > 0 and (parts.path.startswith("/maps") or host.startswith("maps.google.")):
            return url
        try:
            r = _http.get(url, allow_redirects=False, timeout=6, stream=True)
            r.close()
        except requests.RequestException:
            return None
        loc = r.headers.get("Location")
        if r.status_code in (301, 302, 303, 307, 308) and loc:
            url = urljoin(url, loc)
            continue
        return url
    return None


def street_at(lat: float, lng: float) -> str | None:
    """Street or square name at a point, in Italian (what the signs say)."""
    key = ("r", round(lat, 4), round(lng, 4))
    hit = _geo_cache.get(key)
    if hit is not None:
        return hit or None
    data = _nominatim("reverse", {"lat": lat, "lon": lng, "zoom": 17, "accept-language": "it"})
    addr = (data or {}).get("address", {}) if isinstance(data, dict) else {}
    name = addr.get("pedestrian") or addr.get("road") or addr.get("square") or addr.get("footway") or ""
    _geo_cache.put(key, name)
    return name or None
