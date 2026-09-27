"""Exact door positions from the Comune di Perugia's official street numbers.

OpenStreetMap only knows some house numbers in Perugia (Via dei Priori 20 is
missing, for example). The city's own list has about 60,000 doors, so an
address like "Via dei Priori 20" lands on the right door instead of
somewhere along the street.
"""

from __future__ import annotations

import gzip
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "addresses.json.gz"
KINDS = ("via", "viale", "corso", "piazza", "piazzale", "piazzetta", "largo", "vicolo", "strada", "salita",
         "scala", "scalette", "scaletta", "borgo", "galleria", "rampa", "vico", "lungo", "contrada", "localita",
         "frazione", "traversa", "passaggio", "portico", "chiasso")
PARTICLES = {"di", "de", "del", "della", "dei", "degli", "delle", "dello", "dal", "dalla", "da", "e", "d", "dell",
             "san", "santa", "santo", "s"}
FLAT_DETAILS = re.compile(r"\b(piano|interno|int\.?|scala|sc\.?|floor|flat|apt\.?)\s*\w+", re.I)
NOISE = re.compile(r"\b(perugia|pg|italia|italy|umbria)\b|\b0\d{4}\b", re.I)  # city, province, postcode
NUM_AFTER = re.compile(r"^(?P<street>.*?[^\d\s,])[\s,]+(?:n\.?|nr\.?|n°|numero|civico)?\s*(?P<num>\d{1,4})\s*(?:/?\s*(?P<ext>[a-z])\b)?", re.I)
NUM_BEFORE = re.compile(r"^(?P<num>\d{1,4})\s*(?:/?(?P<ext>[a-z])\b)?[\s,]+(?P<street>\D.+)$", re.I)


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower().replace("’", "'"))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _core(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t not in KINDS and t not in PARTICLES]


@lru_cache(maxsize=1)
def _index():
    with gzip.open(DATA, "rt", encoding="utf-8") as f:
        data = json.load(f)
    streets = []
    for code, name in data["streets"].items():
        toks = fold(name).split()
        streets.append((code, name, toks, set(_core(toks))))
    return data["source"], streets, data["numbers"]


def _match_streets(query: str) -> list[tuple[str, str]]:
    """Official streets matching a typed name, best first."""
    q = fold(query).split()
    if not q:
        return []
    q_kind = q[0] if q[0] in KINDS else None
    q_core = set(_core(q))
    if not q_core:
        return []
    _, streets, _ = _index()
    scored = []
    for code, name, toks, core in streets:
        if not q_core <= core:
            continue
        score = len(core - q_core)  # fewer extra words is better ("Corso Vannucci" vs "Via Pietro Vannucci")
        if q_kind and toks[0] != q_kind:
            score += 5
        scored.append((score, name, code))
    scored.sort()
    return [(code, name) for _, name, code in scored[:3]]


def _parse(part: str):
    part = NOISE.sub(" ", part)
    part = re.sub(r"\s+", " ", part).strip(" ,.-")
    for rx in (NUM_BEFORE, NUM_AFTER):
        m = rx.match(part)
        if m:
            street = m.group("street").strip(" ,.-")
            if len(street) >= 3:
                return street, int(m.group("num")), (m.group("ext") or "").upper()
    return None


def lookup(query: str) -> list[dict]:
    """Doors for "Via dei Priori 20", "20 Via dei Priori", "Via dei Priori, 20/B"…"""
    source, _, numbers = _index()
    query = FLAT_DETAILS.sub(" ", query)  # "piano 6 interno 607" is inside the building
    parts = [p.strip() for p in query.split(",") if p.strip()]
    # also try each part joined with the next ("Via dei Priori, 20")
    tries = parts + [f"{a} {b}" for a, b in zip(parts, parts[1:])]
    out: list[dict] = []
    for t in tries:
        parsed = _parse(t)
        if not parsed:
            continue
        street_q, num, ext = parsed
        for code, name in _match_streets(street_q):
            rows = numbers.get(code, [])
            exact = [r for r in rows if r[0] == num and (not ext or r[1].upper().replace(" ", "").endswith(f"/{ext}"))]
            if not exact:
                exact = [r for r in rows if r[0] == num]
            if exact:
                r = exact[0]
                out.append({"name": f"{name} {r[1] or num}", "detail": "Perugia", "lat": r[2], "lng": r[3],
                            "precise": True, "source": "official"})
                continue
            # Number not in the list: nearest door on the same side of the street.
            same_side = [r for r in rows if r[0] % 2 == num % 2] or rows
            if same_side:
                r = min(same_side, key=lambda x: abs(x[0] - num))
                if abs(r[0] - num) <= 10:
                    out.append({"name": f"{name} {num}", "detail": f"near No. {r[1]} · Perugia", "lat": r[2],
                                "lng": r[3], "precise": False, "source": "official"})
        if out:
            break
    return out[:3]
