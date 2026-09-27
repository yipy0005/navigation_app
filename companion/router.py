"""Easiest / Fastest routing over the curated Perugia network."""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from .perugia import EDGES, NODES, Edge, Step

WALK_M_PER_MIN = 60  # an unhurried pace, about 1 m/s
NEAR_RADIUS_M = 350  # "You are near X" only if X is this close
AT_NODE_M = 40  # closer than this, we skip the "walk to the start" step


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1 = map(math.radians, a)
    lat2, lng2 = map(math.radians, b)
    d = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(d))


def bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1 = map(math.radians, a)
    lat2, lng2 = map(math.radians, b)
    y = math.sin(lng2 - lng1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lng2 - lng1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def nearest_node(pt: tuple[float, float]) -> tuple[str, float]:
    best = min(NODES.values(), key=lambda n: haversine_m(pt, (n.lat, n.lng)))
    return best.id, haversine_m(pt, (best.lat, best.lng))


# Build a directed adjacency list once.
_ADJ: dict[str, list[tuple[str, Edge, tuple[Step, ...]]]] = {nid: [] for nid in NODES}
for _e in EDGES:
    _ADJ[_e.a].append((_e.b, _e, _e.fwd))
    _ADJ[_e.b].append((_e.a, _e, _e.rev))


def _cost(edge: Edge, mode: str) -> float:
    if mode == "fastest":
        return edge.minutes + edge.stairs * 0.001
    # Easiest: stairs and steep slopes are expensive; long walks too.
    return edge.minutes + edge.stairs * 0.12 + (6 if edge.steep else 0) + (12 if edge.long_walk else 0)


def _dijkstra(start: str, goal: str, mode: str):
    dist = {start: 0.0}
    prev: dict[str, tuple[str, Edge, tuple[Step, ...]]] = {}
    pq = [(0.0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == goal:
            break
        if d > dist.get(u, math.inf):
            continue
        for v, edge, steps in _ADJ[u]:
            nd = d + _cost(edge, mode)
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = (u, edge, steps)
                heapq.heappush(pq, (nd, v))
    if goal != start and goal not in prev:
        return None
    path = []
    cur = goal
    while cur != start:
        u, edge, steps = prev[cur]
        path.append((edge, steps))
        cur = u
    path.reverse()
    return path


@dataclass
class Target:
    """Where we are going: a network node or a free point (hotel, pharmacy...)."""

    name: dict[str, str]
    lat: float
    lng: float
    node: str | None = None
    hint: str = ""


def _walk_to_step(dest_name: str, lang: str, metres: int, to: tuple[float, float], hint: str) -> dict:
    if lang == "zh":
        text = f"步行大约 {metres} 米到{dest_name}。"
        if hint:
            text += f" 找：{hint}"
    else:
        text = f"Walk about {metres} metres to {dest_name}."
        if hint:
            text += f" Look for: {hint}"
    return {"kind": "walk", "metres": metres, "text": text, "lat": to[0], "lng": to[1],
            "minutes": max(1, round(metres / WALK_M_PER_MIN))}


def _decorate_turns(steps: list[dict], start: tuple[float, float]) -> None:
    """Give walking steps a left / right / straight arrow from the geometry."""
    prev_pt = start
    prev_heading = None
    prev_kind = None
    for s in steps:
        here = (s["lat"], s["lng"])
        heading = bearing(prev_pt, here) if haversine_m(prev_pt, here) > 5 else prev_heading
        arrow = "straight"
        if s["kind"] == "walk" and prev_kind == "walk" and prev_heading is not None and heading is not None:
            delta = (heading - prev_heading + 540) % 360 - 180
            if delta <= -40:
                arrow = "left"
            elif delta >= 40:
                arrow = "right"
        s["arrow"] = arrow
        prev_pt, prev_heading, prev_kind = here, heading, s["kind"]


def plan(start: tuple[float, float], target: Target, mode: str, lang: str) -> dict:
    lang = "zh" if lang == "zh" else "en"
    start_node, start_gap = nearest_node(start)
    goal_pt = (target.lat, target.lng)
    goal_node = target.node or nearest_node(goal_pt)[0]
    dest_name = target.name.get(lang) or target.name.get("en", "")

    steps: list[dict] = []
    stairs = 0
    uses: set[str] = set()
    steep = False
    node_path = [start_node]

    direct = haversine_m(start, goal_pt)
    if direct < 250 and not target.node:
        # Very close: a single short walk is clearer than any network route.
        steps.append(_walk_to_step(dest_name, lang, int(direct * 1.3), goal_pt, target.hint))
    else:
        if start_gap > AT_NODE_M:
            n = NODES[start_node]
            steps.append(_walk_to_step(n.name[lang], lang, int(start_gap * 1.3), (n.lat, n.lng), ""))
        path = _dijkstra(start_node, goal_node, mode) or []
        for edge, edge_steps in path:
            total_m = sum(s.metres for s in edge_steps) or 1
            for s in edge_steps:
                steps.append({
                    "kind": s.kind, "metres": s.metres, "text": getattr(s, lang),
                    "lat": s.to[0], "lng": s.to[1],
                    "minutes": max(1, round(edge.minutes * s.metres / total_m)),
                })
            stairs += edge.stairs
            steep |= edge.steep
            uses |= edge.uses
            node_path.append(edge.b if node_path[-1] == edge.a else edge.a)
        if not target.node:
            g = NODES[goal_node]
            gap = haversine_m((g.lat, g.lng), goal_pt)
            if gap > 15:
                steps.append(_walk_to_step(dest_name, lang, int(gap * 1.3), goal_pt, target.hint))

    _decorate_turns(steps, start)
    steps.append({"kind": "arrive", "arrow": "arrive", "metres": 0, "minutes": 0,
                  "text": dest_name, "lat": target.lat, "lng": target.lng})
    if not stairs and not steep:
        uses.add("level")
    return {
        "mode": mode,
        "minutes": sum(s["minutes"] for s in steps),
        "stairs": stairs,
        "steep": steep,
        "uses": sorted(uses),
        "path": node_path,
        "steps": steps,
    }


def near_place(pt: tuple[float, float], extra: list[tuple[dict, float, float]], lang: str) -> dict | None:
    """Closest named place to the user, or None if nothing is close."""
    candidates = [(n.name, n.lat, n.lng) for n in NODES.values()] + extra
    best = min(candidates, key=lambda c: haversine_m(pt, (c[1], c[2])))
    d = haversine_m(pt, (best[1], best[2]))
    if d > NEAR_RADIUS_M:
        return None
    return {"name": best[0].get(lang) or best[0].get("en"), "it": best[0].get("it") or best[0].get("en"),
            "metres": round(d)}
