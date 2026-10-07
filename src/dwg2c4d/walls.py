"""Turn wall-layer primitives into a 2D wall footprint (metres)."""

from __future__ import annotations

import math
from collections import defaultdict

import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, polygonize, unary_union
from shapely.strtree import STRtree

from .config import Config
from .geom import fix, nest_polygons, polygons_of, union
from .reader import Item, Prim

KEY_PRECISION = 1e6
GRID = 1e-6
MIN_JAMB = 0.02  # closest allowed distance between two endpoints that get joined
SNAP = 0.02  # an endpoint closer than this to another line is snapped onto it


def thinness(poly: Polygon) -> float:
    """Average thickness of a polygon: 2*area/perimeter (= t for a long strip of width t)."""
    return 2.0 * poly.area / poly.length if poly.length else 0.0


def _boundaries(poly_geom: BaseGeometry) -> list[LineString]:
    out = []
    for p in polygons_of(poly_geom):
        out.append(LineString(p.exterior.coords))
        out.extend(LineString(r.coords) for r in p.interiors)
    return out


def _key(pt) -> tuple[int, int]:
    return round(pt[0] * KEY_PRECISION), round(pt[1] * KEY_PRECISION)


def _unit(dx: float, dy: float) -> tuple[float, float] | None:
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-12 else None


def closure_segments(segs: list[LineString], max_t: float) -> list[LineString]:
    """Extra lines that close double-line walls drawn with open ends.

    1. A free endpoint that almost touches another line is snapped onto it.
    2. Two free endpoints of parallel lines, facing the same way and no further
       apart than ``max_t``, are joined (the 'jamb' of a door opening).
    3. A line that stops short of the end of a perpendicular line (no further than
       ``max_t``) is prolonged to it (a wall ending against another at an opening).
    """
    ends: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)  # key -> [(seg, which)]
    for i, s in enumerate(segs):
        c = s.coords
        ends[_key(c[0])].append((i, 0))
        ends[_key(c[-1])].append((i, 1))
    dangling = []  # (point, outward unit dir, seg index)
    for refs in ends.values():
        if len(refs) != 1:
            continue
        i, which = refs[0]
        c = list(segs[i].coords)
        p, q = (c[0], c[1]) if which == 0 else (c[-1], c[-2])
        d = _unit(p[0] - q[0], p[1] - q[1])
        if d:
            dangling.append((p, d, i))

    closers: list[LineString] = []
    tree = STRtree(segs)
    remaining = []
    for p, d, i in dangling:
        pt = Point(p)
        snapped = False
        for j in tree.query(pt.buffer(SNAP)):
            if j == i:
                continue
            # Free ends exist because the line network is not connected there, so any
            # other line within reach (even at 1e-12) needs an explicit connection.
            if segs[j].distance(pt) <= SNAP:
                q, _ = nearest_points(segs[j], pt)
                closers.append(LineString([p, (q.x, q.y)]))
                snapped = True
                break
        if not snapped:
            remaining.append((p, d, i))

    pairs = []
    for a in range(len(remaining)):
        pa, da, ia = remaining[a]
        for b in range(a + 1, len(remaining)):
            pb, db, ib = remaining[b]
            if ia == ib:
                continue
            vx, vy = pb[0] - pa[0], pb[1] - pa[1]
            dist = math.hypot(vx, vy)
            if not (MIN_JAMB <= dist <= max_t):
                continue
            ux, uy = vx / dist, vy / dist
            dot_a, dot_b = ux * da[0] + uy * da[1], ux * db[0] + uy * db[1]
            # 1. jamb: parallel lines ending at the same station, joined across the wall
            jamb = da[0] * db[0] + da[1] * db[1] >= 0.9 and abs(dot_a) <= 0.3
            # 2. T/L end: one line, prolonged, runs into the end of a perpendicular line
            #    (a wall ending against another where an opening starts, with no jamb drawn)
            runs_into = (dot_a >= 0.95 and abs(dot_b) <= 0.3) or (-dot_b >= 0.95 and abs(dot_a) <= 0.3)
            if jamb or runs_into:
                pairs.append((dist, a, b))
    used: set[int] = set()
    for _dist, a, b in sorted(pairs):
        if a in used or b in used:
            continue
        used.update((a, b))
        closers.append(LineString([remaining[a][0], remaining[b][0]]))
    return closers


