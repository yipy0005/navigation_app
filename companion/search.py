"""Find a place he wants to go: a typed or pasted name, address, coordinates,
or a shared Google Maps link."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote_plus

from . import addresses, services
from .live import KIND_WORDS, LANDMARKS

COORDS = re.compile(r"(-?\d{1,2}\.\d{3,})\s*[,;]\s*(-?\d{1,3}\.\d{3,})")
URL_COORDS = [
    re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)"),  # exact pin of a place
    re.compile(r"[?&](?:q|query|ll|destination|daddr)=(-?\d+\.\d+)(?:,|%2C)(-?\d+\.\d+)", re.I),
    re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)"),  # map centre, last resort
]
URL_PLACE = re.compile(r"/place/([^/@?]+)")
URL_QUERY = re.compile(r"[?&](?:q|query)=([^&]+)")


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _in_area(lat: float, lng: float) -> bool:
    return 42.9 <= lat <= 43.3 and 12.2 <= lng <= 12.6


def _from_url(text: str) -> list[dict]:
    url = text.strip().split()[0]
    if re.match(r"^https://(maps\.app\.goo\.gl|goo\.gl)/", url):
        url = services.resolve_maps_link(url) or url
    name = ""
    m = URL_PLACE.search(url)
    if m:
        name = unquote_plus(m.group(1))
    for pattern in URL_COORDS:
        m = pattern.search(url)
        if m:
            lat, lng = float(m.group(1)), float(m.group(2))
            return [{"name": name or f"{lat:.5f}, {lng:.5f}", "detail": "Google Maps", "lat": lat, "lng": lng,
                     "precise": True}]
    # A link with only a name in it: search for that name instead.
    m = URL_QUERY.search(url)
    q = name or (unquote_plus(m.group(1)) if m else "")
    return search(q) if q and not q.startswith("http") else []


def _landmark_matches(q: str, lang: str) -> list[dict]:
    fq = _fold(q)
    words = [w for w in re.split(r"\W+", fq) if len(w) > 1]
    if not words:
        return []
    scored = []
    for lm in LANDMARKS:
        fn = _fold(lm["name"])
        if all(w in fn for w in words):
            scored.append((0 if fn.startswith(fq) else 1, len(fn), lm))
    scored.sort(key=lambda x: (x[0], x[1]))
    out = []
    for _, _, lm in scored[:5]:
        kind = KIND_WORDS[lang].get(lm["kind"], "").removeprefix("the ").strip()
        out.append({"name": lm["name"], "detail": kind, "lat": lm["lat"], "lng": lm["lng"], "precise": True})
    return out


def search(q: str, lang: str = "en") -> list[dict]:
    q = (q or "").strip()[:300]
    if len(q) < 2:
        return []
    # Sharing from the Google Maps app pastes "Name\nAddress\nhttps://maps.app.goo.gl/…"
    link = re.search(r"https://\S+", q)
    if link and re.match(r"https://(maps\.app\.goo\.gl|goo\.gl|(www\.|maps\.)?google\.[a-z.]+)/", link.group(0)):
        found = _from_url(link.group(0))
        if found:
            return found
        q = q.replace(link.group(0), " ").strip()  # fall back to the name/address text
        if len(q) < 2:
            return []
    m = COORDS.search(q)
    if m:
        lat, lng = float(m.group(1)), float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return [{"name": f"{lat:.5f}, {lng:.5f}", "detail": "", "lat": lat, "lng": lng, "precise": True}]

    # Official street numbers first: exact doors, and they work offline.
    results = addresses.lookup(q) + _landmark_matches(q, lang)
    try:
        for g in services.geocode(q):
            first, _, rest = g["name"].partition(", ")
            results.append({"name": first, "detail": rest.replace(", Italia", ""), "lat": g["lat"], "lng": g["lng"],
                            "precise": g["precise"]})
    except services.ServiceError:
        pass  # the local landmark list still works

    # With an exact door from the official list, bare-street guesses only confuse.
    if any(r.get("source") == "official" and r["precise"] for r in results):
        results = [r for r in results if r["precise"]]
    unique = []
    for r in results:
        if not _in_area(r["lat"], r["lng"]):
            continue
        if any(abs(r["lat"] - u["lat"]) < 2e-4 and abs(r["lng"] - u["lng"]) < 2e-4 for u in unique):
            continue
        unique.append(r)
    return unique[:8]
