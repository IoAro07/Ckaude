"""Small 2D geometry helpers built on shapely."""

from __future__ import annotations

import math
from typing import Iterable

import shapely
from shapely.geometry import MultiPoint, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.strtree import STRtree

MIN_AREA = 1e-6  # m^2


def polygons_of(geom: BaseGeometry | None) -> list[Polygon]:
    """All non-degenerate polygons contained in ``geom`` (any geometry type)."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom] if geom.area > MIN_AREA else []
    if hasattr(geom, "geoms"):
        out: list[Polygon] = []
        for g in geom.geoms:
            out.extend(polygons_of(g))
        return out
    return []


def fix(geom: BaseGeometry) -> BaseGeometry:
    return geom if geom.is_valid else shapely.make_valid(geom)


def union(geoms: Iterable[BaseGeometry]) -> BaseGeometry:
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    return unary_union(geoms) if geoms else Polygon()


def grown_by_square(shape: BaseGeometry, half: float) -> BaseGeometry:
    """``shape`` grown by the square of half-side ``half`` (the Minkowski sum): itself plus every edge swept by the
    square. A mitred buffer does the same on square-cornered plans but is not monotone on others: one more thin wall
    can make it drop a whole room from a closed outline. This one never takes anything away when a wall is added."""
    corners = ((-half, -half), (-half, half), (half, -half), (half, half))
    parts = [shape]
    for poly in polygons_of(shape):
        for ring in (poly.exterior, *poly.interiors):
            pts = ring.coords
            for (ax, ay), (bx, by) in zip(pts[:-1], pts[1:]):
                parts.append(MultiPoint([(x + dx, y + dy) for x, y in ((ax, ay), (bx, by)) for dx, dy in corners]).convex_hull)
    return unary_union(parts)


def closed_by_square(shape: BaseGeometry, half: float) -> BaseGeometry:
    """``shape`` with the gaps up to ``2 * half`` filled: grown by the square, then shrunk by it (the free space around
    it grown, and taken from a frame that holds it)."""
    grown = grown_by_square(shape, half)
    frame = box(*grown.bounds).buffer(half + 1.0)
    return frame.difference(grown_by_square(frame.difference(grown), half))


INSIDE = 0.75  # share of a shape's area that must lie in another one to count as nested in it


def nest_polygons(polys: Iterable[Polygon]) -> BaseGeometry:
    """Combine closed outlines drawn as separate shapes.

    A shape lying completely inside another one is a hole (even depth = solid,
    odd depth = hole), so a building drawn as an outer and an inner closed
    polyline becomes a ring. Shapes that merely overlap are united. Duplicates
    are ignored.
    """
    cleaned: list[Polygon] = []
    for p in polys:
        cleaned.extend(polygons_of(fix(p)))
    if not cleaned:
        return Polygon()

    seen: set[bytes] = set()
    unique: list[Polygon] = []
    for p in cleaned:
        key = shapely.to_wkb(shapely.normalize(shapely.set_precision(p, 1e-6)))
        if key not in seen:
            seen.add(key)
            unique.append(p)
    if len(unique) == 1:
        return unique[0]

    tree = STRtree(unique)
    levels: dict[int, list[Polygon]] = {}
    for i, p in enumerate(unique):
        # A shape is inside another when (almost) all of it lies there: a partition drawn a little
        # into the wall it meets is still an island of the room, not a hole in the wall.
        candidates = tree.query(p, predicate="intersects")
        depth = sum(1 for j in candidates if j != i and unique[j].area > p.area
                    and p.intersection(unique[j]).area >= INSIDE * p.area)
        levels.setdefault(depth, []).append(p)
    # Apply the levels outermost first: solid (even depth) is added, hole (odd depth) is cut
    # out, an island inside a hole is added back, and so on.
    result: BaseGeometry = Polygon()
    for depth in sorted(levels):
        shape = union(levels[depth])
        result = result.union(shape) if depth % 2 == 0 else result.difference(shape)
    return result


def oriented_rect(cx: float, cy: float, ux: float, uy: float, length: float,
                  v0: float, v1: float) -> Polygon:
    """Rectangle with the long axis along unit vector (ux, uy), centred on ``c``
    along u (extent ``length``), and spanning [v0, v1] along the perpendicular
    (-uy, ux), measured from ``c``."""
    vx, vy = -uy, ux
    h = length / 2.0
    pts = [
        (cx - ux * h + vx * v0, cy - uy * h + vy * v0),
        (cx + ux * h + vx * v0, cy + uy * h + vy * v0),
        (cx + ux * h + vx * v1, cy + uy * h + vy * v1),
        (cx - ux * h + vx * v1, cy - uy * h + vy * v1),
    ]
    return Polygon(pts)


def long_axis(poly: BaseGeometry) -> tuple[float, float, float, float, float, float] | None:
    """Oriented bounding box of ``poly``: (cx, cy, ux, uy, length, width).

    (ux, uy) is the unit vector of the longer side.
    """
    rect = shapely.minimum_rotated_rectangle(poly)
    if not isinstance(rect, Polygon) or rect.area < MIN_AREA:
        return None
    c = list(rect.exterior.coords)[:4]
    e0 = (c[1][0] - c[0][0], c[1][1] - c[0][1])
    e1 = (c[2][0] - c[1][0], c[2][1] - c[1][1])
    l0, l1 = math.hypot(*e0), math.hypot(*e1)
    long_e, long_l, short_l = (e0, l0, l1) if l0 >= l1 else (e1, l1, l0)
    ux, uy = long_e[0] / long_l, long_e[1] / long_l
    ctr = rect.centroid
    return ctr.x, ctr.y, ux, uy, long_l, short_l
