"""Hand-curated walking network for central Perugia.

This is deliberately small. Instead of routing over every street, it models the
handful of links an older visitor actually needs: the flat Corso Vannucci,
the Rocca Paolina escalators, the Pincetto escalators, the Minimetrò, and the
steep stair shortcuts that a fastest-route engine would happily send him down.

Every edge carries the number of stairs and whether it is steep, so the router
can offer an *Easiest* route (default) next to the *Fastest* one.

IMPORTANT: coordinates and wording are approximate. A family member should
walk the routes once and adjust text in this file before relying on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Node:
    id: str
    name: dict[str, str]  # en / zh / it
    lat: float
    lng: float


@dataclass(frozen=True)
class Step:
    """One instruction shown on its own screen.

    kind: walk | stairs_down | stairs_up | escalator_down | escalator_up |
          lift | metro
    ``to`` is where the step ends; it drives GPS auto-advance and the
    left/right/straight arrow for walking steps.
    """

    kind: str
    metres: int
    en: str
    zh: str
    to: tuple[float, float]


@dataclass(frozen=True)
class Edge:
    a: str
    b: str
    minutes: float
    stairs: int = 0
    steep: bool = False
    long_walk: bool = False
    fwd: tuple[Step, ...] = field(default_factory=tuple)  # a -> b
    rev: tuple[Step, ...] = field(default_factory=tuple)  # b -> a

    @property
    def uses(self) -> set[str]:
        kinds = {s.kind for s in self.fwd}
        out = set()
        if kinds & {"escalator_down", "escalator_up"}:
            out.add("escalator")
        if "lift" in kinds:
            out.add("lift")
        if "metro" in kinds:
            out.add("metro")
        if kinds & {"stairs_down", "stairs_up"}:
            out.add("stairs")
        return out


def _n(id_, en, zh, it, lat, lng):
    return Node(id_, {"en": en, "zh": zh, "it": it}, lat, lng)


NODES: dict[str, Node] = {
    n.id: n
    for n in [
        _n("p4nov", "Piazza IV Novembre", "十一月四日广场", "Piazza IV Novembre", 43.11225, 12.38880),
        _n("vannucci", "Corso Vannucci", "万努奇大街", "Corso Vannucci", 43.10990, 12.38865),
        _n("italia", "Piazza Italia", "意大利广场", "Piazza Italia", 43.10760, 12.38865),
        _n("rocca_top", "Rocca Paolina escalators", "保罗城堡自动扶梯", "Scale mobili Rocca Paolina", 43.10720, 12.38905),
        _n("partigiani", "Piazza Partigiani", "游击队员广场", "Piazza Partigiani", 43.10430, 12.38960),
        _n("matteotti", "Piazza Matteotti", "马泰奥蒂广场", "Piazza Matteotti", 43.11100, 12.39000),
        # Coordinates below are from OpenStreetMap (checked Sep 2026).
        _n("pincetto", "Pincetto Minimetrò", "Pincetto 迷你地铁站", "Minimetrò Pincetto", 43.11024, 12.39056),
        _n("cupa", "Cupa Minimetrò", "Cupa 迷你地铁站", "Minimetrò Cupa", 43.11042, 12.38503),
        _n("fontivegge_mm", "Fontivegge Minimetrò", "Fontivegge 迷你地铁站", "Minimetrò Fontivegge", 43.10555, 12.37397),
        _n("stazione", "Perugia Station", "佩鲁贾火车站", "Stazione di Perugia", 43.10420, 12.37613),  # station forecourt, Piazza Vittorio Veneto
    ]
}


def _s(kind, metres, en, zh, node_or_pt):
    to = node_or_pt if isinstance(node_or_pt, tuple) else (NODES[node_or_pt].lat, NODES[node_or_pt].lng)
    return Step(kind, metres, en, zh, to)


VIA_PRIORI_START = (43.11140, 12.38800)
MATTEOTTI_ESCALATOR = (43.11135, 12.39060)

EDGES: list[Edge] = [
    # --- Flat spine of the old town -------------------------------------
    Edge(
        "p4nov", "vannucci", minutes=4,
        fwd=(_s("walk", 250,
                "Put the big fountain behind you. Walk down the wide main street, Corso Vannucci. The big stone palace is on your RIGHT.",
                "背对大喷泉，沿着宽阔的主街万努奇大街往前走。大石头宫殿在您的右边。",
                "vannucci"),),
        rev=(_s("walk", 250,
                "Walk along Corso Vannucci towards the cathedral. The street is flat. You will see the big fountain ahead.",
                "沿万努奇大街朝大教堂方向走。这条街很平。前面会看到大喷泉。",
                "p4nov"),),
    ),
    Edge(
        "vannucci", "italia", minutes=4,
        fwd=(_s("walk", 300,
                "Keep walking along Corso Vannucci. The street stays flat. At the end is a square with trees and benches: Piazza Italia.",
                "继续沿万努奇大街走。路一直很平。尽头是一个有树和长椅的广场：意大利广场。",
                "italia"),),
        rev=(_s("walk", 300,
                "With the gardens behind you, walk into Corso Vannucci, the wide flat street with shops and cafés.",
                "背对花园，走进万努奇大街，一条两边有商店和咖啡馆的宽阔平路。",
                "vannucci"),),
    ),
    Edge(
        "vannucci", "matteotti", minutes=3,
        fwd=(_s("walk", 150,
                "Take the side street Via Mazzini to Piazza Matteotti. It goes gently downhill. No steps.",
                "走旁边的马志尼街去马泰奥蒂广场。路是缓缓的下坡，没有台阶。",
                "matteotti"),),
        rev=(_s("walk", 150,
                "Take the side street Via Mazzini up to Corso Vannucci. It is a gentle slope. No steps.",
                "走马志尼街上到万努奇大街。是一个缓坡，没有台阶。",
                "vannucci"),),
    ),
    # --- Pincetto escalators to the Minimetrò ---------------------------
    Edge(
        "matteotti", "pincetto", minutes=4,
        fwd=(
            _s("walk", 60,
               "Walk to the end of Piazza Matteotti. Look for the blue sign 'Minimetrò' and the covered passage.",
               "走到马泰奥蒂广场的尽头。找蓝色的“Minimetrò”标志和有顶棚的通道。",
               MATTEOTTI_ESCALATOR),
            _s("escalator_down", 150,
               "Ride the escalators down. Hold the handrail. Stand still until the end.",
               "乘自动扶梯下去。扶好扶手。站稳，直到扶梯尽头。",
               "pincetto"),
        ),
        rev=(
            _s("escalator_up", 150,
               "Leave the Minimetrò and ride the escalators up. Hold the handrail.",
               "离开迷你地铁站，乘自动扶梯上去。扶好扶手。",
               MATTEOTTI_ESCALATOR),
            _s("walk", 60,
               "At the top you are in Piazza Matteotti, a long square with a market building.",
               "到了上面就是马泰奥蒂广场，一个有市场大楼的长广场。",
               "matteotti"),
        ),
    ),
    # --- Rocca Paolina escalators ----------------------------------------
    Edge(
        "italia", "rocca_top", minutes=2,
        fwd=(_s("walk", 60,
                "Walk to the large building at the end of the square. The escalator entrance is under the arches.",
                "走到广场尽头的大楼。自动扶梯的入口在拱廊下面。",
                "rocca_top"),),
        rev=(_s("walk", 60,
                "Step out of the building. You are in Piazza Italia, the square with trees.",
                "走出大楼。您就在意大利广场，有树的那个广场。",
                "italia"),),
    ),
    Edge(
        "rocca_top", "partigiani", minutes=7,
        fwd=(_s("escalator_down", 250,
                "Ride the escalators down through the old underground fortress. There are several in a row. Hold the handrail.",
                "乘自动扶梯穿过古老的地下城堡往下走。有好几段连在一起。扶好扶手。",
                "partigiani"),),
        rev=(_s("escalator_up", 250,
                "Go into the escalator entrance and ride up through the old fortress. There are several in a row. Hold the handrail.",
                "走进自动扶梯入口，穿过古老的城堡往上走。有好几段连在一起。扶好扶手。",
                "rocca_top"),),
    ),
    # --- Steep shortcuts (Fastest likes these, Easiest avoids them) -----
    Edge(
        "p4nov", "pincetto", minutes=5, stairs=140, steep=True,
        fwd=(_s("stairs_down", 350,
                "Take the steep old stone steps down towards Pincetto. There is no lift on this way. Use the handrail.",
                "走陡峭的老石阶下到 Pincetto。这条路没有电梯。请扶着扶手。",
                "pincetto"),),
        rev=(_s("stairs_up", 350,
                "Climb the steep old stone steps up to the cathedral square. Rest whenever you need to.",
                "爬陡峭的老石阶上到大教堂广场。累了随时休息。",
                "p4nov"),),
    ),
    Edge(
        "p4nov", "cupa", minutes=7, stairs=220, steep=True,
        fwd=(
            _s("walk", 120,
               "Walk into Via dei Priori, the narrow street under the arch next to the big palace.",
               "走进大宫殿旁边拱门下的那条窄街：普里奥里街。",
               VIA_PRIORI_START),
            _s("stairs_down", 450,
               "Go down the long steep street and steps to the Minimetrò Cupa station.",
               "沿着又长又陡的街道和台阶一直往下走，到 Cupa 迷你地铁站。",
               "cupa"),
        ),
        rev=(
            _s("stairs_up", 450,
               "Climb the long steep street and steps up to the big palace. It is hard work. Rest when you need to.",
               "沿又长又陡的街道和台阶往上爬到大宫殿。比较吃力，需要时请休息。",
               VIA_PRIORI_START),
            _s("walk", 120,
               "Go through the arch. You are next to the big fountain.",
               "穿过拱门。您就在大喷泉旁边。",
               "p4nov"),
        ),
    ),
    Edge(
        "italia", "partigiani", minutes=5, stairs=160, steep=True,
        fwd=(_s("stairs_down", 400,
                "Take the steep steps down from the gardens to Piazza Partigiani. Use the handrail.",
                "从花园走陡峭的台阶下到游击队员广场。请扶着扶手。",
                "partigiani"),),
        rev=(_s("stairs_up", 400,
                "Climb the steep steps up to the gardens of Piazza Italia. Rest when you need to.",
                "爬陡峭的台阶上到意大利广场的花园。累了请休息。",
                "italia"),),
    ),
    Edge(
        "partigiani", "stazione", minutes=24, long_walk=True,
        fwd=(_s("walk", 1600,
                "This is a long walk of about 1.6 km down to the railway station. A taxi or the Minimetrò is easier.",
                "去火车站要走大约 1.6 公里的长路。坐出租车或迷你地铁会更轻松。",
                "stazione"),),
        rev=(_s("walk", 1600,
                "This is a long uphill walk of about 1.6 km. A taxi or the Minimetrò is easier.",
                "这是大约 1.6 公里的上坡长路。坐出租车或迷你地铁会更轻松。",
                "partigiani"),),
    ),
    # --- Minimetrò (minutes include about 3 minutes of waiting) --------
    Edge(
        "pincetto", "fontivegge_mm", minutes=8,
        fwd=(_s("metro", 2200,
                "Take the Minimetrò towards Pian di Massiano. Get off at Fontivegge, the 3rd stop. Trains come every few minutes.",
                "乘迷你地铁往 Pian di Massiano 方向。在第 3 站 Fontivegge 下车。每隔几分钟就有一班。",
                "fontivegge_mm"),),
        rev=(_s("metro", 2200,
                "Take the Minimetrò towards Pincetto. Stay on until the last stop, Pincetto. Trains come every few minutes.",
                "乘迷你地铁往 Pincetto 方向。坐到终点站 Pincetto 再下车。每隔几分钟就有一班。",
                "pincetto"),),
    ),
    Edge(
        "cupa", "fontivegge_mm", minutes=6,
        fwd=(_s("metro", 1500,
                "Take the Minimetrò towards Pian di Massiano. Get off at Fontivegge, the 2nd stop.",
                "乘迷你地铁往 Pian di Massiano 方向。在第 2 站 Fontivegge 下车。",
                "fontivegge_mm"),),
        rev=(_s("metro", 1500,
                "Take the Minimetrò towards Pincetto. Get off at Cupa, the 2nd stop.",
                "乘迷你地铁往 Pincetto 方向。在第 2 站 Cupa 下车。",
                "cupa"),),
    ),
    Edge(
        "cupa", "pincetto", minutes=4,
        fwd=(_s("metro", 700,
                "Take the Minimetrò towards Pincetto. Get off at the next stop, Pincetto, the end of the line.",
                "乘迷你地铁往 Pincetto 方向。下一站就是终点站 Pincetto。",
                "pincetto"),),
        rev=(_s("metro", 700,
                "Take the Minimetrò towards Pian di Massiano. Get off at the next stop, Cupa.",
                "乘迷你地铁往 Pian di Massiano 方向。在下一站 Cupa 下车。",
                "cupa"),),
    ),
    Edge(
        "fontivegge_mm", "stazione", minutes=3,
        fwd=(_s("walk", 250,
                "Leave the Minimetrò station and walk across the square. The railway station is the long building in front of you.",
                "走出迷你地铁站，穿过广场。前面那栋长长的建筑就是火车站。",
                "stazione"),),
        rev=(_s("walk", 250,
                "Come out of the railway station and cross the square. Look for the Minimetrò sign and the lift.",
                "走出火车站，穿过广场。找迷你地铁的标志和电梯。",
                "fontivegge_mm"),),
    ),
]

# Default destinations a family member can change on the Setup page.
DEFAULT_CONFIG = {
    "configured": False,
    "home": {
        "name": "My hotel",
        "address": "Please set the hotel address in Family setup",
        "lat": 43.10690,
        "lng": 12.38790,
        "hint": "",
    },
    "contact": {"name": "Family", "phone": ""},
    "taxi_phone": "+39 075 5004888",
    "emergency_phone": "112",
    # Used when GPS is unavailable (e.g. testing at home, or permission denied)
    "fallback_location": {"lat": 43.11225, "lng": 12.38880},
    # Test mode: ignore GPS and pretend to be at the fallback location.
    # Only for trying the app at home; switched on from Family setup.
    "pretend_location": False,
    "default_mode": "easiest",  # or "safest": which route the big START button uses
    "metro_hours": None,  # filled from minimetro.DEFAULT_HOURS
    "destinations": [
        {"id": "station", "icon": "train", "name": {"en": "Perugia Station", "zh": "佩鲁贾火车站"}, "node": "stazione"},
        {"id": "centre", "icon": "fountain", "name": {"en": "City centre", "zh": "市中心"}, "node": "p4nov"},
        {"id": "pincetto", "icon": "metro", "name": {"en": "Pincetto Minimetrò", "zh": "Pincetto 迷你地铁站"}, "node": "pincetto"},
        {"id": "hospital", "icon": "hospital",
         "name": {"en": "Hospital", "zh": "医院"},
         "address": "Ospedale Santa Maria della Misericordia, Piazzale Giorgio Menghini 3, 06129 Perugia",
         "lat": 43.07915, "lng": 12.35530, "taxi_only": True},
        {"id": "pharmacy", "icon": "pill",
         "name": {"en": "Pharmacy", "zh": "药店"},
         "address": "Farmacia Lemmi, Corso Vannucci 57, 06121 Perugia", "lat": 43.11043, "lng": 12.38830,
         "hint": "Green cross sign"},
        {"id": "restaurant", "icon": "food",
         "name": {"en": "Favourite restaurant", "zh": "喜欢的餐厅"},
         "address": "Piazza IV Novembre, Perugia", "lat": 43.11200, "lng": 12.38930, "hint": ""},
    ],
}
