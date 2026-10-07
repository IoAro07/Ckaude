"""Floors per room, skirting boards and the thin interior partitions as an object of their own.

Floors: the closed shapes of the floor layer (one object each, named after the room they cover)
or, without such a layer, the closed spaces of the walls (named after the name written in
them). Skirting: the lines of the skirting layer as a thin strip on the room side. Partitions:
what the "fondelli/tramezzi" layers add to the walls, kept apart so it can have its own material.
"""

from __future__ import annotations

import math
import random
import re

from shapely.geometry import MultiPoint, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union, voronoi_diagram
from shapely.strtree import STRtree

from .config import Config
from .geom import fix, polygons_of, union
from .labels import Room
from .openings import Opening
from .reader import Item

MIN_FLOOR_AREA = 0.5  # m2
OVERLAP_SAME = 0.5  # a floor covers a room when it holds at least this share of the room
SKIRTING_WALL_NEAR = 0.02  # a skirting line this close to a wall is on its face: the strip goes room-side
GUARD = 0.005
OUTER_TOLERANCE = 0.05  # m: the band of perimeter wall is this much thicker than the walls found


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") or "Locale"


def layer_floors(items: list[Item]) -> list[Polygon]:
    """Floor shapes of the floor layer: closed outlines, fills, and lines that close up into outlines.
    A shape inside a bigger one is subtracted from it (another finish), so none overlap."""
    shapes: list[Polygon] = []
    lines = []
    for it in items:
        if it.category != "floor":
            continue
        for p in it.prims:
            if p.kind in ("ring", "fill"):
                shapes.extend(polygons_of(fix(p.geom)))
            elif p.kind == "line":
                lines.append(p.geom)
    if lines:
        shapes.extend(polygonize(unary_union(lines)))
    shapes = [s for s in shapes if s.area >= MIN_FLOOR_AREA]
    shapes.sort(key=lambda s: -s.area)
    out: list[Polygon] = []
    for i, big in enumerate(shapes):
        inner = [s for s in shapes[i + 1:] if big.intersection(s).area >= 0.5 * s.area]
        shape = big.difference(union(inner)) if inner else big
        out.extend(p for p in polygons_of(shape) if p.area >= MIN_FLOOR_AREA)
    return out


def name_floors(shapes: list[BaseGeometry], rooms: list[Room]) -> list[tuple[str, BaseGeometry]]:
    """Name each floor shape after the named rooms whose name is written on it (or, failing that, that it
    mostly covers and no other shape claimed). Repeated names get _2, _3."""
    claimed: list[list[Room]] = []
    taken: set[int] = set()
    for shape in shapes:
        mine = [r for r in rooms if r.named and any(shape.contains(Point(w.x, w.y)) for w in r.words)]
        taken.update(id(r) for r in mine)
        claimed.append(mine)
    named: list[tuple[str, BaseGeometry]] = []
    count: dict[str, int] = {}
    for k, (shape, mine) in enumerate(zip(shapes, claimed), 1):
        if not mine:
            mine = [r for r in rooms if r.named and id(r) not in taken
                    and shape.intersection(r.polygon).area >= OVERLAP_SAME * r.polygon.area]
        label = "_".join(dict.fromkeys(_slug(r.name) for r in mine)) or f"Locale_{k:02d}"
        count[label] = count.get(label, 0) + 1
        named.append((label if count[label] == 1 else f"{label}_{count[label]}", shape))
    return named


def room_floors(rooms: list[Room], openings: list[Opening]) -> list[tuple[str, BaseGeometry]]:
    """One floor per closed room, extended under the doors that open onto it (no hole at the threshold)."""
    thresholds = [o.cut for o in openings if o.keep and o.kind != "window" and o.z0 <= 1e-9]
    shapes: list[BaseGeometry] = []
    for room in rooms:
        shape: BaseGeometry = room.polygon
        mine = [t for t in thresholds if t.buffer(GUARD).intersects(room.polygon)]
        if mine:
            shape = union([shape] + mine)
        shapes.append(shape)
    # a threshold shared by two rooms was added to both: give it to the first only
    seen: BaseGeometry = Polygon()
    for i, shape in enumerate(shapes):
        shapes[i] = shape.difference(seen) if not seen.is_empty else shape
        seen = seen.union(shapes[i])
    return name_floors(shapes, rooms)


SAMPLE = 0.1  # m: spacing of the points that stand for a floor's outline when sharing the wall band


