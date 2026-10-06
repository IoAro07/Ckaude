"""Tie the written texts to the plan: sizes and sill heights to openings, names and heights to rooms.

Sources of an opening's measures, strongest first: the openings table, the written size next
to it ("120x150", "ht 100"), the elevation, the defaults. The written numbers are centimetres
unless they are clearly millimetres or metres.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from .config import Config
from .geom import polygons_of
from .openings import Opening
from .texts import Word

MIN_ROOM_AREA = 0.8  # m2: a smaller enclosed space is a shaft or a cupboard, not a room
NAME_NEAR = 3.0  # a height written within this many text heights of a room name belongs to the room
ROOM_HEIGHT = (2.0, 6.0)  # m: a tagged number this big is a room height, not a sill
NEAR_LABEL = 0.6  # m: a size this close to an opening is its own even if the width differs
WIDTH_TOLERANCE = 0.15  # m: a farther size must match the measured width this well
HEIGHT_TAGS = ("h", "alt")  # tags that can mean a room height; ht/hf/dt/dav are heights from the ground
FLOOR_WINDOW = 2.0  # m: a window this tall with no written sill starts at the floor
NOTE_DIFF = 0.03  # m: text and elevation closer than this are the same measure


@dataclass
class Room:
    name: str
    polygon: Polygon
    height: float | None = None  # m, from an "h 300" text
    named: bool = False
    words: list[Word] = field(default_factory=list)

    @property
    def area(self) -> float:
        return self.polygon.area

    @property
    def slug(self) -> str:
        return re.sub(r"[^A-Za-z0-9]+", "_", self.name).strip("_") or "Locale"


def text_scale(words: list[Word]) -> float:
    """Metres per written unit: centimetres, unless the numbers are in the thousands (mm) or tiny (m)."""
    firsts = [w.value[0] for w in words if w.kind == "size" and w.value]
    firsts += [w.value for w in words if w.kind == "sill" and w.value is not None and w.tag in ("ht", "dt", "dav")]
    if not firsts:
        return 0.01
    med = statistics.median(firsts)
    if med >= 400:
        return 0.001
    if med < 12:
        return 1.0
    return 0.01


def _reading_order(p: Polygon) -> tuple[int, float]:
    c = p.centroid
    return (-round(c.y / 0.5), c.x)


def find_rooms(solid: BaseGeometry, words: list[Word], scale: float, warnings: list[str]) -> list[Room]:
    """Closed spaces of the walls, with the names and heights written inside them."""
    rings = [Polygon(r) for poly in polygons_of(solid) for r in poly.interiors]
    rings = sorted((r for r in rings if r.area >= MIN_ROOM_AREA), key=_reading_order)
    names = [w for w in words if w.kind == "name"]
    rooms: list[Room] = []
    for k, ring in enumerate(rings, 1):
        inside = [w for w in names if ring.contains(Point(w.x, w.y))]
        known = [w for w in inside if w.value]
        pick = sorted(known or inside[:2], key=lambda w: (-round(w.y / 0.1), w.x))
        text = " / ".join(dict.fromkeys(w.text for w in pick))
        rooms.append(Room(text or f"Locale {k:02d}", ring, None, bool(text), pick))

    # Heights: a tagged number written next to a room's name (or loose inside the room).
    found: dict[int, list[tuple[bool, Word]]] = {}
    for w in words:
        if w.kind != "sill" or w.tag not in HEIGHT_TAGS or w.value is None:
            continue
        if not ROOM_HEIGHT[0] <= w.value * scale <= ROOM_HEIGHT[1]:
            continue
        for k, room in enumerate(rooms):
            if room.polygon.contains(Point(w.x, w.y)):
                near_name = any(math.hypot(n.x - w.x, n.y - w.y) <= NAME_NEAR * w.height + 0.08
                                for n in room.words)
                found.setdefault(k, []).append((near_name, w))
                w.used = True
                break
    for k, cands in found.items():
        room = rooms[k]
        cands.sort(key=lambda c: not c[0])  # next to the name first
        room.height = cands[0][1].value * scale
        for _, w in cands[1:]:
            warnings.append(f"{room.name}: oltre all'altezza {room.height:g} m c'e' una scritta '{w.text}' "
                            f"({w.value * scale:g} m): uso la prima, controlla.")
    return rooms


def wall_height_from_rooms(rooms: list[Room], warnings: list[str]) -> float | None:
    heights = {r.name: r.height for r in rooms if r.height}
    if not heights:
        return None
    best = max(heights.values())
    if len({round(h, 3) for h in heights.values()}) > 1:
        detail = ", ".join(f"{n} {h:g}" for n, h in heights.items())
        warnings.append(f"Locali con altezze diverse ({detail} m): i muri sono tutti alti {best:g} m.")
    return best


# --- openings --------------------------------------------------------------------------------

def _plausible(vals: tuple, scale: float) -> bool:
    """A width/height pair of an opening: 30 cm..6 m wide, 30 cm..4.5 m high (not a date, not "10/20")."""
    return 0.3 <= vals[0] * scale <= 6.0 and 0.3 <= vals[1] * scale <= 4.5


def _distance(w: Word, o: Opening) -> float:
    half = o.width / 2.0
    ux, uy = o.axis
    seg = LineString([(o.center[0] - ux * half, o.center[1] - uy * half),
                      (o.center[0] + ux * half, o.center[1] + uy * half)])
    return Point(w.x, w.y).distance(seg)


def apply_labels(openings: list[Opening], words: list[Word], cfg: Config, scale: float,
                 plan_bounds: tuple[float, float, float, float], warnings: list[str]) -> int:
    """Read sizes and sills from the texts next to each opening. Returns how many openings got a text."""
    if not openings:
        return 0
    radius = cfg.label_radius
    sizes = [w for w in words if w.kind == "size" and not w.used and _plausible(w.value, scale)]
    pairs = []
    for w in sizes:
        width = w.value[0] * scale
        for o in openings:
            d = _distance(w, o)
            if d > radius:
                continue
            miss = abs(width - o.width)
            if d > NEAR_LABEL and miss > WIDTH_TOLERANCE:
                continue
            pairs.append((d + 3.0 * miss, w, o, miss))
    pairs.sort(key=lambda t: t[0])
    matched: dict[int, Word] = {}
    taken: set[int] = set()
    for _, w, o, miss in pairs:
        if id(o) in matched or id(w) in taken:
            continue
        matched[id(o)] = w
        taken.add(id(w))
        w.used = True

    sills = [w for w in words if w.kind == "sill" and not w.used and w.value is not None]
    sill_pairs = []
    for w in sills:
        for o in openings:
            d = _distance(w, o)
            if d <= radius:
                sill_pairs.append((d - (0.3 if id(o) in matched else 0.0), w, o))
    sill_pairs.sort(key=lambda t: t[0])
    sill_of: dict[int, Word] = {}
    for _, w, o in sill_pairs:
        if id(o) in sill_of or w.used:
            continue
        sill_of[id(o)] = w
        w.used = True

    count = 0
    for o in openings:
        size, sill = matched.get(id(o)), sill_of.get(id(o))
        if size is None and sill is None:
            continue
        count += 1
        _apply(o, size, sill, scale, cfg)

    x0, y0, x1, y1 = plan_bounds
    for w in sizes:
        if not w.used and x0 - 3 <= w.x <= x1 + 3 and y0 - 3 <= w.y <= y1 + 3:
            warnings.append(
                f"Scritta '{w.text}' in ({w.x:.2f}, {w.y:.2f}) non associata ad alcuna apertura: "
                f"manca la porta/finestra nel disegno, oppure e' piu' lontana di {radius:g} m "
                "(--raggio-scritte).")
    return count


def _apply(o: Opening, size: Word | None, sill: Word | None, scale: float, cfg: Config) -> None:
    label = [w.text for w in (size, sill) if w is not None]
    o.label = " | ".join(label)
    old = (o.z0, o.z1, o.from_elevation)
    z0 = o.z0
    if sill is not None:
        z0 = sill.value * scale
        o.src["sill"] = "scritta"
    if size is not None:
        vals = size.value
        miss = abs(vals[0] * scale - o.width)
        if miss > 0.05:
            o.notes.append(f"scritta {vals[0] * scale * 100:g} cm ma il simbolo misura {o.width * 100:g} cm: "
                           "tengo la misura del disegno")
        height = vals[1] * scale
        if len(vals) > 2 and sill is None:
            z0 = vals[2] * scale
            o.src["sill"] = "scritta"
        elif sill is None and (o.kind != "window" or height >= FLOOR_WINDOW):
            z0 = 0.0  # a door, or a window as tall as a door (portafinestra): from the floor
            if o.kind == "window":
                o.notes.append(f"altezza {height:g} m senza davanzale scritto: finestra a terra")
        o.z0, o.z1 = z0, z0 + height
        o.src["height"] = "scritta"
    else:
        o.z1 = max(o.z1 - old[0] + z0, z0 + 0.2)  # only the sill is written: keep the height
        o.z0 = z0
    if o.z1 > cfg.wall_height + 1e-6:
        o.notes.append(f"architrave {o.z1:.2f} m oltre l'altezza dei muri {cfg.wall_height:.2f} m: ridotta")
        o.z1 = cfg.wall_height
    if old[2] and (abs(old[0] - o.z0) > NOTE_DIFF or abs(old[1] - o.z1) > NOTE_DIFF):
        o.notes.append(f"scritta e prospetto non coincidono (prospetto {old[0]:.2f}..{old[1]:.2f} m, "
                       f"scritta {o.z0:.2f}..{o.z1:.2f} m): vale la scritta")
    o.from_elevation = False
