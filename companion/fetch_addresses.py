"""Build data/addresses.json.gz from the Comune di Perugia's official list of
street numbers (numeri civici, about 60,000 doors, CC BY 4.0).

Source: https://dati.regione.umbria.it/dataset/elenco-strade-e-numeri-civici
Run with `pixi run fetch-addresses` (uses the `data` environment, which has
pyshp and pyproj for reading the shapefile and converting its coordinates).
"""

from __future__ import annotations

import gzip
import io
import json
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import requests
import shapefile
from pyproj import Transformer

BASE = "https://dati.regione.umbria.it/dataset/943479df-f283-48ca-994e-8a8074fe0462/resource"
STREETS_URL = f"{BASE}/6e1a1b6e-e790-473b-8a71-96817c780f29/download/opendatastradeperugia.xml"
NUMBERS_URL = f"{BASE}/cd80ad5f-65b0-44cc-97dd-9671fad40003/download/numericivici.zip"
OUT = Path(__file__).parent / "data" / "addresses.json.gz"

LOWER = {"di", "de", "del", "della", "dei", "degli", "delle", "dello", "dal", "dalla", "dai", "da", "e", "ed",
         "in", "a", "al", "alla", "alle", "ai", "agli", "per", "sul", "sulla", "tra", "fra", "con", "la", "le",
         "il", "lo", "gli", "i"}


def pretty(kind: str, name: str) -> str:
    """'VIA', 'DEI PRIORI' -> 'Via dei Priori'; "DELL'IRIDE" -> "dell'Iride"."""
    words = []
    # The list writes accents as apostrophes: MAESTA' -> Maestà
    name = re.sub(r"([AEIOU])'(?=\s|$)", lambda m: {"A": "À", "E": "È", "I": "Ì", "O": "Ò", "U": "Ù"}[m.group(1)], name)
    for k, w in enumerate(f"{kind} {name}".lower().split()):
        if "'" in w:
            head, _, tail = w.partition("'")
            w = f"{head}'{tail.capitalize()}" if head in {"d", "dell", "sull", "all", "nell", "l"} else w.capitalize()
            if k == 0:
                w = w[0].upper() + w[1:]
        elif k > 0 and w in LOWER:
            pass
        elif re.fullmatch(r"[ivxlc]+", w) and len(w) <= 4 and w not in {"di", "ci", "li"}:
            w = w.upper()  # Roman numerals: Piazza IV Novembre
        else:
            w = w.capitalize()
        words.append(w)
    return " ".join(words)


def main() -> None:
    root = ET.fromstring(requests.get(STREETS_URL, timeout=120).content)
    streets = {}
    for row in root.iter("Row"):
        code = (row.findtext("UK_STRADA") or "").strip()
        if code:
            streets[code] = pretty((row.findtext("SPECIE_STRADA") or "").strip(), (row.findtext("NOME_STRADA") or "").strip())

    z = zipfile.ZipFile(io.BytesIO(requests.get(NUMBERS_URL, timeout=300).content))
    to_wgs84 = Transformer.from_crs("EPSG:3004", "EPSG:4326", always_xy=True)  # Monte Mario / Italy zone 2
    numbers: dict[str, list] = {}
    with tempfile.TemporaryDirectory() as tmp:
        z.extractall(tmp)
        shp = next(Path(tmp).rglob("*.shp"))
        reader = shapefile.Reader(str(shp.with_suffix("")))
        names = [f[0] for f in reader.fields[1:]]
        for sr in reader.iterShapeRecords():
            rec = dict(zip(names, sr.record))
            if not sr.shape.points or rec["FK_STRADE"] not in streets:
                continue
            x, y = sr.shape.points[0]
            lng, lat = to_wgs84.transform(x, y)
            numbers.setdefault(rec["FK_STRADE"], []).append(
                [int(rec["NUMERO"] or 0), (rec["DESCRIZION"] or "").strip(), round(lat, 6), round(lng, 6)])

    used = {code: streets[code] for code in numbers}
    for rows in numbers.values():
        rows.sort()
    OUT.parent.mkdir(exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        json.dump({"source": "Comune di Perugia, Elenco Strade e Numeri Civici (CC BY 4.0)",
                   "streets": used, "numbers": numbers}, f, ensure_ascii=False, separators=(",", ":"))
    total = sum(len(v) for v in numbers.values())
    print(f"saved {total} street numbers on {len(used)} streets to {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