def _share_band(shapes: list[BaseGeometry], band: BaseGeometry, reach: float) -> list[BaseGeometry]:
    """Split ``band`` between the floors by nearest floor (a Voronoi diagram of their outlines' vertices and
    points every ``SAMPLE``). Where a partition meets the perimeter wall the line falls on the partition's
    middle line, straight, because it is the bisector of the two floors' corners."""
    pts: list[tuple[float, float]] = []
    owner: list[int] = []
    zone = band.buffer(reach * 1.6)
    for i, shape in enumerate(shapes):
        for poly in polygons_of(shape):
            for ring in (poly.exterior, *poly.interiors):
                c = list(ring.coords)
                for (ax, ay), (bx, by) in zip(c, c[1:]):
                    n = max(int(math.hypot(bx - ax, by - ay) / SAMPLE), 1)
                    for k in range(n):
                        x, y = ax + (bx - ax) * k / n, ay + (by - ay) * k / n
                        if zone.covers(Point(x, y)):
                            pts.append((round(x, 6), round(y, 6)))
                            owner.append(i)
    if not pts:
        return [Polygon() for _ in shapes]
    seen: dict[tuple[float, float], int] = {}
    for pt, o in zip(pts, owner):
        seen.setdefault(pt, o)
    # Perfectly regular points (a straight wall sampled every 10 cm) make a degenerate diagram in which GEOS
    # can lose a cell: a 0.2 mm jitter, always the same, removes the symmetry and moves no line visibly.
    rng = random.Random(7)
    points = [Point(x + rng.uniform(-2e-4, 2e-4), y + rng.uniform(-2e-4, 2e-4)) for x, y in seen]
    owners = list(seen.values())
    diagram = voronoi_diagram(MultiPoint(points), envelope=band.envelope.buffer(2.0 * reach + 1.0))
    tree = STRtree(points)
    cells: list[list[BaseGeometry]] = [[] for _ in shapes]
    for cell in diagram.geoms:
        hit = tree.query(cell, predicate="contains")
        if len(hit):
            cells[owners[int(hit[0])]].append(cell)
    shares = [union(c).intersection(band).intersection(shapes[i].buffer(reach * 1.6)) if c else Polygon()
              for i, c in enumerate(cells)]
    # Safety net: whatever a cell lost (a degenerate diagram) goes to the nearest floor within reach.
    for piece in polygons_of(band.difference(union(shares))):
        if piece.area < 1e-6:
            continue
        near = min(range(len(shapes)), key=lambda i: shapes[i].distance(piece))
        if shapes[near].distance(piece) <= reach * 1.6:
            shares[near] = union([shares[near], piece])
    return shares


def extend_to_outer_faces(floors: list[tuple[str, BaseGeometry]], solid: BaseGeometry, max_thickness: float,
                          exclude: BaseGeometry | None = None) -> list[tuple[str, BaseGeometry]]:
    """Let every floor reach the outer face of the perimeter walls (they stop at the inner face).

    The strip of wall that lies within a wall's thickness of the outside is shared between the floors by
    nearest floor: where a partition ends on the perimeter wall the two floors meet on the partition's middle
    line and never overlap. ``exclude``: material that stays free (the partitions)."""
    if not floors:
        return floors
    footprint = union(Polygon(p.exterior) for p in polygons_of(solid))
    if footprint.is_empty:
        return floors
    gaps = [shape.distance(footprint.boundary) for _, shape in floors]
    gaps = [g for g in gaps if 1e-6 < g <= max_thickness]
    if not gaps:
        return floors  # nothing between the floors and the outside, or no perimeter wall to speak of
    reach = max(gaps) + OUTER_TOLERANCE
    inner = footprint.buffer(-reach, join_style="mitre")
    band = (footprint.difference(inner) if not inner.is_empty else footprint).intersection(solid)
    if exclude is not None and not exclude.is_empty:
        band = band.difference(exclude)
    shapes = [shape for _, shape in floors]
    band = band.difference(union(shapes))
    if band.is_empty:
        return floors
    out = []
    for (name, shape), extra in zip(floors, _share_band(shapes, band, reach)):
        if not extra.is_empty:
            merged = unary_union([shape, extra]).buffer(0.0005, join_style="mitre").buffer(-0.0005, join_style="mitre")
            shape = merged.simplify(0.001, preserve_topology=True)
        out.append((name, shape))
    return out


def skirting_strips(items: list[Item], walls: BaseGeometry, openings: list[Opening], cfg: Config) -> BaseGeometry:
    """Thin strips along the skirting lines, on the room side when the line runs on a wall face."""
    t = cfg.skirting_thickness
    gaps = union([o.cut.buffer(0.01) for o in openings if o.keep and o.kind != "window" and o.z0 <= 1e-9])
    strips: list[BaseGeometry] = []
    for it in items:
        if it.category != "skirting":
            continue
        for p in it.prims:
            geom = p.geom.boundary if p.kind in ("ring", "fill") else p.geom
            for line in getattr(geom, "geoms", [geom]):
                if line.geom_type != "LineString" or line.length < 0.05:
                    continue
                if line.distance(walls) <= SKIRTING_WALL_NEAR:
                    left, right = line.buffer(t, single_sided=True), line.buffer(-t, single_sided=True)
                    strip = left if left.intersection(walls).area <= right.intersection(walls).area else right
                else:
                    strip = line.buffer(t / 2.0, cap_style="flat", join_style="mitre")
                strips.append(strip)
    result = union(strips)
    if not gaps.is_empty and not result.is_empty:
        result = result.difference(gaps)
    return union(p for p in polygons_of(result) if p.area > 1e-5)


def split_partitions(solid: BaseGeometry, main: BaseGeometry, part: BaseGeometry,
                     openings: list[Opening]) -> BaseGeometry:
    """The part of ``solid`` that belongs to the partition layers: their own shapes, plus the gap
    fillers of the doors that sit in a partition. Whatever touches a main wall stays with the walls."""
    if part.is_empty:
        return Polygon()
    fills = [o.fill for o in openings
             if o.fill.buffer(0.02).intersects(part) and not (not main.is_empty and o.fill.buffer(0.02).intersects(main))]
    owned = union([part.buffer(GUARD)] + fills)
    result = solid.intersection(owned)
    if not main.is_empty:
        result = result.difference(main.buffer(GUARD))
    return union(p for p in polygons_of(result) if p.area > 1e-4)


def skirting_items(items: list[Item]) -> bool:
    return any(it.category == "skirting" for it in items)
