"""Live routing on real OpenStreetMap data.

Walking legs come from Valhalla. The Minimetrò is added on top: we compare
walking all the way with walk → Minimetrò → walk and keep the best option.

"Fastest" minimises minutes. "Easiest" minimises effort: minutes on foot,
metres climbed on foot (escalators and lifts don't count), and steps. Sitting
in the Minimetrò counts as rest.
"""

from __future__ import annotations

import bisect
import math
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import minimetro as mm
from . import services as svc
from .router import bearing, haversine_m

COSTINGS = {
    "fast": {"walking_speed": 4.2},
    # Two "easy" variants: Valhalla's step penalty also punishes escalators,
    # so we ask both ways and let our own effort score decide.
    "easy_a": {"walking_speed": 3.6, "use_hills": 0.0},
    "easy_b": {"walking_speed": 3.6, "use_hills": 0.0, "step_penalty": 900, "max_hiking_difficulty": 1},
    # "Safe": stay on proper streets. Avoid alleys, car parks, driveways, service
    # roads, tracks and unlit paths; prefer pavements and lit streets.
    "safe": {"walking_speed": 3.6, "use_hills": 0.2, "step_penalty": 60, "alley_factor": 6, "driveway_factor": 6,
             "service_penalty": 40, "service_factor": 3, "use_tracks": 0, "use_living_streets": 0.3,
             "sidewalk_factor": 0.8, "use_lit": 1, "max_hiking_difficulty": 1},
}
MODE_COSTINGS = {"fastest": ("fast",), "easiest": ("easy_a", "easy_b"), "safest": ("safe", "easy_a")}
MODES = ("easiest", "safest", "fastest")
METRO_MIN_WALK_MIN = 10  # below this, walking is simpler than the Minimetrò
STEPS_PER_METRE = 2.5  # horizontal metre of stairway ≈ 2–3 steps

_POOL = ThreadPoolExecutor(max_workers=12)
LANDMARKS: list[dict] = json.loads((Path(__file__).parent / "data" / "landmarks.json").read_text(encoding="utf-8"))
# Pedestrian crossings from OpenStreetMap: signals (traffic lights), zebra, unmarked, crossing (type unknown)
CROSSINGS: list[dict] = json.loads((Path(__file__).parent / "data" / "crossings.json").read_text(encoding="utf-8"))
CROSSING_NEAR_M = 8
MAIN_CLASSES = {"motorway", "trunk", "primary", "secondary", "tertiary"}
CAR_CLASSES = MAIN_CLASSES | {"unclassified", "residential"}
QUIET_USES = {"path", "track", "alley", "parking_aisle", "driveway", "service_road", "emergency_access", "cycleway",
              "mountain_bike", "bridleway", "drive_through", "other"}
PED_USES = {"footway", "sidewalk", "pedestrian", "pedestrian_crossing", "steps", "path", "elevator", "escalator"}

STRAIGHT = {0, 1, 2, 3, 7, 8, 17, 18, 19, 22, 25, 26, 27, 42, 43}
RIGHT = {9, 10, 11, 20, 23}
LEFT = {14, 15, 16, 21, 24}
UTURN = {12, 13}
DEST = {4, 5, 6}
CONVEYED = {39, 41}  # lift, escalator: no effort

