"""Doors and windows: where they are, how wide, and how thick the wall is there.

The wall is rebuilt across the opening and then cut only between the opening's
bottom and top. This handles both ways of drawing a plan:
  * the wall lines run through the symbol -> the wall is cut;
  * the wall lines stop at the symbol (a gap) -> the gap is filled first, so the
    lintel above a door and the parapet/lintel around a window still exist.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from .config import Config
from .geom import oriented_rect, polygons_of, union
from .reader import Item

CLUSTER_TOL = 0.03  # loose lines closer than this belong to the same symbol
REGION = 0.15  # a symbol must lie within this distance of a wall
ON_WALL = 0.02  # a sample point this close to a wall counts as "on" it
MIN_OPENING_WIDTH = 0.2
OVERLAP = 0.005  # a gap filler reaches this far into the wall on each side, so the pieces merge
STATION_OFFSETS = (0.03, 0.10, 0.25)  # where to look for the wall just outside the symbol


@dataclass
class Opening:
    kind: str  # "door" | "window"
    cut: Polygon  # footprint removed from the walls between z0 and z1
    fill: Polygon  # same, slightly longer: used to close a gap drawn in the wall lines
    z0: float
    z1: float
    glass: Polygon | None  # thin pane for windows


def _symbols(items: list[Item], kind: str) -> list[BaseGeometry]:
    """One geometry per symbol: a block reference is one symbol; loose
    entities are grouped by proximity."""
    symbols: list[BaseGeometry] = []
    loose: list[BaseGeometry] = []
    for it in items:
        if it.category != kind:
            continue
        geoms = [p.geom for p in it.prims]
        if it.block:
            symbols.append(union(geoms))
        else:
            loose.extend(geoms)
    if loose:
        grown = union(g.buffer(CLUSTER_TOL) for g in loose)
        for cl in getattr(grown, "geoms", [grown]):
            symbols.append(union([g for g in loose if cl.intersects(g)]))
    return symbols


class _WallEdges:
    """Wall boundary segments, to find the direction a wall runs near a point."""

    def __init__(self, walls: BaseGeometry):
        segs, self.lengths, self.angles = [], [], []
        for poly in polygons_of(walls):
            for ring in (poly.exterior, *poly.interiors):
                c = np.asarray(ring.coords)
                for a, b in zip(c[:-1], c[1:]):
                    d = b - a
                    n = math.hypot(*d)
                    if n < 1e-9:
                        continue
                    segs.append(LineString([a, b]))
                    self.lengths.append(n)
                    self.angles.append(math.atan2(d[1], d[0]) % math.pi)
        self.lengths = np.asarray(self.lengths)
        self.angles = np.asarray(self.angles)
        self.tree = STRtree(segs) if segs else None

    def direction_near(self, region: BaseGeometry) -> tuple[float, float] | None:
        """Unit vector of the dominant wall direction among edges touching ``region``,
        weighting each edge by its full length (long faces beat short jamb ends)."""
        if self.tree is None:
            return None
        idx = self.tree.query(region, predicate="intersects")
        if len(idx) == 0:
            return None
        ang, w = self.angles[idx], self.lengths[idx]
        bins = np.bincount((ang / math.pi * 90).astype(int) % 90, weights=w, minlength=90)
        best = (int(np.argmax(bins)) + 0.5) * math.pi / 90
        diff = np.abs((ang - best + math.pi / 2) % math.pi - math.pi / 2)
        near = diff < math.radians(3)
        theta = float(np.average(ang[near], weights=w[near])) if near.any() else best
        return math.cos(theta), math.sin(theta)


def _cross_section(walls: BaseGeometry, p: Point, vx: float, vy: float,
                   reach: float) -> tuple[float, float] | None:
    """Wall extent along v through ``p`` (absolute v coordinates), nearest piece."""
    line = LineString([(p.x - vx * reach, p.y - vy * reach), (p.x + vx * reach, p.y + vy * reach)])
    hit = walls.intersection(line)
    pieces = [g for g in getattr(hit, "geoms", [hit]) if isinstance(g, LineString) and g.length > 0]
    if not pieces:
        return None
    piece = min(pieces, key=lambda g: g.distance(p))
    vs = [x * vx + y * vy for x, y in piece.coords]
    return min(vs), max(vs)


def _locate_opening(sym: BaseGeometry, walls: BaseGeometry, edges: _WallEdges,
                    max_t: float) -> tuple[float, float, float, float, float, float] | None:
    """(ux, uy, a0, a1, s0, s1): wall direction u, the opening's extent [a0, a1] along u
    and the wall's extent [s0, s1] across it (absolute coordinates), or None."""
    hull = sym.convex_hull
    if not isinstance(hull, Polygon):
        hull = hull.buffer(0.005)
    region = hull.buffer(REGION)
    if not region.intersects(walls):
        return None
    direction = edges.direction_near(region)
    if direction is None:
        return None
    ux, uy = direction
    vx, vy = -uy, ux

    pts = np.asarray(hull.exterior.coords)
    us, vs = pts @ [ux, uy], pts @ [vx, vy]
    a0, a1 = float(us.min()), float(us.max())
    if a1 - a0 < MIN_OPENING_WIDTH:
        return None
    mid = (a0 + a1) / 2.0
    v_candidates = [(vs.min() + vs.max()) / 2.0, float(vs.min()), float(vs.max())]
    stations = [mid, a0 + 0.1 * (a1 - a0), a1 - 0.1 * (a1 - a0)]
    for off in STATION_OFFSETS:
        stations += [a0 - off, a1 + off]

    for u in stations:
        best = None
        for v in v_candidates:
            p = Point(u * ux + v * vx, u * uy + v * vy)
            d = walls.distance(p)
            if d <= ON_WALL and (best is None or d < best[0]):
                best = (d, p)
        if best:
            section = _cross_section(walls, best[1], vx, vy, max_t)
            if section:
                s0, s1 = section
                return ux, uy, a0, a1, s0, s1
    return None


def build_openings(items: list[Item], walls: BaseGeometry, cfg: Config,
                   warnings: list[str]) -> list[Opening]:
    openings: list[Opening] = []
    if walls.is_empty:
        return openings
    edges = _WallEdges(walls)
    skipped = 0
    for kind in ("door", "window"):
        z0 = 0.0 if kind == "door" else cfg.window_sill
        z1 = min(cfg.door_height if kind == "door" else cfg.window_sill + cfg.window_height,
                 cfg.wall_height)
        if z1 <= z0:
            continue
        for sym in _symbols(items, kind):
            found = _locate_opening(sym, walls, edges, cfg.max_wall_thickness)
            if found is None:
                skipped += 1
                continue
            ux, uy, a0, a1, s0, s1 = found
            # centre c has u = (a0+a1)/2 and v = 0, so v offsets are absolute
            mid = (a0 + a1) / 2.0
            cx, cy = mid * ux, mid * uy
            margin = 0.002
            cut = oriented_rect(cx, cy, ux, uy, a1 - a0, s0 - margin, s1 + margin)
            fill = oriented_rect(cx, cy, ux, uy, a1 - a0 + 2 * OVERLAP, s0, s1)
            glass = None
            if kind == "window" and cfg.glass:
                m, h = (s0 + s1) / 2.0, cfg.glass_thickness / 2.0
                glass = oriented_rect(cx, cy, ux, uy, a1 - a0, m - h, m + h)
            openings.append(Opening(kind, cut, fill, z0, z1, glass))
    if skipped:
        warnings.append(f"{skipped} porte/finestre non toccano nessun muro e sono state ignorate.")
    return openings