def faces_from_lines(lines: list[LineString], max_t: float) -> list[Polygon]:
    """Double-line walls: faces of the line network that are thin (strips between lines)."""
    # Snap to a 1 micron grid so ends that should meet (arc ends, 4e-16 off) really do.
    lines = [g for g in (shapely.set_precision(l, GRID) for l in lines) if not g.is_empty]
    merged = unary_union(lines)
    segs = [g for g in getattr(merged, "geoms", [merged]) if isinstance(g, LineString) and g.length > 0]
    if not segs:
        return []
    network = unary_union(segs + closure_segments(segs, max_t))
    return [p for p in polygonize(network) if p.area > 1e-6 and thinness(p) <= max_t]


def _centerline(lines: list[BaseGeometry], thickness: float) -> BaseGeometry:
    # Flat caps would leave notches at corners; square caps close them.
    return union(l.buffer(thickness / 2.0, cap_style="square", join_style="mitre") for l in lines)


def _layer_footprint(prims: list[Prim], layer: str, cfg: Config, warnings: list[str]) -> BaseGeometry:
    rings = [p.geom for p in prims if p.kind == "ring"]
    fills = [p.geom for p in prims if p.kind == "fill"]
    lines = [p.geom for p in prims if p.kind == "line"]
    mode = cfg.wall_mode

    if mode == "solid":
        if lines:
            warnings.append(f"Layer {layer}: {len(lines)} linee aperte ignorate (modalita' 'solidi').")
        return nest_polygons(rings + fills)

    if mode == "centerline":
        return _centerline(lines + [b for r in rings for b in _boundaries(r)], cfg.wall_thickness)

    if mode == "faces":
        return union(faces_from_lines(lines + [b for r in rings for b in _boundaries(r)],
                                      cfg.max_wall_thickness))

    # auto
    parts: list[BaseGeometry] = []
    ring_shape = nest_polygons(rings)
    if not ring_shape.is_empty:
        loops = []
        for poly in polygons_of(ring_shape):
            if thinness(poly) > cfg.max_wall_thickness:
                loops.append(poly)  # a room / centerline loop, not a wall outline
            else:
                parts.append(poly)
        if loops:
            warnings.append(
                f"Layer {layer}: {len(loops)} contorni chiusi troppo larghi per essere muri "
                f"sono stati trattati come assi (spessore {cfg.wall_thickness:g} m)."
            )
            parts.append(_centerline([b for l in loops for b in _boundaries(l)], cfg.wall_thickness))
    parts.extend(fills)
    if lines:
        faces = faces_from_lines(lines, cfg.max_wall_thickness)
        if faces:
            parts.extend(faces)
        else:
            warnings.append(
                f"Layer {layer}: nessun muro a doppia linea riconosciuto, le {len(lines)} linee "
                f"sono trattate come assi (spessore {cfg.wall_thickness:g} m)."
            )
            parts.append(_centerline(lines, cfg.wall_thickness))
    return union(parts)


def clean_footprint(geom: BaseGeometry, tol: float) -> BaseGeometry:
    g = fix(geom)
    if tol > 0:
        g = g.buffer(tol, join_style="mitre").buffer(-tol, join_style="mitre")
    return g.simplify(1e-4, preserve_topology=True)


def build_wall_layers(items: list[Item], cfg: Config, warnings: list[str]) -> dict[str, BaseGeometry]:
    """Footprint of each wall layer on its own (layers are never mixed before they are united)."""
    by_layer: dict[str, list[Prim]] = defaultdict(list)
    for it in items:
        if it.category == "wall":
            by_layer[it.layer].extend(it.prims)
    return {layer: _layer_footprint(prims, layer, cfg, warnings) for layer, prims in sorted(by_layer.items())}


def build_walls(items: list[Item], cfg: Config, warnings: list[str]) -> BaseGeometry:
    return clean_footprint(union(build_wall_layers(items, cfg, warnings).values()), cfg.merge_tolerance)


def build_columns(items: list[Item], cfg: Config) -> BaseGeometry:
    polys = []
    for it in items:
        if it.category == "column":
            polys.extend(p.geom for p in it.prims if p.kind in ("ring", "fill"))
    return clean_footprint(nest_polygons(polys), cfg.merge_tolerance) if polys else Polygon()