KIND_WORDS = {
    "en": {"pharmacy": "the pharmacy", "cafe": "the café", "bar": "the café", "restaurant": "the restaurant",
           "bank": "the bank", "place_of_worship": "the church", "church": "the church", "fountain": "the fountain",
           "ice_cream": "the ice-cream shop", "post_office": "the post office", "theatre": "the theatre",
           "library": "the library", "townhall": "the town hall", "museum": "the museum", "attraction": "",
           "hotel": "the hotel", "monument": "the monument", "city_gate": "the old city gate", "castle": "",
           "palace": "the palace", "bakery": "the bakery", "supermarket": "the supermarket",
           "convenience": "the shop", "books": "the bookshop", "newsagent": "the newsagent",
           "tobacco": "the tobacconist", "chocolate": "the chocolate shop", "pastry": "the pastry shop"},
    "zh": {"pharmacy": "药店", "cafe": "咖啡馆", "bar": "咖啡馆", "restaurant": "餐厅", "bank": "银行",
           "place_of_worship": "教堂", "church": "教堂", "fountain": "喷泉", "ice_cream": "冰淇淋店",
           "post_office": "邮局", "theatre": "剧院", "library": "图书馆", "townhall": "市政厅",
           "museum": "博物馆", "attraction": "景点", "hotel": "酒店", "monument": "纪念碑",
           "city_gate": "古城门", "castle": "城堡", "palace": "宫殿", "bakery": "面包店",
           "supermarket": "超市", "convenience": "小商店", "books": "书店", "newsagent": "报刊亭",
           "tobacco": "烟草店", "chocolate": "巧克力店", "pastry": "糕点店"},
}
# Easy-to-spot things first.
KIND_PRIORITY = ["pharmacy", "place_of_worship", "fountain", "city_gate", "museum", "cafe", "bar", "bank",
                 "bakery", "pastry", "supermarket", "ice_cream", "post_office", "theatre", "hotel", "restaurant"]


# --------------------------------------------------------------------------- analysis
def _cumulative(shape):
    cum = [0.0]
    for p, q in zip(shape, shape[1:]):
        cum.append(cum[-1] + haversine_m(p, q))
    return cum


def analyse(route: dict) -> dict:
    """Effort numbers and normalised maneuvers for one Valhalla walking leg."""
    shape, cum = route["shape"], _cumulative(route["shape"])
    elev, step = route["elevation"], route["elevation_interval"]

    def elev_at(d):
        if not elev:
            return 0.0
        return elev[min(int(d / step), len(elev) - 1)]

    items = []
    for m in route["maneuvers"]:
        t = m["type"]
        if t in DEST:
            continue
        b, e = m["begin_shape_index"], m["end_shape_index"]
        kind = {39: "lift", 40: "stairs", 41: "escalator"}.get(t, "walk")
        arrow = "right" if t in RIGHT else "left" if t in LEFT else "uturn" if t in UTURN else "straight"
        items.append({
            "type": t, "kind": kind, "arrow": arrow, "metres": m["length"] * 1000, "secs": m["time"],
            "street": (m.get("street_names") or [None])[0], "b": b, "e": e,
            "rise": elev_at(cum[e]) - elev_at(cum[b]), "count": 1,
        })

    # Climb on foot: rising elevation samples that are not on a lift/escalator.
    starts = [cum[i["b"]] for i in items]
    climb = 0.0
    for k in range(1, len(elev)):
        d = k * step
        idx = max(0, bisect.bisect_right(starts, d) - 1)
        if items and items[idx]["kind"] in ("lift", "escalator"):
            continue
        climb += max(0.0, elev[k] - elev[k - 1])

    stairs_m = sum(i["metres"] for i in items if i["kind"] == "stairs")
    uses = {i["kind"] for i in items} - {"walk"}
    return {
        "minutes": route["time_s"] / 60, "metres": route["length_m"], "climb": climb,
        "stairs": int(round(stairs_m * STEPS_PER_METRE, -1)) if stairs_m else 0,
        "uses": uses, "items": items, "shape": shape, "crossings": find_crossings(shape),
    }


def _seg_dist(p, a, b):
    """Metres from p to segment ab (small distances, flat-earth is fine)."""
    kx = 111320 * math.cos(math.radians(p[0]))
    ky = 110540
    ax, ay = (a[1] - p[1]) * kx, (a[0] - p[0]) * ky
    bx, by = (b[1] - p[1]) * kx, (b[0] - p[0]) * ky
    dx, dy = bx - ax, by - ay
    len2 = dx * dx + dy * dy
    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / len2)) if len2 else 0.0
    return math.hypot(ax + t * dx, ay + t * dy)


