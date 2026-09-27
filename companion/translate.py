"""Translation: the offline phrasebook first, then an optional online service.

Free text is sent to the MyMemory public API (no key needed). Set
COMPANION_ONLINE_TRANSLATE=0 to keep everything on this server.
"""

from __future__ import annotations

import os

import requests

from .phrases import lookup

MYMEMORY_URL = "https://api.mymemory.translated.net/get"
MAX_CHARS = 400


class TranslationUnavailable(Exception):
    pass


def to_italian(text: str, source_lang: str) -> tuple[str, str]:
    """Return (italian_text, source) where source is 'phrasebook' or 'online'."""
    text = (text or "").strip()[:MAX_CHARS]
    if not text:
        raise ValueError("empty text")

    hit = lookup(text)
    if hit:
        return hit, "phrasebook"

    if os.environ.get("COMPANION_ONLINE_TRANSLATE", "1") == "0":
        raise TranslationUnavailable("online translation disabled")

    pair = "zh-CN|it" if source_lang == "zh" else "en|it"
    try:
        r = requests.get(MYMEMORY_URL, params={"q": text, "langpair": pair}, timeout=6)
        r.raise_for_status()
        data = r.json()
        out = (data.get("responseData") or {}).get("translatedText") or ""
    except (requests.RequestException, ValueError) as exc:
        raise TranslationUnavailable(str(exc)) from exc
    # MyMemory reports quota/errors inside a 200 response.
    if not out or data.get("responseStatus") not in (200, "200") or "MYMEMORY WARNING" in out.upper():
        raise TranslationUnavailable("bad response")
    return out, "online"
