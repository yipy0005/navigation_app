"""Download named landmarks and pedestrian crossings for Perugia from
OpenStreetMap into data/landmarks.json and data/crossings.json.

Run with `pixi run fetch-landmarks`. Bundling the file keeps directions fast and
lets landmarks work even when the public Overpass server is busy.
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

from .services import OVERPASS_URL, USER_AGENT

BBOX = "43.095,12.350,43.120,12.400"  # old town, Minimetrò line, station
KINDS = {
    "amenity": "pharmacy|cafe|bar|restaurant|bank|place_of_worship|fountain|ice_cream|post_office|theatre|library|townhall",
    "tourism": "museum|attraction|hotel",
    "historic": "monument|church|fountain|city_gate|castle|palace",
    "shop": "bakery|supermarket|convenience|books|newsagent|tobacco|chocolate|pastry",
}
OUT = Path(__file__).parent / "data" / "landmarks.json"
CROSSINGS_OUT = Path(__file__).parent / "data" / "crossings.json"
CROSSINGS_BBOX = "43.070,12.340,43.130,12.420"


def crossing_kind(t: dict) -> str:
    """signals (traffic lights) | zebra (marked, cars must stop) | unmarked | crossing (type unknown)."""
    c = t.get("crossing", "")
    ref = t.get("crossing_ref", "")
    if t.get("highway") == "traffic_signals" or c == "traffic_signals" or ref in ("pelican", "toucan", "puffin", "pegasus"):
        return "signals"
    if c in ("zebra", "marked", "uncontrolled") or ref == "zebra" or t.get("crossing:markings") not in (None, "no"):
        return "zebra"
    if c in ("unmarked", "no", "informal"):
        return "unmarked"
    return "crossing"


def fetch_crossings() -> None:
    q = (f'[out:json][timeout:90];(node["highway"="crossing"]({CROSSINGS_BBOX});'
         f'node["highway"="traffic_signals"]({CROSSINGS_BBOX}););out;')
    r = requests.post(OVERPASS_URL, data={"data": q}, timeout=120, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    out = [{"lat": round(e["lat"], 6), "lng": round(e["lon"], 6), "kind": crossing_kind(e.get("tags", {}))}
           for e in r.json()["elements"] if "lat" in e]
    CROSSINGS_OUT.write_text(json.dumps(sorted(out, key=lambda p: (p["lat"], p["lng"])), separators=(",", ":")))
    print(f"saved {len(out)} crossings to {CROSSINGS_OUT}")


def main() -> None:
    parts = "".join(f'nwr["name"]["{k}"~"^({v})$"]({BBOX});' for k, v in KINDS.items())
    q = f"[out:json][timeout:90];({parts});out center tags;"
    r = requests.post(OVERPASS_URL, data={"data": q}, timeout=120, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    out = []
    for e in r.json()["elements"]:
        c = e.get("center", e)
        t = e.get("tags", {})
        kind = next((t[k] for k in KINDS if k in t), "")
        out.append({"name": t["name"], "kind": kind, "lat": round(c["lat"], 6), "lng": round(c["lon"], 6)})
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(sorted(out, key=lambda p: (p["lat"], p["lng"])), ensure_ascii=False, indent=0))
    print(f"saved {len(out)} landmarks to {OUT}")
    fetch_crossings()


if __name__ == "__main__":
    main()