def find_crossings(shape) -> list[dict]:
    """Mapped pedestrian crossings the route goes over, in walking order."""
    lats = [p[0] for p in shape]
    lngs = [p[1] for p in shape]
    s_, n_, w_, e_ = min(lats) - 1e-4, max(lats) + 1e-4, min(lngs) - 1e-4, max(lngs) + 1e-4
    found = []
    for c in CROSSINGS:
        if not (s_ <= c["lat"] <= n_ and w_ <= c["lng"] <= e_):
            continue
        pt = (c["lat"], c["lng"])
        best = min(((_seg_dist(pt, shape[k], shape[k + 1]), k) for k in range(len(shape) - 1)), default=None)
        if best and best[0] <= CROSSING_NEAR_M:
            found.append({"kind": c["kind"], "idx": best[1], "lat": c["lat"], "lng": c["lng"]})
    found.sort(key=lambda c: c["idx"])
    # one junction often has several crossing points: keep the best-protected one
    rank = {"signals": 0, "zebra": 1, "crossing": 2, "unmarked": 3}
    out = []
    for c in found:
        if out and haversine_m((c["lat"], c["lng"]), (out[-1]["lat"], out[-1]["lng"])) < 12:
            if rank[c["kind"]] < rank[out[-1]["kind"]]:
                out[-1] = c
            continue
        out.append(c)
    return out


def _crossing_kind_near(pt, radius=15.0) -> str | None:
    """Type of the mapped crossing (from OpenStreetMap) closest to a point."""
    best, best_d = None, radius
    for c in CROSSINGS:
        if abs(c["lat"] - pt[0]) > 3e-4 or abs(c["lng"] - pt[1]) > 4e-4:
            continue
        d = haversine_m(pt, (c["lat"], c["lng"]))
        if d < best_d:
            best, best_d = c["kind"], d
    return best


