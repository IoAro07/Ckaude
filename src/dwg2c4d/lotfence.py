"""The fence of the lot: a low wall with a cap, square pillars, a railing on top, a gate gap, a thin wire fence.

What a lot boundary looks like in a plan, drawn on any layer that is not a wall of the house (the shapes decide, the names
help): the low wall is a pair of long parallel lines 8-40 cm apart (or one thin closed outline), the pillars are small closed
squares or circles at its ends and corners and at both sides of a gate, the railing that stands on the wall is a pair of thin
lines 1-4 cm apart along it, the gate is a gap between two pillars, and a thin wire fence is a single long line that joins a
pillar to the building or to another part of the boundary. A pair of lines alone proves nothing (a paving joint, a kerb, a
bed edge are pairs too): a run is taken for the boundary only when a pillar stands at one of its ends, or a railing runs along
it, or the name of its layer says what it is (Muretto, Recinzione, Ringhiera...); a railing and a pillar need the same proofs.
The layers the names call fence are read whatever else their names say (Muretto is a wall layer for the names, and so would
be built as a wall of the house): what they give is taken out of the walls.

Heights and design come from the elevation drawn in line with the plan (a front elevation below it): the ground line, the top
of the wall and its cap, the top of the pillars and their caps, the rails and the spacing of the bars of the railing. What the
elevation does not give is a default (``DEFAULT_*``) and the report says which it was.

The result is built as groups of their own (Muretto, Copertina, Pilastri_lotto, Ringhiera, Recinzione_pali, Recinzione_rete):
see ``add_lot_fence``. The pipeline calls ``detect_lot_fence`` before the walls (the layers it takes leave the walls) and
``finish_lot_fence`` when the building is known (what lies in it is no fence; the wire fences join the building).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, substring
from shapely.strtree import STRtree

from .config import Config
from .geom import polygons_of, union
from .mesh import Mesh, Slab
from .reader import Item, _entity_prims, layer_used, read_items

LINE_ENTITIES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE"}  # what a boundary is drawn with
NAME_WORDS = ("murett", "recinz", "ringhier", "cancell", "inferriat", "fence", "railing", "gate", "confin", "boundary")  # layer names that say it

WALL_THICKNESS = (0.08, 0.40)  # m: a low wall is drawn as two lines this far apart
RAIL_GAP = (0.008, 0.04)  # m: a railing is drawn as two thin lines this far apart (closer: one line drawn twice)
MIN_RUN = 1.5  # m: a run of wall shorter than this is a piece of something else
MIN_RAIL_RUN = 1.0  # m: the same for a railing
SAMPLE = 0.02  # m: spacing of the points at which two lines are compared
PILLAR_SIDE = (0.12, 0.60)  # m: sides of a closed outline that can be a pillar
PILLAR_ASPECT = 1.3  # its sides differ by less than this ratio
TOUCH = 0.05  # m: touching is being this close
MAX_CHAINS = 3000  # a drawing with more lines than this in the plan is not searched
MAX_ENTITIES = 400_000  # a drawing with more lines than this in the plan is not looked at
GATE_GAP = (0.6, 6.0)  # m: the gap between two pillars that is a gate
WIRE_MIN = 3.0  # m: a single line shorter than this is no fence
MIN_STRIP_AREA = 0.15  # m2: a smaller strip is no wall
SAME_THICKNESS = 0.03  # m: strips that touch and differ by less than this in thickness are of one boundary
INSIDE = 0.5  # a strip or a railing with more than this share in the building is no fence

DEFAULT_WALL = 0.80  # m: top of the wall, cap included, above the ground
DEFAULT_CAP = 0.04  # m: thickness of the cap of the wall
CAP_OVERHANG = 0.015  # m: the cap is this much wider than the wall at each side
DEFAULT_PILLAR_RISE = 0.70  # m: the pillars stand this much higher than the wall
DEFAULT_PILLAR_CAP = 0.04  # m: thickness of the cap of a pillar
PILLAR_CAP_OVERHANG = 0.02  # m: the cap of a pillar is this much wider than it at each side
DEFAULT_RAIL = 0.60  # m: height of the railing over the wall
DEFAULT_PITCH = 0.12  # m: distance between two bars
DEFAULT_BAR = 0.012  # m: side of a bar
RAIL_SECTION = 0.02  # m: side of the rails of a railing
DEFAULT_FOOT = 0.06  # m: the bottom rail of a railing the elevation does not draw is this far over the wall
DEFAULT_WIRE = 1.20  # m: height of a wire fence
WIRE_POST = 0.05  # m: side of the posts of a wire fence
WIRE_SPAN = 2.5  # m: distance between two posts
WIRE_MESH = 0.006  # m: thickness of the panel of the mesh
WIRE_FOOT = 0.10  # m: the mesh begins this far from the ground
MAX_BARS = 6000  # a railing with more bars than this is built with a bar every two (the faces stay few)
STEP = 2.0  # m: on sloping ground the rails of a railing step from piece to piece this long


@dataclass
class Rail:
    """A horizontal bar of the railing: from ``z0`` to ``z1`` above the top of the wall."""

    z0: float
    z1: float


@dataclass
class Profile:
    """Heights (m, above the ground) and design of the fence; ``sources`` tells where each came from (prospetto | predefinita |
    indicata)."""

    wall: float = DEFAULT_WALL  # top of the wall, cap included
    cap: float = DEFAULT_CAP
    pillar: float = DEFAULT_WALL + DEFAULT_PILLAR_RISE  # top of the pillars, cap included
    pillar_cap: float = DEFAULT_PILLAR_CAP
    rail: float = DEFAULT_RAIL  # top of the railing over the wall
    pitch: float = DEFAULT_PITCH
    bar: float = DEFAULT_BAR
    rails: list[Rail] = field(default_factory=list)  # the rails the elevation draws
    wire: float = DEFAULT_WIRE
    sources: dict[str, str] = field(default_factory=lambda: {"muretto": "predefinita", "pilastri": "predefinita",
                                                              "ringhiera": "predefinita", "rete": "predefinita"})

    def rail_bars(self) -> list[Rail]:
        """The rails of the railing: the ones the elevation draws, else a bottom and a top one."""
        return self.rails or [Rail(DEFAULT_FOOT, DEFAULT_FOOT + RAIL_SECTION), Rail(self.rail - RAIL_SECTION, self.rail)]


@dataclass
class Gate:
    x: float
    y: float
    width: float  # m, between the pillars
    angle: float  # radians: the direction of the wall it interrupts


@dataclass
class LotFence:
    walls: BaseGeometry = field(default_factory=Polygon)  # the footprint of the low wall
    pillars: list[Polygon] = field(default_factory=list)
    railings: list[LineString] = field(default_factory=list)  # the axes of the railing, along the wall or free
    wires: list[LineString] = field(default_factory=list)  # the axes of the wire fences
    gates: list[Gate] = field(default_factory=list)
    profile: Profile = field(default_factory=Profile)
    layers: set[str] = field(default_factory=set)  # the layers the elements were found on
    chains: list[tuple[str, LineString]] = field(default_factory=list, repr=False)  # the lines of the plan, for the wire fences

    @property
    def empty(self) -> bool:
        return self.walls.is_empty and not self.pillars and not self.railings and not self.wires

    @property
    def footprint(self) -> BaseGeometry:
        """What the fence covers on the ground: the wall and the pillars (the ground is cut round them)."""
        return union([self.walls, *self.pillars])

    def lengths(self) -> tuple[float, float, float]:
        """(metres of wall, metres of railing, metres of wire fence)."""
        wall = sum(_strip_length(p) for p in polygons_of(self.walls))
        return wall, sum(r.length for r in self.railings), sum(w.length for w in self.wires)


def _strip_length(strip: Polygon) -> float:
    """The length of a strip of wall: half of its perimeter less its width."""
    return strip.length / 2.0 - _thickness(strip)


# --- reading the plan ------------------------------------------------------------------------

def name_says_fence(layer: str) -> bool:
    """Does the name of a layer say it holds a fence (Muretto, Recinzione, Ringhiera, Cancello...)?"""
    return any(t.startswith(NAME_WORDS) for t in re.split(r"[^a-z]+", layer.lower()) if t)


def _user_layer(cfg: Config, layer: str) -> bool:
    """Did the user (with --muri, --pilastri...) say what the layer is? The lists the program made itself (the layers the
    analysis chose, the proposals he accepted) do not count."""
    rules = cfg.layers
    return any(rules.matches(cat, layer) for cat in rules.overrides
               if cat not in cfg.proposed and not (cat == "wall" and cfg.walls_guessed))


def _eligible(cfg: Config, layer: str) -> bool:
    """Is the linework of a layer looked at? Not the layers the names call walls, doors, windows, columns... (they are the
    building), the furniture, the green, the texts, the elevations: unless the name says fence; and never what the user said."""
    rules = cfg.layers
    if _user_layer(cfg, layer) or rules.vetoed(layer):
        return False
    return name_says_fence(layer) or rules.classify_layer(layer) is None


def _rough(e) -> tuple[float, float, float, float] | None:
    """The box of an entity in drawing units, cheaply (no curve is flattened)."""
    t = e.dxftype()
    try:
        if t == "LINE":
            s, f = e.dxf.start, e.dxf.end
            return min(s.x, f.x), min(s.y, f.y), max(s.x, f.x), max(s.y, f.y)
        if t == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in e.get_points("xy")]
        elif t == "POLYLINE":
            pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
        else:
            c, r = e.dxf.center, e.dxf.radius
            return c.x - r, c.y - r, c.x + r, c.y + r
    except Exception:
        return None
    if not pts:
        return None
    xs, ys = zip(*pts)
    return min(xs), min(ys), max(xs), max(ys)


@dataclass
class Linework:
    lines: list[tuple[str, LineString]] = field(default_factory=list)  # open, with the layer they are on
    rings: list[tuple[str, Polygon]] = field(default_factory=list)  # closed


def read_linework(doc, cfg: Config, unit_scale: float, window, skip: list[tuple[float, float, float, float]]) -> Linework:
    """The lines, polylines, arcs and circles of the plan (modelspace; drawing units in, metres out) on the layers a boundary can
    be drawn on. ``window``: the box of the plan (drawing units) or None; ``skip``: boxes of other views (an elevation has
    pillars and walls too)."""
    out = Linework()
    dist = cfg.arc_tolerance / unit_scale
    seen = 0
    ok: dict[str, bool] = {}
    for e in doc.modelspace():
        if e.dxftype() not in LINE_ENTITIES:
            continue
        layer = e.dxf.layer
        if layer not in ok:
            ok[layer] = layer_used(doc, cfg, layer) and _eligible(cfg, layer)
        if not ok[layer]:
            continue
        seen += 1
        if seen > MAX_ENTITIES:
            return Linework()
        b = _rough(e)
        if b is None:
            continue
        cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
        if window is not None and not (window[0] <= cx <= window[2] and window[1] <= cy <= window[3]):
            continue
        if any(s[0] <= cx <= s[2] and s[1] <= cy <= s[3] for s in skip):
            continue
        try:
            prims = _entity_prims(e, dist)
        except Exception:
            continue
        for p in prims:
            geom = affinity.scale(p.geom, unit_scale, unit_scale, origin=(0, 0))
            if geom.is_empty:
                continue
            if p.kind == "ring" and geom.geom_type == "Polygon":
                out.rings.append((layer, geom))
            elif p.kind == "line" and geom.geom_type == "LineString":
                out.lines.append((layer, geom))
    return out


# --- shapes ----------------------------------------------------------------------------------

def _is_pillar(poly: Polygon) -> bool:
    """A closed outline the size of a pillar and about as wide as long: a square, a circle."""
    x0, y0, x1, y1 = poly.bounds
    w, h = x1 - x0, y1 - y0
    lo, hi = PILLAR_SIDE
    return lo <= w <= hi and lo <= h <= hi and max(w, h) <= PILLAR_ASPECT * min(w, h) and poly.area >= 0.7 * w * h


def _thickness(poly: Polygon) -> float:
    """Average thickness: 2 * area / perimeter (a strip of width t gives t)."""
    return 2.0 * poly.area / poly.length if poly.length else 0.0


def _dedupe(lines: list[tuple[str, LineString]]) -> list[tuple[str, LineString]]:
    """The lines without the ones drawn twice (the same ends, either way round)."""
    seen: set[bytes] = set()
    out = []
    for layer, g in lines:
        key = shapely.to_wkb(shapely.normalize(shapely.set_precision(g, 1e-4)))
        if key not in seen:
            seen.add(key)
            out.append((layer, g))
    return out


def _chains(lines: list[tuple[str, LineString]]) -> list[tuple[str, LineString]]:
    """The lines of each layer joined end to end wherever exactly two of them meet: a wall drawn in pieces is one chain."""
    by_layer: dict[str, list[LineString]] = {}
    for layer, g in _dedupe(lines):
        by_layer.setdefault(layer, []).append(g)
    out = []
    for layer, parts in by_layer.items():
        merged = linemerge(parts) if len(parts) > 1 else parts[0]
        out.extend((layer, g) for g in getattr(merged, "geoms", [merged]) if g.length > 0)
    return out


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """The first and last index of every stretch of True in ``mask``."""
    if not mask.any():
        return []
    edges = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    return list(zip(np.flatnonzero(edges == 1).tolist(), (np.flatnonzero(edges == -1) - 1).tolist()))


def _pair_runs(a: LineString, b: LineString, gap: tuple[float, float], min_run: float) -> list[tuple[LineString, LineString, float]]:
    """The stretches where line ``a`` runs along line ``b`` at a distance within ``gap``: [(the part of a, the part of b that
    faces it, the median distance)], each at least ``min_run`` long."""
    at = np.linspace(0.0, a.length, max(int(a.length / SAMPLE), 2) + 1)
    d = shapely.distance(shapely.line_interpolate_point(a, at), b)
    out = []
    for i0, i1 in _runs((d >= gap[0]) & (d <= gap[1])):
        s0, s1 = float(at[i0]), float(at[i1])
        if s1 - s0 < min_run:
            continue
        part = substring(a, s0, s1)
        t0, t1 = b.project(Point(part.coords[0])), b.project(Point(part.coords[-1]))
        if abs(t1 - t0) < 0.8 * min_run:
            continue
        facing = substring(b, min(t0, t1), max(t0, t1))
        if t0 > t1:
            facing = LineString(list(facing.coords)[::-1])
        out.append((part, facing, float(np.median(d[i0:i1 + 1]))))
    return out


def _ribbon(a: LineString, b: LineString) -> Polygon | None:
    """The strip between two lines that run side by side: the points of a, then those of b back."""
    poly = Polygon(list(a.coords) + list(b.coords)[::-1])
    if not poly.is_valid:
        poly = poly.buffer(0)
    polys = polygons_of(poly)
    return max(polys, key=lambda p: p.area) if polys else None


def _midline(a: LineString, b: LineString) -> LineString:
    """The line half way between two lines that run side by side."""
    pts = []
    for d in np.linspace(0.0, a.length, max(int(a.length / 0.1), 1) + 1):
        p = a.interpolate(d)
        q = b.interpolate(b.project(p))
        pts.append(((p.x + q.x) / 2.0, (p.y + q.y) / 2.0))
    return LineString(pts).simplify(0.002)


@dataclass
class _Pair:
    layer: str
    strip: Polygon
    axis: LineString
    distance: float


def _pairs(chains: list[tuple[str, LineString]], gap: tuple[float, float], min_run: float) -> list[_Pair]:
    """Every stretch where two chains run side by side at a distance within ``gap``."""
    if len(chains) > MAX_CHAINS:
        return []
    geoms = [g for _, g in chains]
    tree = STRtree(geoms)
    found = []
    for i, (layer, a) in enumerate(chains):
        if a.length < min_run:
            continue
        for j in tree.query(a.buffer(gap[1])):
            if int(j) <= i or geoms[int(j)].length < min_run:
                continue
            for part, facing, dist in _pair_runs(a, geoms[int(j)], gap, min_run):
                strip = _ribbon(part, facing)
                if strip is not None and strip.area >= 0.5 * part.length * dist:
                    found.append(_Pair(layer, strip, _midline(part, facing), dist))
    return found


def _end_sides(strip: Polygon) -> list[LineString]:
    """The two short sides of a strip (of the box that holds it, turned to fit)."""
    c = list(shapely.minimum_rotated_rectangle(strip).exterior.coords)[:4]
    sides = sorted((LineString([c[k], c[(k + 1) % 4]]) for k in range(4)), key=lambda s: s.length)
    return sides[:2]


# --- the elements ----------------------------------------------------------------------------

@dataclass
class _Strip:
    body: Polygon
    axis: LineString
    thickness: float
    layer: str
    sure: bool = False  # a name, a pillar or a railing vouches for it


def _wall_strips(rings: list[tuple[str, Polygon]], pairs: list[_Pair]) -> list[_Strip]:
    """The wall strips: thin long closed outlines, and the stretches where two lines run side by side. Where strips overlap
    (three lines make three) the narrowest are kept."""
    found: list[_Strip] = []
    for layer, poly in rings:
        x0, y0, x1, y1 = poly.bounds
        t = _thickness(poly)
        if WALL_THICKNESS[0] <= t <= WALL_THICKNESS[1] and max(x1 - x0, y1 - y0) >= MIN_RUN and not poly.interiors:
            ends = _end_sides(poly)
            found.append(_Strip(poly, LineString([ends[0].centroid.coords[0], ends[1].centroid.coords[0]]), t, layer))
    found.extend(_Strip(p.strip, p.axis, p.distance, p.layer) for p in pairs)
    found.sort(key=lambda s: s.thickness)
    kept: list[_Strip] = []
    covered: BaseGeometry = Polygon()
    for s in found:
        if s.body.area < MIN_STRIP_AREA or (not covered.is_empty and s.body.intersection(covered).area > 0.3 * s.body.area):
            continue
        kept.append(s)
        covered = covered.union(s.body)
    return kept


def _without_railings(chains: list[tuple[str, LineString]], rails: list[_Pair]) -> list[tuple[str, LineString]]:
    """The chains without the stretches that make up the railings found (the two lines of a railing are no faces of a wall)."""
    if not rails:
        return chains
    cover = union([r.axis.buffer(RAIL_GAP[1] / 2.0 + 0.005) for r in rails])
    out = []
    for layer, g in chains:
        rest = g.difference(cover)
        out.extend((layer, p) for p in getattr(rest, "geoms", [rest]) if p.geom_type == "LineString" and p.length > 0)
    return _chains(out)


def detect_lot_fence(doc, cfg: Config, unit_scale: float, skip: list) -> LotFence | None:
    """The fence of the lot in the plan (the pipeline calls it before the walls are read), or None. ``skip``: boxes (drawing
    units) of the other views of the sheet. The building is not known yet: ``finish_lot_fence`` takes out what lies in it."""
    lines = read_linework(doc, cfg, unit_scale, cfg.area, skip)
    chains = _chains(lines.lines)
    rails = _pairs(chains, RAIL_GAP, MIN_RAIL_RUN)
    strips = _wall_strips([(l, p) for l, p in lines.rings if not _is_pillar(p)],
                          _pairs(_without_railings(chains, rails), WALL_THICKNESS, MIN_RUN))
    if not strips and not rails:
        return None
    candidates = [(layer, p) for layer, p in lines.rings if _is_pillar(p)]
    tree = STRtree([p for _, p in candidates]) if candidates else None

    def pillars_at(geom: BaseGeometry) -> list[int]:
        return [int(i) for i in tree.query(geom.buffer(TOUCH))] if tree is not None else []

    for s in strips:
        s.sure = name_says_fence(s.layer) or any(pillars_at(e) for e in _end_sides(s.body))
    wall_union = union([s.body for s in strips]).buffer(0.01)
    railings: list[tuple[str, LineString, bool]] = []  # (layer, axis, stands on a wall)
    for r in rails:
        on_wall = not wall_union.is_empty and r.axis.intersection(wall_union).length >= 0.8 * r.axis.length
        if on_wall or name_says_fence(r.layer) or any(pillars_at(Point(c)) for c in (r.axis.coords[0], r.axis.coords[-1])):
            railings.append((r.layer, r.axis, on_wall))
    for s in strips:  # a railing on a strip vouches for it
        if not s.sure and any(on and s.body.buffer(0.01).contains(axis.interpolate(0.5, normalized=True)) for _, axis, on in railings):
            s.sure = True
    changed = True
    while changed:  # strips that touch a sure one, of about its thickness, are of the same boundary
        changed = False
        for s in strips:
            if not s.sure and any(o.sure and abs(o.thickness - s.thickness) <= SAME_THICKNESS and s.body.distance(o.body) <= TOUCH
                                  for o in strips):
                s.sure, changed = True, True
    strips = [s for s in strips if s.sure]

    chosen = {k for k, (layer, _) in enumerate(candidates) if name_says_fence(layer)}  # a name vouches for a pillar
    for s in strips:
        chosen.update(pillars_at(s.body))
    for _, axis, _ in railings:
        chosen.update(pillars_at(axis))
    pillars = [candidates[i][1] for i in sorted(chosen)]
    fence = LotFence(walls=union([s.body for s in strips]).difference(union(pillars).buffer(0.002)), pillars=pillars, chains=chains)
    fence.gates = _gates(pillars, fence.walls)
    leaves = union(_gate_zone(g) for g in fence.gates)
    fence.railings = [axis for _, axis, _ in railings if leaves.is_empty or not leaves.contains(axis)]  # the leaf of a gate is left open
    fence.layers = {s.layer for s in strips} | {layer for layer, _, _ in railings} | {candidates[i][0] for i in chosen}
    return None if fence.empty else fence


def _gates(pillars: list[Polygon], walls: BaseGeometry) -> list[Gate]:
    """The gaps between two pillars that face each other: the wall stops at each and nothing joins them."""
    out: list[Gate] = []
    reach = walls.buffer(TOUCH)
    for i, a in enumerate(pillars):
        for b in pillars[i + 1:]:
            ca, cb = a.centroid, b.centroid
            dx, dy = cb.x - ca.x, cb.y - ca.y
            across_x = abs(dx) > abs(dy)
            aligned = abs(dy if across_x else dx) <= TOUCH
            sizes = (a.bounds[2] - a.bounds[0] + b.bounds[2] - b.bounds[0]) if across_x else (a.bounds[3] - a.bounds[1] + b.bounds[3] - b.bounds[1])
            gap = abs(dx if across_x else dy) - sizes / 2.0
            if not aligned or not GATE_GAP[0] <= gap <= GATE_GAP[1] or not (reach.intersects(a) and reach.intersects(b)):
                continue
            between = LineString([(ca.x, ca.y), (cb.x, cb.y)]).difference(a).difference(b)
            if not walls.is_empty and between.intersection(walls).length > 0.1:
                continue  # a wall joins them: no gate
            mid = between.interpolate(0.5, normalized=True)
            out.append(Gate(mid.x, mid.y, gap, 0.0 if across_x else math.pi / 2.0))
    return out


def _gate_zone(gate: Gate) -> BaseGeometry:
    """The box of the gap of a gate (where its leaf is drawn, with a hair around)."""
    half = gate.width / 2.0 + TOUCH
    if gate.angle == 0.0:
        return box(gate.x - half, gate.y - TOUCH, gate.x + half, gate.y + TOUCH)
    return box(gate.x - TOUCH, gate.y - half, gate.x + TOUCH, gate.y + half)


def _wires(fence: LotFence, footprint: BaseGeometry) -> list[LineString]:
    """The thin wire fences: a single line of ``WIRE_MIN`` or more, straight, that starts at a pillar of the fence and ends at the
    building or at another part of the boundary (or, on a layer the name calls fence, any long line), outside the building."""
    anchors = union([fence.walls.buffer(TOUCH), *[p.buffer(TOUCH) for p in fence.pillars], footprint.buffer(TOUCH)])
    taken = union([fence.walls.buffer(0.01), *[axis.buffer(0.03) for axis in fence.railings], *[_gate_zone(g) for g in fence.gates]])
    out = []
    for layer, g in fence.chains:
        if g.length < WIRE_MIN or g.intersection(footprint).length > 0.2 or g.intersection(taken).length > 0.5 * g.length:
            continue
        ends = [Point(g.coords[0]), Point(g.coords[-1])]
        named = name_says_fence(layer)
        joined = any(p.distance(q) <= TOUCH for p in ends for q in fence.pillars) and all(anchors.contains(p) for p in ends)
        if named or (joined and len(g.coords) == 2):
            out.append(g)
    return out


def finish_lot_fence(fence: LotFence, doc, cfg: Config, unit: str, footprint: BaseGeometry, zones: list, warnings: list[str]) -> LotFence | None:
    """What the fence needs once the building is known: what lies in the building is no fence, the wire fences, the heights and
    the design from the elevations (``zones``: their boxes in drawing units), the height the user gave. None when nothing is left.
    It is a help: whatever goes wrong in it is never the reason a conversion fails."""
    try:
        if not footprint.is_empty:
            inside = footprint.buffer(0.02)
            fence.walls = union([p for p in polygons_of(fence.walls) if p.intersection(inside).area <= INSIDE * p.area])
            fence.pillars = [p for p in fence.pillars if not inside.contains(p.centroid)]
            fence.railings = [r for r in fence.railings if r.intersection(inside).length <= INSIDE * r.length]
        fence.wires = _wires(fence, footprint)
        if fence.empty:
            return None
        read_profile(fence, [read_items(doc, cfg, area=z, ignore_veto=True, keep_other=True, keep_fills=True, unit=unit).items
                             for z in zones])
        if cfg.lot_wall_height is not None:
            prof = fence.profile
            prof.wall, prof.sources["muretto"] = cfg.lot_wall_height, "indicata"
            if prof.sources["pilastri"] == "predefinita":
                prof.pillar = prof.wall + DEFAULT_PILLAR_RISE
    except Exception as exc:
        warnings.append(f"Recinzione del lotto non costruita: {type(exc).__name__}: {exc}. Il resto della conversione e' completo; "
                        "per escluderla usa --no-recinzioni.")
        return None
    warnings.append("Recinzione del lotto riconosciuta dalla forma del disegno (coppie di linee parallele a 8-40 cm con un pilastro "
                    "a un'estremita' o una ringhiera lungo, o su un layer che la nomina; quadrati chiusi come pilastri; due linee "
                    "sottili come ringhiera; una linea sola che unisce un pilastro alla casa come rete): controlla nella pianta "
                    "di controllo e, se non lo e', escludila con --no-recinzioni.")
    return fence


def without_fence_layers(items: list[Item], fence: LotFence | None, cfg: Config) -> tuple[list[Item], list[str]]:
    """(the items without the walls and columns on the layers the names call fence that gave elements of the fence, those layers):
    a layer called Muretto is a wall layer for the names, and the fence would be built twice."""
    if fence is None:
        return items, []
    layers = sorted({it.layer for it in items if it.layer in fence.layers and name_says_fence(it.layer)
                     and it.category in ("wall", "column") and not _user_layer(cfg, it.layer)})
    return [it for it in items if it.layer not in layers], layers


# --- the elevation ---------------------------------------------------------------------------

LEVEL_TOL = 0.001  # m: a line is horizontal (vertical) if its ends differ by less than this
JOIN = 0.05  # m: pieces of a line this close are one line (the blocks of a row of railing have a hair between them)
COVER = 0.7  # a line that covers this share of a run of wall runs along it
WALL_RANGE = (0.3, 2.5)  # m: a wall of the lot is this high
CAP_RANGE = 0.08  # m: a line this close above the top edge of the wall is the top of its cap
RAIL_RANGE = (0.2, 2.0)  # m: a railing is this high over the wall
BAR_SPACING = (0.03, 0.5)  # m: the bars of a railing are this far apart
BAR_WIDTH = (0.004, 0.04)  # m: the two edges of a bar are this far apart
MIN_BARS = 5  # fewer bars than this do not tell the spacing
RAIL_GROUP = 0.03  # m: lines this close above one another are one rail
PILLAR_TOP = 0.15  # m: the cap of a pillar is looked for this far above the shaft
MIN_SHAFT = 0.5  # m: a vertical line this long at the foot of a pillar is its shaft


def _segments(items: list[Item]) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """The horizontal ((y, x0, x1)) and the vertical ((x, y0, y1)) pieces of every line, outline and fill of ``items`` (metres)."""
    horizontal, vertical = [], []
    for it in items:
        for p in it.prims:
            rings = []
            for g in getattr(p.geom, "geoms", [p.geom]):
                if g.geom_type == "Polygon":
                    rings.extend([g.exterior, *g.interiors])
                elif g.geom_type == "LineString":
                    rings.append(g)
            for ring in rings:
                c = list(ring.coords)
                for (ax, ay), (bx, by) in zip(c, c[1:]):
                    if abs(ay - by) <= LEVEL_TOL and abs(bx - ax) >= 0.01:
                        horizontal.append((round((ay + by) / 2.0, 3), min(ax, bx), max(ax, bx)))
                    elif abs(ax - bx) <= LEVEL_TOL and abs(by - ay) >= 0.01:
                        vertical.append((round((ax + bx) / 2.0, 3), min(ay, by), max(ay, by)))
    return horizontal, vertical


def _joined(pieces: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """Pieces (level, from, to) with the ones on one level (within a millimetre) that touch or overlap joined into one."""
    by_level: list[list[tuple[float, float, float]]] = []
    for piece in sorted(pieces):
        if by_level and piece[0] - by_level[-1][-1][0] <= LEVEL_TOL:
            by_level[-1].append(piece)
        else:
            by_level.append([piece])
    out: list[tuple[float, float, float]] = []
    for group in by_level:
        start = len(out)
        for _, a, b in sorted(group, key=lambda p: p[1]):
            if len(out) > start and a <= out[-1][2] + JOIN:
                out[-1] = (group[0][0], out[-1][1], max(out[-1][2], b))
            else:
                out.append((group[0][0], a, b))
    return out


def _grouped(levels: list[float], tol: float) -> list[tuple[float, float]]:
    """(lowest, highest) of every set of values that follow each other within ``tol``."""
    out: list[list[float]] = []
    for v in sorted(levels):
        if out and v - out[-1][1] <= tol:
            out[-1][1] = v
        else:
            out.append([v, v])
    return [(a, b) for a, b in out]


def read_profile(fence: LotFence, elevations: list[list[Item]]) -> None:
    """Fill ``fence.profile`` with what an elevation drawn in line with the plan says: the ground line, the top of the wall and
    its cap, the top of the pillars and their caps, the rails and the bars of the railing. ``elevations``: the items (hatches
    included) of each elevation, in metres. What it does not say stays as it was (the defaults)."""
    runs = [p.bounds for p in polygons_of(fence.walls) if p.bounds[2] - p.bounds[0] >= p.bounds[3] - p.bounds[1]]  # along x: seen in a front view
    pillars = [p.bounds for p in fence.pillars]
    if not runs and not pillars:
        return
    x_lo, x_hi = min(b[0] for b in runs + pillars), max(b[2] for b in runs + pillars)
    for items in elevations:
        horizontal, vertical = _segments(items)
        lines = _joined([h for h in horizontal if h[2] >= x_lo - 0.1 and h[1] <= x_hi + 0.1])
        ground = min((y for y, a, b in lines if b - a >= 0.5 * (x_hi - x_lo)), default=None)  # the ground line spans the lot
        if ground is not None and _read_heights(fence.profile, lines, vertical, ground, runs, pillars, bool(fence.railings)):
            return


def _read_heights(prof: Profile, lines: list, vertical: list, ground: float, runs: list, pillars: list, railing: bool) -> bool:
    """The part of ``read_profile`` that works on the pieces of one elevation: True when it gave the wall or the pillars."""
    tops, caps = [], []
    for x0, _, x1, _ in pillars:
        shafts = [y1 for x, y0, y1 in vertical if x0 - TOUCH <= x <= x1 + TOUCH and y0 <= ground + 0.1 and y1 - y0 >= MIN_SHAFT]
        if not shafts:
            continue
        shaft = max(shafts)
        above = [y for y, a, b in lines if shaft < y <= shaft + PILLAR_TOP and a >= x0 - 0.1 and b <= x1 + 0.1 and b - a >= 0.5 * (x1 - x0)]
        tops.append(max(above, default=shaft) - ground)
        caps.append(max(above, default=shaft) - shaft)
    prof_top = float(np.median(tops)) if tops else None
    wall_tops = []
    for x0, _, x1, _ in runs:
        covering = sorted(y for y, a, b in lines if WALL_RANGE[0] <= y - ground <= WALL_RANGE[1] and a <= x0 + 0.1 and b >= x0 + COVER * (x1 - x0))
        if covering:
            capped = next((y for y in covering if covering[0] < y <= covering[0] + CAP_RANGE), None)
            wall_tops.append((capped or covering[0], capped - covering[0] if capped else 0.0))
    wall_top = max(wall_tops, default=None)  # the highest run: a run whose cap the drawing leaves out is not lower for it
    if wall_top is not None:
        prof.wall, prof.sources["muretto"] = wall_top[0] - ground, "prospetto"
        if wall_top[1]:
            prof.cap = wall_top[1]
    if prof_top is not None:
        prof.pillar, prof.sources["pilastri"] = prof_top, "prospetto"
        if float(np.median(caps)) >= 0.01:
            prof.pillar_cap = float(np.median(caps))
    elif wall_top is not None:
        prof.pillar = prof.wall + DEFAULT_PILLAR_RISE  # the pillars stand over the wall that was found, not over the default one
    if wall_top is not None and railing:
        _read_railing(prof, lines, vertical, wall_top[0], runs)
    return prof_top is not None or wall_top is not None


def _read_railing(prof: Profile, lines: list, vertical: list, top: float, runs: list) -> None:
    """The rails and the bars of the railing over the wall whose top is at ``top`` (drawing y), in the span of the runs."""
    x0, x1 = min(r[0] for r in runs), max(r[2] for r in runs)
    shortest = min(r[2] - r[0] for r in runs)
    band = [y for y, a, b in lines if top + 0.02 < y <= top + RAIL_RANGE[1] and b - a >= COVER * shortest]
    rails = [Rail(a - top, b - top) for a, b in _grouped(band, RAIL_GROUP)]
    if not rails or not RAIL_RANGE[0] <= rails[-1].z1 <= RAIL_RANGE[1]:
        return
    prof.rails, prof.rail, prof.sources["ringhiera"] = rails[:4], rails[-1].z1, "prospetto"
    edges = [x for x, y0, y1 in vertical if x0 <= x <= x1 and y0 >= top - 0.01 and y1 <= top + prof.rail + 0.01 and y1 - y0 >= 0.05]
    bars = _grouped(edges, BAR_WIDTH[1])  # the two edges of a bar are one bar
    centres = [(a + b) / 2.0 for a, b in bars]
    gaps = [d for d in np.diff(centres) if BAR_SPACING[0] <= d <= BAR_SPACING[1]]
    if len(centres) >= MIN_BARS and gaps:
        prof.pitch = float(np.median(gaps))
        widths = [b - a for a, b in bars if BAR_WIDTH[0] <= b - a <= BAR_WIDTH[1]]
        if widths:
            prof.bar = float(np.median(widths))


# --- building --------------------------------------------------------------------------------

def _lift(ground, x: float, y: float) -> float:
    """How much higher the ground is at (x, y) than the flat model has it (0 on flat ground)."""
    return 0.0 if ground is None else float(ground.lift(x, y))


def _bars(axis: LineString, pitch: float) -> list[tuple[float, float]]:
    """The places of the bars along a railing: evenly spaced, half a step from each end, at ``pitch`` or a little less."""
    n = max(int(math.ceil(axis.length / pitch - 1e-9)), 1)
    return [(p.x, p.y) for p in (axis.interpolate((i + 0.5) * axis.length / n) for i in range(n))]


def _pieces(axis: LineString, step: float) -> list[LineString]:
    """The axis cut into pieces no longer than ``step`` (the rails of a railing on sloping ground step from piece to piece)."""
    n = max(int(math.ceil(axis.length / step - 1e-9)), 1)
    return [substring(axis, i * axis.length / n, (i + 1) * axis.length / n) for i in range(n)]


def _laid(mesh: Mesh, ground, group: str, geom: BaseGeometry, z0: float, z1: float, base: float) -> None:
    """``geom`` as a closed solid from ``z0`` to ``z1`` above the ground: from ``base`` (below the ground slabs) when it starts at
    the ground. On a terrain it is laid on it (a slab that starts above the ground keeps its thickness over the slopes)."""
    from .terrainmesh import add_draped

    if geom.is_empty:
        return
    if ground is None:
        mesh.add_extrusion(group, [Slab(base if z0 <= 0 else z0, z1, geom)], bottom=True)
    elif z0 <= 0:
        add_draped(mesh, ground, group, geom, z1)
    else:
        add_draped(mesh, ground, group, geom, z1, embed=-(z0 - ground.level))


def add_lot_fence(mesh: Mesh, fence: LotFence, cfg: Config, ground=None) -> None:
    """The wall, its cap, the pillars, the railing and the wire fences as groups of their own. They stand on the ground of the
    flat model (zero) from the depth of the ground slabs up to the heights of ``fence.profile``; on a terrain (``ground``) the
    wall and the pillars are laid on it and the railing follows it, stepping from piece to piece."""
    prof = fence.profile
    top = prof.wall
    cap = min(prof.cap, top / 2.0)
    base = -cfg.garden_thickness
    _laid(mesh, ground, "Muretto", fence.walls, 0.0, top - cap, base)
    _laid(mesh, ground, "Copertina", fence.walls.buffer(CAP_OVERHANG, join_style="mitre").difference(union(fence.pillars)),
          top - cap, top, base)
    if fence.pillars:
        ptop = max(prof.pillar, top + 0.05)
        pcap = min(prof.pillar_cap, (ptop - top) / 2.0)
        _laid(mesh, ground, "Pilastri_lotto", union(fence.pillars), 0.0, ptop - pcap, base)
        _laid(mesh, ground, "Copertina", union(p.buffer(PILLAR_CAP_OVERHANG, join_style="mitre") for p in fence.pillars),
              ptop - pcap, ptop, base)

    rails = prof.rail_bars()
    pitch = prof.pitch
    while sum(len(_bars(a, pitch)) for a in fence.railings) > MAX_BARS:
        pitch *= 2.0
    top_rail = max(rails, key=lambda r: r.z0)
    bar_top = (top_rail.z0 + top_rail.z1) / 2.0  # the bars end inside the top rail
    half = prof.bar / 2.0
    for axis in fence.railings:
        for piece in (_pieces(axis, STEP) if ground is not None else [axis]):
            z = top + max(_lift(ground, p.x, p.y) for p in (piece.interpolate(d) for d in (0.0, piece.length / 2.0, piece.length)))
            for r in rails:  # level on the highest ground under the piece
                mesh.add_extrusion("Ringhiera", [Slab(z + r.z0, z + r.z1, piece.buffer(RAIL_SECTION / 2.0, cap_style="flat", join_style="mitre"))])
            for x, y in _bars(piece, pitch):
                mesh.add_extrusion("Ringhiera", [Slab(top + _lift(ground, x, y), z + bar_top, box(x - half, y - half, x + half, y + half))])

    for axis in fence.wires:
        n = max(int(math.ceil(axis.length / WIRE_SPAN - 1e-9)), 1)
        for i in range(n + 1):
            p = axis.interpolate(i * axis.length / n)
            g, h = _lift(ground, p.x, p.y), WIRE_POST / 2.0
            mesh.add_extrusion("Recinzione_pali", [Slab(base + g, prof.wire + g, box(p.x - h, p.y - h, p.x + h, p.y + h))])
        g = _lift(ground, *axis.coords[0])
        mesh.add_extrusion("Recinzione_rete", [Slab(WIRE_FOOT + g, prof.wire + g, axis.buffer(WIRE_MESH / 2.0, cap_style="flat", join_style="mitre"))])


def summary(fence: LotFence) -> str:
    """The line of the report: what was found, and where the heights come from."""
    wall, rail, wire = fence.lengths()
    prof = fence.profile
    parts = []
    if wall >= 0.1:
        parts.append(f"{wall:.1f} m di muretto alto {prof.wall:.2f} m ({prof.sources['muretto']})")
    if fence.pillars:
        parts.append(f"{len(fence.pillars)} pilastri alti {prof.pillar:.2f} m ({prof.sources['pilastri']})")
    if rail >= 0.1:
        parts.append(f"{rail:.1f} m di ringhiera alta {prof.rail:.2f} m sul muretto, un montante ogni {prof.pitch * 100:.0f} cm "
                     f"({prof.sources['ringhiera']})")
    if wire >= 0.1:
        parts.append(f"{wire:.1f} m di rete alta {prof.wire:.2f} m ({prof.sources['rete']})")
    if fence.gates:
        one = len(fence.gates) == 1
        parts.append(f"{len(fence.gates)} cancell{'o' if one else 'i'} ({', '.join(f'{g.width:.2f} m' for g in fence.gates)}) "
                     f"lasciat{'o' if one else 'i'} apert{'o' if one else 'i'}")
    return ", ".join(parts)
