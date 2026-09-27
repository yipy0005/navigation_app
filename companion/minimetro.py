"""Minimetrò: stations (from OpenStreetMap), line order, and opening hours.

Hours come from minimetrospa.it/orari (checked 27 Sep 2026):
  Mon–Fri 07:00–21:05, Sat 07:00–01:05 (late Saturdays until 26 Dec 2026),
  Sundays/holidays 09:00–20:45 (OpenStreetMap). Trains every ~2.5 minutes.
The line closes for about two weeks of maintenance each August and on
occasional days; the family can add those dates in Family setup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    lat: float
    lng: float


# Line order: Pian di Massiano (0) ... Pincetto (6, below the old town).
STATIONS: list[Station] = [
    Station("pian_di_massiano", "Pian di Massiano", 43.10906, 12.36079),
    Station("cortonese", "Cortonese", 43.10618, 12.36400),
    Station("madonna_alta", "Madonna Alta", 43.10438, 12.36989),
    Station("fontivegge", "Fontivegge", 43.10555, 12.37397),
    Station("case_bruciate", "Case Bruciate", 43.10810, 12.37711),
    Station("cupa", "Cupa", 43.11042, 12.38503),
    Station("pincetto", "Pincetto", 43.11024, 12.39056),
]
BY_ID = {s.id: s for s in STATIONS}

RIDE_MIN_PER_STOP = 1.7
WAIT_MIN = 3  # average wait (trains every ~2.5 min) plus getting on calmly

DEFAULT_HOURS = {
    "weekday": ["07:00", "21:05"],
    "saturday": ["07:00", "01:05"],
    "saturday_late_until": "2026-12-26",
    "sunday": ["09:00", "20:45"],
    "closed_dates": ["2026-09-27"],
}


def ride_minutes(a: int, b: int) -> float:
    return abs(a - b) * RIDE_MIN_PER_STOP


def terminus_towards(a: int, b: int) -> Station:
    return STATIONS[-1] if b > a else STATIONS[0]


def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def _is_holiday(d: date) -> bool:
    fixed = {(1, 1), (1, 6), (1, 29), (4, 25), (5, 1), (6, 2), (8, 15), (11, 1), (12, 8), (12, 25), (12, 26)}
    return (d.month, d.day) in fixed or d == _easter(d.year) + timedelta(days=1)


def _parse(hm: str) -> time:
    h, m = hm.split(":")
    return time(int(h), int(m))


def _window(d: date, hours: dict) -> tuple[datetime, datetime] | None:
    if d.isoformat() in set(hours.get("closed_dates") or []):
        return None
    if d.weekday() == 6 or _is_holiday(d):
        key = "sunday"
    elif d.weekday() == 5:
        late_until = hours.get("saturday_late_until")
        key = "saturday" if not late_until or d.isoformat() <= late_until else "weekday"
    else:
        key = "weekday"
    start_s, end_s = hours.get(key) or DEFAULT_HOURS[key]
    start = datetime.combine(d, _parse(start_s), ROME)
    end = datetime.combine(d, _parse(end_s), ROME)
    if end <= start:  # closes after midnight
        end += timedelta(days=1)
    return start, end


def is_running(hours: dict, at: datetime | None = None, margin_min: float = 0) -> bool:
    """True if a train can be boarded at ``at`` + margin (Rome time)."""
    at = (at or datetime.now(ROME)).astimezone(ROME) + timedelta(minutes=margin_min)
    for d in (at.date() - timedelta(days=1), at.date()):
        w = _window(d, hours)
        # the last train leaves a few minutes before closing
        if w and w[0] <= at <= w[1] - timedelta(minutes=5):
            return True
    return False