def add_safety(leg: dict) -> None:
    """How safe a walking leg feels, and exactly where it crosses roads.

    Valhalla describes every piece of the route (pavement, road, crossing,
    steps…). A road is crossed where the route uses a mapped crossing, or where
    it steps off the pavement onto a short piece of road and back (a crossing
    with no markings). The type (traffic lights, zebra…) comes from the nearest
    OpenStreetMap crossing. Passing a crossing without using it does not count.
    """
    edges = svc.trace_edges(leg["shape"])
    quiet = footway = main = total = lit = 0.0
    for e in edges:
        m = (e.get("length") or 0) * 1000
        total += m
        use, cls = e.get("use"), e.get("road_class")
        if use in QUIET_USES:
            quiet += m
        elif use == "footway":
            footway += m
        if (use == "road" and cls in MAIN_CLASSES) or use in ("sidewalk", "pedestrian", "pedestrian_crossing"):
            main += m
        if e.get("lit"):
            lit += m

    shape = leg["shape"]
    crossings = []
    k = 0
    while k < len(edges):
        e = edges[k]
        use = e.get("use")
        if use == "pedestrian_crossing":
            # a mapped crossing can be several short pieces in a row
            j = k
            while j + 1 < len(edges) and edges[j + 1].get("use") == "pedestrian_crossing":
                j += 1
            b_idx = e.get("begin_shape_index", 0)
            e_idx = edges[j].get("end_shape_index", b_idx)
            mid = shape[min((b_idx + e_idx) // 2, len(shape) - 1)]
            signal = any((edges[x].get("end_node") or {}).get("traffic_signal") for x in range(k, j + 1))
            kind = "signals" if signal else (_crossing_kind_near(mid) or "crossing")
            crossings.append({"kind": kind, "idx": b_idx, "lat": mid[0], "lng": mid[1]})
            k = j + 1
            continue
        if (0 < k < len(edges) - 1 and use == "road" and e.get("road_class") in CAR_CLASSES
                and (e.get("length") or 1) * 1000 < 16
                and edges[k - 1].get("use") in PED_USES and edges[k + 1].get("use") in PED_USES):
            idx = e.get("begin_shape_index", 0)
            pt = shape[min(idx, len(shape) - 1)]
            crossings.append({"kind": _crossing_kind_near(pt) or "unmarked", "idx": idx, "lat": pt[0], "lng": pt[1]})
        k += 1
    leg["crossings"] = crossings
    leg["safety"] = {"quiet_m": quiet, "footway_m": footway, "main_m": main, "total_m": total or 1, "lit_m": lit}

    # Steps, from the route pieces themselves: not every server announces
    # stairs as a separate instruction, so they could otherwise go unnoticed.
    flights = [(e.get("begin_shape_index", 0), e.get("end_shape_index", 0), (e.get("length") or 0) * 1000)
               for e in edges if e.get("use") == "steps"]
    leg["flights"] = flights
    steps_m = sum(f[2] for f in flights)
    if steps_m:
        leg["stairs"] = max(leg["stairs"], int(round(steps_m * STEPS_PER_METRE, -1)) or 10)
        leg["uses"] = set(leg["uses"]) | {"stairs"}


def crossing_counts(legs) -> dict:
    out = {"signals": 0, "zebra": 0, "crossing": 0, "unmarked": 0}
    for leg in legs:
        for c in leg.get("crossings", []):
            out[c["kind"]] += 1
    return out


def safety_cost(c: dict) -> float:
    """Extra 'minutes' for things that make a walk feel unsafe for an older person."""
    cost = 0.0
    for leg in c["legs"]:
        sf = leg.get("safety") or {}
        cost += 0.03 * sf.get("quiet_m", 0) + 0.008 * sf.get("footway_m", 0)
    n = crossing_counts(c["legs"])
    return cost + 6 * n["unmarked"] + 1.5 * n["crossing"] + 0.8 * n["zebra"] + 0.3 * n["signals"]


def effort(total_walk_min, climb, stairs, ride_min=0.0, metro=False) -> float:
    return total_walk_min + 0.35 * climb + 0.08 * stairs + 0.4 * ride_min + (1.5 if metro else 0)


# --------------------------------------------------------------------------- step text
def _merge(items):
    """Collapse Valhalla's many tiny maneuvers into steps a person can follow."""
    out = []
    for it in items:
        it = dict(it)
        if out:
            prev = out[-1]
            # tiny connectors (crossing onto a pavement, a few metres of path)
            if it["kind"] == "walk" and it["metres"] < 12 and prev["kind"] != "walk":
                prev["metres"] += it["metres"]; prev["secs"] += it["secs"]; prev["e"] = it["e"]
                continue
            if prev["kind"] == "walk" and prev["metres"] < 12 and it["kind"] == "walk":
                it["metres"] += prev["metres"]; it["secs"] += prev["secs"]; it["b"] = prev["b"]
                out[-1] = it
                continue
            # several escalators / flights of steps in a row
            if it["kind"] == prev["kind"] and it["kind"] != "walk":
                prev["metres"] += it["metres"]; prev["secs"] += it["secs"]; prev["e"] = it["e"]
                prev["rise"] += it["rise"]; prev["count"] += 1
                continue
            # "continue / stay on" joins the previous walking step
            if it["kind"] == "walk" and prev["kind"] == "walk" and it["arrow"] == "straight":
                prev["metres"] += it["metres"]; prev["secs"] += it["secs"]; prev["e"] = it["e"]
                prev["rise"] += it["rise"]
                prev["street"] = prev["street"] or it["street"]
                continue
        out.append(it)
    return out


CHURCH_NAMES = ("chiesa", "basilica", "cattedrale", "duomo", "oratorio", "san ", "santa ", "santo ", "santi ", "sant'")


def _landmark_text(lm, lang):
    word = KIND_WORDS[lang].get(lm["kind"], "")
    # Not every place of worship is a church (e.g. "Islamic Center Perugia").
    if lm["kind"] == "place_of_worship" and not lm["name"].lower().startswith(CHURCH_NAMES):
        word = ""
    if lang == "zh":
        return f"{word}“{lm['name']}”" if word else f"“{lm['name']}”"
    return f'{word} "{lm["name"]}"'.strip() if word else f'"{lm["name"]}"'


def _side(at, heading, lm):
    delta = (bearing(at, (lm["lat"], lm["lng"])) - heading + 540) % 360 - 180
    return "left" if delta < 0 else "right"


def _nearest_landmark(pt, radius, used, box):
    best, best_d = None, radius
    for lm in box:
        if lm["name"] in used:
            continue
        d = haversine_m(pt, (lm["lat"], lm["lng"]))
        rank = KIND_PRIORITY.index(lm["kind"]) if lm["kind"] in KIND_PRIORITY else len(KIND_PRIORITY)
        score = d + rank * 1.5  # prefer an obvious pharmacy/church over a random shop
        if score < best_d:
            best, best_d = lm, score
    return best


def _grade_sentence(rise, metres, lang):
    if metres < 40:
        return ""
    g = rise / metres
    table = {
        "en": ["It is steep uphill. Take your time.", "It goes gently uphill.",
               "It goes gently downhill.", "It is steep downhill. Walk carefully."],
        "zh": ["这段是陡峭的上坡，请慢慢走。", "这段是缓缓的上坡。", "这段是缓缓的下坡。", "这段是陡峭的下坡，请小心走。"],
    }[lang]
    if g > 0.07:
        return table[0]
    if g > 0.035:
        return table[1]
    if g < -0.07:
        return table[3]
    if g < -0.035:
        return table[2]
    return ""


def _decimate(points, min_gap=6.0):
    out = [points[0]]
    for p in points[1:]:
        if haversine_m(out[-1], p) >= min_gap:
            out.append(p)
    if out[-1] != points[-1]:
        out.append(points[-1])
    return [[round(a, 6), round(b, 6)] for a, b in out]


CROSSING_TEXT = {
    "en": {"signals": "Cross the road at the traffic lights. Wait for the green man.",
           "zebra": "Cross at the zebra crossing. Wait until the cars have stopped.",
           "crossing": "Cross at the pedestrian crossing. Look both ways first.",
           "unmarked": "You need to cross the road here, where there is no crossing. Stop, look left and right, and cross only when it is clear."},
    "zh": {"signals": "在红绿灯处过马路。等绿色小人亮了再走。",
           "zebra": "从斑马线过马路。等车停下来再走。",
           "crossing": "从人行横道过马路。先看看两边。",
           "unmarked": "这里没有人行横道，需要过马路。先停下，看左右两边，确认没有车再过。"},
}


def walk_steps(leg: dict, lang: str, used: set, after_metro: bool = False) -> list[dict]:
    shape = leg["shape"]
    lats = [p[0] for p in shape]
    lngs = [p[1] for p in shape]
    s, n, w, e = min(lats) - 0.0005, max(lats) + 0.0005, min(lngs) - 0.0007, max(lngs) + 0.0007
    box = [lm for lm in LANDMARKS if s <= lm["lat"] <= n and w <= lm["lng"] <= e]

    steps = []
    merged = _merge(leg["items"])
    # crossings are announced on walking steps (not inside lifts or on stairs)
    for k, it in enumerate(merged):
        start_pt, end_pt = shape[it["b"]], shape[it["e"]]
        seg = shape[it["b"]:it["e"] + 1] or [start_pt, end_pt]
        heading = bearing(seg[0], seg[min(len(seg) - 1, 3)]) if len(seg) > 1 else 0
        street = it["street"]
        parts = []
        kind = it["kind"]
        up, down = it["rise"] > 2, it["rise"] < -2
        last = k == len(merged) - 1
        step_crossings = [c for c in leg.get("crossings", [])
                          if it["b"] <= c["idx"] < it["e"] or (last and c["idx"] >= it["b"])] if kind == "walk" else []

        if k == 0:
            it["arrow"] = "straight"
        if kind == "walk":
            lm_turn = _nearest_landmark(start_pt, 30, used, box) if it["arrow"] in ("left", "right") else None
            if lang == "zh":
                on = f"走上 {street}" if street else ""
                if k == 0:
                    parts.append(f"{'走出车站，' if after_metro else ''}开始往前走{('，沿着 ' + street) if street else ''}。")
                elif it["arrow"] in ("left", "right"):
                    where = f"在{_landmark_text(lm_turn, lang)}这里" if lm_turn else ""
                    parts.append(f"{where}向{'左' if it['arrow'] == 'left' else '右'}转{('，' + on) if on else ''}。")
                elif it["arrow"] == "uturn":
                    parts.append("请掉头往回走。")
                else:
                    parts.append(f"继续直走{('，沿着 ' + street) if street else ''}。")
            else:
                if k == 0:
                    parts.append(f"{'Leave the station. ' if after_metro else ''}Start walking{(' along ' + street) if street else ''}.")
                elif it["arrow"] in ("left", "right"):
                    where = f" at {_landmark_text(lm_turn, lang)}" if lm_turn else ""
                    parts.append(f"Turn {it['arrow'].upper()}{where}{(' onto ' + street) if street else ''}.")
                elif it["arrow"] == "uturn":
                    parts.append("Turn around and walk back.")
                else:
                    parts.append(f"Keep walking straight{(' along ' + street) if street else ''}.")
            if lm_turn:
                used.add(lm_turn["name"])
            for kind_c in dict.fromkeys(c["kind"] for c in step_crossings):  # each kind once, in order
                parts.append(CROSSING_TEXT[lang][kind_c])
            inside = sum(m for fb, fe, m in leg.get("flights", []) if it["b"] <= fb < max(it["e"], it["b"] + 1))
            if inside >= 1.5:
                n_in = int(round(inside * STEPS_PER_METRE, -1)) or 5
                parts.append(f"这段路上有台阶（大约 {n_in} 级），请扶好扶手。" if lang == "zh"
                             else f"There are steps on the way, about {n_in}. Hold the handrail.")
            grade = _grade_sentence(it["rise"], it["metres"], lang)
            if grade:
                parts.append(grade)
            # something to look out for along the way
            if it["metres"] > 50 and len(seg) > 2:
                mid = seg[len(seg) // 2]
                lm = _nearest_landmark(mid, 25, used, box)
                if lm:
                    side = _side(mid, heading, lm)
                    used.add(lm["name"])
                    if lang == "zh":
                        parts.append(f"您会经过{'左' if side == 'left' else '右'}边的{_landmark_text(lm, lang)}。")
                    else:
                        parts.append(f"You will pass {_landmark_text(lm, lang)} on your {side.upper()}.")
        elif kind == "stairs":
            n_steps = int(round(it["metres"] * STEPS_PER_METRE, -1)) or 10
            kind = "stairs_up" if up else "stairs_down" if down else "stairs"
            if lang == "zh":
                verb = "走上" if up else "走下" if down else "走"
                parts.append(f"{verb}台阶（大约 {n_steps} 级）。请扶着扶手，累了就休息。")
            else:
                verb = "Go UP" if up else "Go DOWN" if down else "Take"
                parts.append(f"{verb} the steps, about {n_steps} of them. Hold the handrail. Rest if you need to.")
        elif kind == "escalator":
            kind = "escalator_up" if up else "escalator_down" if down else "escalator"
            if lang == "zh":
                many = f"（连续 {it['count']} 段）" if it["count"] > 1 else ""
                parts.append(f"乘自动扶梯{'上去' if up else '下去' if down else ''}{many}。扶好扶手，站稳。")
            else:
                many = f" There are {it['count']} in a row." if it["count"] > 1 else ""
                dirn = " up" if up else " down" if down else ""
                parts.append(f"Take the escalator{dirn}.{many} Hold the handrail and stand still.")
        elif kind == "lift":
            if lang == "zh":
                parts.append(f"乘电梯{'上楼' if up else '下楼' if down else ''}。出电梯后继续往前走。")
            else:
                parts.append(f"Take the lift{' up' if up else ' down' if down else ''}. Walk out and carry on.")

        steps.append({
            "kind": kind, "arrow": it["arrow"] if it["kind"] == "walk" else "straight",
            "metres": int(round(it["metres"])), "minutes": max(1, round(it["secs"] / 60)),
            "text": " ".join(parts), "lat": end_pt[0], "lng": end_pt[1], "path": _decimate(seg),
            "crossings": [c["kind"] for c in step_crossings],
        })
    return steps


def metro_step(i: int, j: int, lang: str) -> dict:
    board, alight, towards = mm.STATIONS[i], mm.STATIONS[j], mm.terminus_towards(i, j)
    stops = abs(i - j)
    if lang == "zh":
        text = (f"进入迷你地铁 {board.name} 站。在售票机买票。乘坐开往 {towards.name} 方向的车。"
                f"坐 {stops} 站，在 {alight.name} 下车。" + ("这是终点站。" if alight in (mm.STATIONS[0], mm.STATIONS[-1]) else ""))
    else:
        text = (f"Go into the Minimetrò station {board.name}. Buy a ticket from the machine. "
                f"Take the train towards {towards.name}. Get off at {alight.name}, after {stops} "
                f"stop{'s' if stops > 1 else ''}." + (" It is the last stop." if alight in (mm.STATIONS[0], mm.STATIONS[-1]) else ""))
    return {"kind": "metro", "arrow": "straight", "metres": 0,
            "minutes": round(mm.WAIT_MIN + mm.ride_minutes(i, j)), "text": text,
            "lat": alight.lat, "lng": alight.lng, "board": board.id, "alight": alight.id,
            # the line through the stations, for drawing on the map
            "path": [[s.lat, s.lng] for s in mm.STATIONS[min(i, j):max(i, j) + 1]][:: 1 if j > i else -1]}


# --------------------------------------------------------------------------- planning
def _candidate_walk(start, goal, costing_name):
    leg = analyse(svc.walk_route(start, goal, COSTINGS[costing_name]))
    return {"legs": [leg], "metro": None}


def _score(c, mode):
    walk_min = sum(leg["minutes"] for leg in c["legs"])
    climb = sum(leg["climb"] for leg in c["legs"])
    stairs = sum(leg["stairs"] for leg in c["legs"])
    ride = 0.0
    if c["metro"]:
        i, j = c["metro"]
        ride = mm.WAIT_MIN + mm.ride_minutes(i, j)
    minutes = walk_min + ride
    c.update(minutes=minutes, climb=climb, stairs=stairs)
    if mode == "fastest":
        return minutes
    base = effort(walk_min, climb, stairs, ride, bool(c["metro"]))
    if mode == "safest":
        # still mind hills and steps, but a little less than "easiest"
        return walk_min + 0.2 * climb + 0.05 * stairs + 0.4 * ride + safety_cost(c)
    return base


def plan_candidates(start, goal, mode, hours, now=None, allow_metro=True):
    """All candidate itineraries for a mode, best first."""
    now = now or datetime.now(mm.ROME)
    costings = MODE_COSTINGS[mode]
    jobs = [_POOL.submit(_candidate_walk, start, goal, c) for c in costings]
    matrix_job = None
    if allow_metro:
        pts = [(s.lat, s.lng) for s in mm.STATIONS]
        matrix_job = _POOL.submit(svc.walk_matrix, [start] + pts, pts + [goal], COSTINGS[costings[0]])

    candidates = []
    errors = []
    for j in jobs:
        try:
            candidates.append(j.result())
        except svc.ServiceError as exc:
            errors.append(exc)
    if not candidates:
        raise errors[0]

    direct_min = min(c["legs"][0]["minutes"] for c in candidates)
    if matrix_job and direct_min > METRO_MIN_WALK_MIN:
        try:
            m = matrix_job.result()
        except svc.ServiceError:
            m = None
        if m:
            n = len(mm.STATIONS)
            options = []
            for i in range(n):
                to_st = m[0][i]
                if to_st is None or not mm.is_running(hours, now, margin_min=to_st / 60):
                    continue
                for jdx in range(n):
                    from_st = m[1 + jdx][n]
                    if i == jdx or from_st is None:
                        continue
                    est = to_st / 60 + mm.WAIT_MIN + mm.ride_minutes(i, jdx) + from_st / 60
                    options.append((est, i, jdx))
            options.sort()
            keep = [o for o in options[: (1 if mode == "fastest" else 2)] if o[0] < direct_min * 0.95]
            leg_jobs = []
            for _, i, jdx in keep:
                a = (mm.STATIONS[i].lat, mm.STATIONS[i].lng)
                b = (mm.STATIONS[jdx].lat, mm.STATIONS[jdx].lng)
                leg_jobs.append(((i, jdx), _POOL.submit(svc.walk_route, start, a, COSTINGS[costings[0]]),
                                 _POOL.submit(svc.walk_route, b, goal, COSTINGS[costings[0]])))
            for pair, j1, j2 in leg_jobs:
                try:
                    candidates.append({"legs": [analyse(j1.result()), analyse(j2.result())], "metro": pair})
                except svc.ServiceError:
                    continue

    if mode == "safest" or not svc.PUBLIC_VALHALLA:
        # needs one extra request per leg; the free public server only allows 1/s
        jobs = [_POOL.submit(add_safety, leg) for c in candidates for leg in c["legs"] if "safety" not in leg]
        for j in jobs:
            try:
                j.result()
            except svc.ServiceError:
                pass  # scored on mapped crossings only
    candidates.sort(key=lambda c: _score(c, mode))
    return candidates


def build_plan(c: dict, mode: str, dest_name: str, hint: str, goal, lang: str) -> dict:
    # Find where the chosen route really crosses roads (for the spoken directions).
    jobs = [_POOL.submit(add_safety, leg) for leg in c["legs"] if "safety" not in leg]
    for j in jobs:
        try:
            j.result()
        except svc.ServiceError:
            pass  # keep the rough crossings found from the map alone
    used: set = set()
    steps = walk_steps(c["legs"][0], lang, used)
    uses = set(c["legs"][0]["uses"])
    if c["metro"]:
        i, j = c["metro"]
        steps.append(metro_step(i, j, lang))
        steps += walk_steps(c["legs"][1], lang, used, after_metro=True)
        uses |= c["legs"][1]["uses"] | {"metro"}
    uses = {"stairs" if u.startswith("stairs") else u for u in uses}
    if not c["stairs"] and c["climb"] < 15:
        uses.add("level")
    arrive_text = dest_name + (f" — {hint}" if hint else "")
    steps.append({"kind": "arrive", "arrow": "arrive", "metres": 0, "minutes": 0, "text": arrive_text,
                  "lat": goal[0], "lng": goal[1], "path": []})
    signature = [f"walk:{round(leg['metres'] / 25)}:{round(leg['climb'] / 5)}" for leg in c["legs"]]
    if c["metro"]:
        signature.insert(1, "metro:%d-%d" % c["metro"])
    return {
        "mode": mode, "engine": "live",
        "minutes": max(1, round(c["minutes"])),
        "stairs": c["stairs"], "climb": int(round(c["climb"], -1)) if c["climb"] >= 10 else 0,
        "steep": c["climb"] > 35,
        "uses": sorted(uses), "path": signature, "steps": steps,
        "crossings": crossing_counts(c["legs"]),
        "main_pct": _main_pct(c["legs"]),
    }


def _main_pct(legs) -> int | None:
    sf = [leg["safety"] for leg in legs if leg.get("safety")]
    if not sf:
        return None
    return round(100 * sum(x["main_m"] + x["footway_m"] * 0.5 for x in sf) / sum(x["total_m"] for x in sf))


def plan(start, target, mode: str, lang: str, hours: dict, allow_metro=True, now=None) -> dict:
    lang = "zh" if lang == "zh" else "en"
    goal = (target.lat, target.lng)
    dest_name = target.name.get(lang) or target.name.get("en", "")
    if haversine_m(start, goal) < 20:
        return {"mode": mode, "engine": "live", "minutes": 0, "stairs": 0, "climb": 0, "steep": False,
                "uses": ["level"], "path": ["here"], "main_pct": None,
                "crossings": {"signals": 0, "zebra": 0, "crossing": 0, "unmarked": 0},
                "steps": [{"kind": "arrive", "arrow": "arrive", "metres": 0, "minutes": 0, "text": dest_name,
                           "lat": goal[0], "lng": goal[1], "path": []}]}
    best = plan_candidates(start, goal, mode, hours, now, allow_metro)[0]
    return build_plan(best, mode, dest_name, target.hint, goal, lang)
