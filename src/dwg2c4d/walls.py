"""Turn wall-layer primitives into a 2D wall footprint (metres)."""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
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


PAIR_MIN_T = 0.04  # m: two parallel lines closer than this are one line drawn twice, not two faces of a wall
PAIR_MIN_LENGTH = 0.25  # m: shorter pieces (arcs flattened to segments, ticks, jambs) never make a wall by themselves
PAIR_MIN_OVERLAP = 0.15  # m: two parallel lines must face each other along at least this much
PAIR_ANGLE = math.radians(1.0)  # two lines whose directions differ less than this are parallel
PAIR_MAX_PAIRS = 3_000_000  # more candidate pairs than this: the drawing is not made of wall lines, skip it
SPECK_AREA = 0.6  # m2: a lone body smaller than this is a piece of furniture or a symbol, not a wall...
SPECK_SIZE = 1.0  # ... if it is also shorter than this in both directions (m)


def _straight_pieces(lines: list[BaseGeometry]) -> np.ndarray:
    """(n, 4) array x0, y0, x1, y1 of every straight piece of the given lines, longer than PAIR_MIN_LENGTH."""
    out = []
    for g in lines:
        for part in getattr(g, "geoms", [g]):
            if part.geom_type == "Polygon":
                part = part.exterior
            if part.geom_type not in ("LineString", "LinearRing"):
                continue
            c = np.asarray(part.coords)[:, :2]
            if len(c) >= 2:
                out.append(np.hstack([c[:-1], c[1:]]))
    if not out:
        return np.zeros((0, 4))
    seg = np.vstack(out)
    return seg[np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1]) >= PAIR_MIN_LENGTH]


def _pair_candidates(seg: np.ndarray, theta: np.ndarray, max_t: float) -> tuple[np.ndarray, np.ndarray] | None:
    """Index pairs (i, j) of nearly parallel pieces whose midpoints may be closer than ``max_t`` across their
    direction. Pieces are binned by direction (1 degree); inside a bin and its neighbour they are sorted across
    the direction, so only those within reach are compared. None when there are too many pairs."""
    mx, my = (seg[:, 0] + seg[:, 2]) / 2, (seg[:, 1] + seg[:, 3]) / 2
    length = np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1])
    bins = np.round(np.degrees(theta)).astype(int) % 180
    out_i: list[np.ndarray] = []
    out_j: list[np.ndarray] = []
    total = 0
    for b in np.unique(bins):
        own = np.flatnonzero(bins == b)
        cand = np.flatnonzero((bins == b) | (bins == (b + 1) % 180))
        centre = math.radians(float(b))
        d = -mx * math.sin(centre) + my * math.cos(centre)
        margin = 0.5 * float(length[cand].max()) * math.sin(math.radians(2.0)) + 0.02
        reach = max_t + margin
        cand = cand[np.argsort(d[cand])]
        dc = d[cand]
        lo = np.searchsorted(dc, d[own] - reach, side="left")
        hi = np.searchsorted(dc, d[own] + reach, side="right")
        counts = hi - lo
        total += int(counts.sum())
        if total > PAIR_MAX_PAIRS:
            return None
        if counts.sum() == 0:
            continue
        i = np.repeat(own, counts)
        pos = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts) + np.repeat(lo, counts)
        j = cand[pos]
        keep = (i != j) & ~((bins[j] == b) & (j < i))  # a pair inside one bin once
        out_i.append(i[keep])
        out_j.append(j[keep])
    if not out_i:
        return np.zeros(0, dtype=int), np.zeros(0, dtype=int)
    return np.concatenate(out_i), np.concatenate(out_j)


def faces_from_pairs(lines: list[BaseGeometry], max_t: float) -> list[Polygon]:
    """Walls drawn as two parallel lines whose ends are not joined: the strip between every two parallel lines that
    face each other (closer than ``max_t``, farther than PAIR_MIN_T, overlapping along PAIR_MIN_OVERLAP at least).
    ``faces_from_lines`` needs closed outlines; this does not, so a wall that stops at a window or a door still
    counts, but so does anything else made of two near parallel lines (a bookcase, a table): the caller removes
    what is too small to be a wall."""
    seg = _straight_pieces(lines)
    if len(seg) < 2:
        return []
    theta = np.arctan2(seg[:, 3] - seg[:, 1], seg[:, 2] - seg[:, 0]) % math.pi
    found = _pair_candidates(seg, theta, max_t)
    if found is None or len(found[0]) == 0:
        return []
    ia, ib = found
    ux, uy = np.cos(theta), np.sin(theta)
    nx, ny = -uy, ux
    mx, my = (seg[:, 0] + seg[:, 2]) / 2, (seg[:, 1] + seg[:, 3]) / 2
    # The angle between the two (mod pi), and the perpendicular distance of b's midpoint from a's line.
    dtheta = np.abs((theta[ib] - theta[ia] + math.pi / 2) % math.pi - math.pi / 2)
    t = np.abs((mx[ib] - mx[ia]) * nx[ia] + (my[ib] - my[ia]) * ny[ia])
    ok = (dtheta <= PAIR_ANGLE) & (t >= PAIR_MIN_T) & (t <= max_t)
    ia, ib = ia[ok], ib[ok]
    if len(ia) == 0:
        return []
    ax, ay = ux[ia], uy[ia]
    a0 = seg[ia, 0] * ax + seg[ia, 1] * ay
    a1 = seg[ia, 2] * ax + seg[ia, 3] * ay
    b0 = seg[ib, 0] * ax + seg[ib, 1] * ay
    b1 = seg[ib, 2] * ax + seg[ib, 3] * ay
    lo = np.maximum(np.minimum(a0, a1), np.minimum(b0, b1))
    hi = np.minimum(np.maximum(a0, a1), np.maximum(b0, b1))
    good = hi - lo >= PAIR_MIN_OVERLAP
    if not good.any():
        return []
    ia, ib, lo, hi = ia[good], ib[good], lo[good], hi[good]
    ax, ay = ux[ia], uy[ia]
    nxa, nya = -ay, ax
    off_a = mx[ia] * nxa + my[ia] * nya  # across a's direction: where a's line and b's line are
    off_b = mx[ib] * nxa + my[ib] * nya
    cor = np.empty((len(ia), 5, 2))
    for k, (along, off) in enumerate(((lo, off_a), (hi, off_a), (hi, off_b), (lo, off_b), (lo, off_a))):
        cor[:, k, 0] = along * ax + off * nxa
        cor[:, k, 1] = along * ay + off * nya
    polys = shapely.polygons(cor)
    return [p for p in polys if p is not None and not p.is_empty and p.area > 1e-6]


def drop_specks(footprint: BaseGeometry) -> BaseGeometry:
    """Remove the small lone bodies (furniture drawn with two parallel lines, symbols) from a footprint built from
    parallel line pairs. A body that touches another one, or that is a long strip, stays."""
    parts = polygons_of(footprint)
    if len(parts) < 2:
        return footprint
    tree = STRtree(parts)
    kept = []
    for i, p in enumerate(parts):
        x0, y0, x1, y1 = p.bounds
        small = p.area < SPECK_AREA and max(x1 - x0, y1 - y0) < SPECK_SIZE
        if small:
            near = [j for j in tree.query(p.buffer(0.05), predicate="intersects") if j != i]
            if not near:
                continue
        kept.append(p)
    return union(kept) if kept else Polygon()


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
    paired = layer in cfg.pair_layers  # a layer chosen by the shape of its lines: double-line walls with open ends count
    ring_shape = nest_polygons(rings)
    if not ring_shape.is_empty:
        loops = []
        for poly in polygons_of(ring_shape):
            if thinness(poly) > cfg.max_wall_thickness:
                loops.append(poly)  # a room / centerline loop, not a wall outline
            else:
                parts.append(poly)
        if loops and paired:
            warnings.append(f"Layer {layer}: {len(loops)} contorni chiusi troppo larghi per essere muri "
                            f"(arredi, locali) sono stati ignorati.")
        elif loops:
            warnings.append(
                f"Layer {layer}: {len(loops)} contorni chiusi troppo larghi per essere muri "
                f"sono stati trattati come assi (spessore {cfg.wall_thickness:g} m)."
            )
            parts.append(_centerline([b for l in loops for b in _boundaries(l)], cfg.wall_thickness))
    parts.extend(fills)
    if paired:
        parts.extend(faces_from_pairs(lines + [b for r in rings for b in _boundaries(r)], cfg.max_wall_thickness))
        if lines:
            parts.extend(faces_from_lines(lines, cfg.max_wall_thickness))
        return drop_specks(union(parts))
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
