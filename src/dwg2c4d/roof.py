"""Roof from a roof plan: outline + ridge/hip/valley lines + a pitch.

The roof plan's linework is turned into planar faces (like double-line walls). Every
face has one *eave edge* (its longest edge on the outline); a node's height is
``tan(pitch) * distance to the eave line``, averaged over the faces meeting there, so
the surface is continuous by construction. Corner nodes land on 0, ridge nodes on the
ridge height, gable-end ridge nodes on the outline get the ridge height too.

When elevations are given, ridge heights are read from them: a horizontal line in the
elevation whose x-extent matches a ridge of the roof plan fixes that ridge's height, and
the pitch is the median of what those ridges imply.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union

from .config import Config
from .geom import polygons_of
from .reader import Item
from .walls import _boundaries, closure_segments, thinness

Vec3 = tuple[float, float, float]

GRID = 1e-6  # input snapping (m)
NODE_GRID = 1e-3  # nodes closer than this are the same node (m)
MIN_FACE_AREA = 0.05  # m^2
MIN_FACE_THICKNESS = 0.25  # thinner faces are fascia bands between nested outlines
CLOSE_GAP = 0.10  # roof lines that stop this short of each other are joined
RIDGE_MIN_RISE = 0.3  # a silhouette line must be this far above the eaves to count as a ridge
RIDGE_MATCH = 0.8  # fraction of the longer of {ridge, silhouette line} that must overlap in x
MIN_RIDGE = 1.0  # shorter horizontal roof lines (connectors, valleys) are not ridges (m)
ON_OUTLINE = 1e-4
STEP_TOL = 0.02  # neighbouring faces closer in height than this meet (no wall between them) (m)


@dataclass
class Roof:
    triangles: list[tuple[Vec3, Vec3, Vec3]]  # top surface; z is height above the eaves
    edges: list[tuple[Vec3, Vec3]]  # outline edges, outward on the right-hand side
    steps: list[list[Vec3]]  # vertical walls between neighbouring faces that do not meet (gables)
    pitch_deg: float  # typical (median) pitch
    pitch_range: tuple[float, float]  # steepest / shallowest face, degrees
    pitch_source: str  # "indicata" | "prospetto" | "predefinita"
    ridge_height: float  # highest point above the eaves
    faces: int
    offset: tuple[float, float]  # translation applied to the roof plan (m)
    ridges_from_elevation: int


def _key(x: float, y: float) -> tuple[int, int]:
    return round(x / NODE_GRID), round(y / NODE_GRID)


def _edges(poly: Polygon):
    poly = orient(poly, 1.0)
    for ring in (poly.exterior, *poly.interiors):
        c = list(ring.coords)
        yield from zip(c, c[1:])


def _line_dist(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return abs(dx * (p[1] - a[1]) - dy * (p[0] - a[0])) / n if n else 0.0


def _drop_bands(faces: list[Polygon]) -> list[Polygon]:
    """Drop ring-shaped faces: the band between two nested outlines (eave and wall line,
    or fascia) that no hip/ridge line crosses. A real roof plane has no hole filled by
    other roof planes."""
    kept = []
    for i, f in enumerate(faces):
        if f.interiors:
            others = unary_union([g for j, g in enumerate(faces) if j != i])
            holes = [Polygon(r) for r in f.interiors]
            if all(h.intersection(others).area >= 0.8 * h.area for h in holes):
                continue
        kept.append(f)
    return kept


def _ridge_runs(network, outline_boundary) -> list[tuple[float, float, float]]:
    """Horizontal (constant-y) interior line runs as (y, x0, x1), touching pieces merged."""
    pieces = []
    for g in getattr(network, "geoms", [network]):
        if not isinstance(g, LineString):
            continue
        c = list(g.coords)
        for (ax, ay), (bx, by) in zip(c, c[1:]):
            if abs(ay - by) > NODE_GRID or abs(bx - ax) < NODE_GRID:
                continue
            mid = Point((ax + bx) / 2, (ay + by) / 2)
            if outline_boundary.distance(mid) < ON_OUTLINE:
                continue
            pieces.append((round(ay / NODE_GRID), min(ax, bx), max(ax, bx)))
    pieces.sort()
    runs: list[list[float]] = []
    for yk, x0, x1 in pieces:
        y = yk * NODE_GRID
        if runs and abs(runs[-1][0] - y) < NODE_GRID and x0 <= runs[-1][2] + NODE_GRID:
            runs[-1][2] = max(runs[-1][2], x1)
        else:
            runs.append([y, x0, x1])
    return [tuple(r) for r in runs]


def build_roof(items: list[Item], cfg: Config, unit_scale: float,
               walls_bounds: tuple[float, float, float, float],
               ridge_hints: list[tuple[float, float, float]],
               warnings: list[str]) -> Roof | None:
    """``ridge_hints``: (x0, x1, height above the eaves), metres, in plan coordinates."""
    lines: list[LineString] = []
    for it in items:
        if it.category != "roof":
            continue
        for p in it.prims:
            if p.kind == "line":
                lines.append(p.geom)
            elif p.kind == "ring":
                lines.extend(_boundaries(p.geom))
    if not lines:  # a roof drawn only as a filled hatch: use its boundary as the outline
        for it in items:
            if it.category == "roof":
                lines.extend(b for p in it.prims if p.kind == "fill" for b in _boundaries(p.geom))
    lines = [g for g in (shapely.set_precision(l, GRID) for l in lines) if not g.is_empty]
    if not lines:
        warnings.append("Tetto: nessuna linea trovata sui layer del tetto (Tetto/Roof/Copertura): "
                        "indicali con --layer-tetto o l'area con --area-tetto.")
        return None

    merged = unary_union(lines)
    segs = [g for g in getattr(merged, "geoms", [merged]) if isinstance(g, LineString) and g.length > 0]
    network = unary_union(segs + closure_segments(segs, CLOSE_GAP))
    faces = [f for f in polygonize(network)
             if f.area >= MIN_FACE_AREA and thinness(f) > MIN_FACE_THICKNESS]
    faces = _drop_bands(faces)
    if not faces:
        warnings.append("Tetto: le linee del tetto non formano nessuna falda chiusa.")
        return None

    if len(faces) == 1:
        warnings.append("Tetto: una sola falda (nessun colmo o displuvio disegnato nella pianta del tetto): "
                        "falda unica che sale dal lato piu' lungo.")

    # Move the roof plan onto the building: centre of its outline on the centre of the walls.
    outline = unary_union(faces)
    ox0, oy0, ox1, oy1 = outline.bounds
    if cfg.roof_offset is not None:
        dx, dy = cfg.roof_offset[0] * unit_scale, cfg.roof_offset[1] * unit_scale
    else:
        wx0, wy0, wx1, wy1 = walls_bounds
        dx, dy = (wx0 + wx1) / 2 - (ox0 + ox1) / 2, (wy0 + wy1) / 2 - (oy0 + oy1) / 2
    shift = lambda g: shapely.transform(g, lambda a: a + [dx, dy])  # noqa: E731
    faces = [shift(f) for f in faces]
    network = shift(network)
    outline = unary_union(faces)
    outline_boundary = outline.boundary

    # Eave edge of every face = its longest edge lying on the outline.
    node_pos: dict[tuple[int, int], tuple[float, float]] = {}
    faces_at: dict[tuple[int, int], list[int]] = defaultdict(list)
    eave: dict[int, tuple[tuple[float, float], tuple[float, float]]] = {}
    oriented: list[Polygon] = []
    outline_edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for i, face in enumerate(faces):
        face = orient(face, 1.0)
        oriented.append(face)
        best = None
        for a, b in _edges(face):
            for pt in (a, b):
                k = _key(*pt)
                node_pos.setdefault(k, pt)
                if i not in faces_at[k]:
                    faces_at[k].append(i)
            mid = Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            if outline_boundary.distance(mid) < ON_OUTLINE:
                outline_edges.append((a, b))
                length = math.hypot(b[0] - a[0], b[1] - a[1])
                if best is None or length > best[0]:
                    best = (length, a, b)
        if best:
            eave[i] = (best[1], best[2])

    dist: dict[tuple[int, int, int], float] = {}  # (face, node) -> distance to the face's eave line
    for k, fs in faces_at.items():
        for i in fs:
            if i in eave:
                dist[(i, *k)] = _line_dist(node_pos[k], *eave[i])
    nodes_with_eave = {k for (_, *k2) in dist for k in [tuple(k2)]}
    lost = [k for k in node_pos if k not in nodes_with_eave]
    if lost:
        warnings.append(f"Tetto: {len(lost)} punti senza una gronda di riferimento sono stati messi alla quota di gronda.")

    # Ridges whose height an elevation gives (an explicit pitch wins over the elevation).
    run_info: list[tuple[list[tuple[int, int]], float, float, float, float]] = []
    if cfg.roof_pitch is None:
        for y, x0, x1 in _ridge_runs(network, outline_boundary):
            if x1 - x0 < MIN_RIDGE:
                continue
            best_h, best_ov = None, 0.0
            for hx0, hx1, h in ridge_hints:
                ov = min(x1, hx1) - max(x0, hx0)
                if ov >= RIDGE_MATCH * max(x1 - x0, hx1 - hx0) and ov > best_ov:
                    best_h, best_ov = h, ov
            if best_h is None:
                continue
            ks = [k for k, (nx, ny) in node_pos.items()
                  if abs(ny - y) < NODE_GRID and x0 - NODE_GRID <= nx <= x1 + NODE_GRID]
            if ks:
                run_info.append((ks, best_h, y, x0, x1))

    # Slope of each face: from the ridge nodes the elevation fixed on it, else the default.
    if cfg.roof_pitch is not None:
        default_k, source = math.tan(math.radians(cfg.roof_pitch)), "indicata"
    else:
        default_k, source = math.tan(math.radians(cfg.roof_default_pitch)), "predefinita"
    # A ridge's height belongs to the faces that have it as an edge and to the end faces that
    # rest on its ends (their eave is across the ridge); not to a face that merely touches a
    # ridge end of another roof volume.
    def owns(face_i: int, y: float, x0: float, x1: float) -> bool:
        for a, b in _edges(oriented[face_i]):
            if abs(a[1] - y) < NODE_GRID and abs(b[1] - y) < NODE_GRID \
                    and min(max(a[0], b[0]), x1) - max(min(a[0], b[0]), x0) > 0.1:
                return True
        ea, eb = eave[face_i]
        return abs(eb[0] - ea[0]) < 0.3 * math.hypot(eb[0] - ea[0], eb[1] - ea[1])  # eave across the ridge

    face_k: dict[int, float] = {}
    measured: list[float] = []
    fixed: dict[tuple[int, int], float] = {}
    for i in eave:
        ratios = []
        for ks, h, y, x0, x1 in run_info:
            if not owns(i, y, x0, x1):
                continue
            for k in ks:
                if (i, *k) in dist and dist[(i, *k)] > 0.05:
                    ratios.append(h / dist[(i, *k)])
                    fixed[k] = h
        if ratios:
            face_k[i] = sum(ratios) / len(ratios)
            measured.append(face_k[i])
    if measured:
        source = "prospetto"
        fallback_k = float(np.median(measured))
    else:
        fallback_k = default_k
        if ridge_hints and cfg.roof_pitch is None:
            warnings.append("Tetto: nessun colmo della pianta del tetto corrisponde alle linee orizzontali "
                            "del prospetto (stesse coordinate X?): pendenza predefinita.")

    # Every face is its own plane (slope from its ridge nodes, else the typical one). Faces without
    # an eave of their own take the average of the planes around them.
    zf: dict[tuple[int, int, int], float] = {}
    for (i, *k), d in dist.items():
        zf[(i, *k)] = face_k.get(i, fallback_k) * d
    node_avg: dict[tuple[int, int], float] = {}
    for k, fs in faces_at.items():
        vals = [zf[(i, *k)] for i in fs if (i, *k) in zf]
        node_avg[k] = sum(vals) / len(vals) if vals else 0.0
    for i, face in enumerate(oriented):
        for a, _ in _edges(face):
            k = _key(*a)
            zf.setdefault((i, *k), node_avg[k])
    # Heights that differ by less than a step tolerance are the same height (so the mesh welds).
    for k, fs in faces_at.items():
        vals = sorted((zf[(i, *k)], i) for i in fs)
        group = [vals[0]]
        for v in vals[1:] + [None]:
            if v is not None and v[0] - group[-1][0] <= STEP_TOL:
                group.append(v)
                continue
            mean = sum(g[0] for g in group) / len(group)
            for _, i in group:
                zf[(i, *k)] = mean
            group = [v] if v is not None else []

    def zat(i: int, pt) -> float:
        return zf[(i, *_key(*pt))]

    ks_all = [face_k.get(i, fallback_k) for i in eave]
    k_slope = float(np.median(ks_all)) if ks_all else fallback_k

    triangles: list[tuple[Vec3, Vec3, Vec3]] = []
    for i, face in enumerate(oriented):
        for tri in shapely.constrained_delaunay_triangles(face).geoms:
            p = [(c[0], c[1], zat(i, c)) for c in list(tri.exterior.coords)[:3]]
            nz = (p[1][0] - p[0][0]) * (p[2][1] - p[0][1]) - (p[2][0] - p[0][0]) * (p[1][1] - p[0][1])
            if abs(nz) < 1e-12:
                continue
            triangles.append((p[0], p[1], p[2]) if nz > 0 else (p[0], p[2], p[1]))

    # Outline sides, and a vertical wall wherever two neighbouring faces differ in height along
    # their shared edge (a higher gable standing over a lower roof).
    edges: list[tuple[Vec3, Vec3]] = []
    by_edge: dict[frozenset, list[tuple[int, tuple[float, float], tuple[float, float]]]] = defaultdict(list)
    for i, face in enumerate(oriented):
        for a, b in _edges(face):
            by_edge[frozenset((_key(*a), _key(*b)))].append((i, a, b))
    steps: list[list[Vec3]] = []
    for pair in by_edge.values():
        if len(pair) == 1:
            i, a, b = pair[0]
            mid = Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            if outline_boundary.distance(mid) < ON_OUTLINE:
                edges.append(((a[0], a[1], zat(i, a)), (b[0], b[1], zat(i, b))))
            continue
        (f, a, b), (g, _, _) = pair[0], pair[1]  # f has the edge a->b with f on its left
        da, db = zat(f, a) - zat(g, a), zat(f, b) - zat(g, b)
        if abs(da) <= STEP_TOL and abs(db) <= STEP_TOL:
            continue
        pa, pb = (a[0], a[1]), (b[0], b[1])

        def piece(p0, p1, d0, d1, fa, fb, ga, gb):
            lower_is_f = (d0 + d1) < 0
            lo0, lo1 = (fa, fb) if lower_is_f else (ga, gb)
            hi0, hi1 = (ga, gb) if lower_is_f else (fa, fb)
            quad = [(*p0, lo0), (*p1, lo1), (*p1, hi1), (*p0, hi0)]  # normal on the right of p0->p1
            return quad[::-1] if lower_is_f else quad  # the wall must face the lower face

        fa, fb, ga, gb = zat(f, a), zat(f, b), zat(g, a), zat(g, b)
        if da * db < 0:  # the two profiles cross: two pieces
            t = da / (da - db)
            pm = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
            zm = fa + t * (fb - fa)
            steps.append(piece(pa, pm, da, 0.0, fa, zm, ga, zm))
            steps.append(piece(pm, pb, 0.0, db, zm, fb, zm, gb))
        else:
            steps.append(piece(pa, pb, da, db, fa, fb, ga, gb))
    return Roof(
        triangles=triangles,
        edges=edges,
        steps=steps,
        pitch_deg=math.degrees(math.atan(k_slope)),
        pitch_range=(math.degrees(math.atan(min(ks_all))), math.degrees(math.atan(max(ks_all))))
        if ks_all else (0.0, 0.0),
        pitch_source=source,
        ridge_height=max((v for v in zf.values()), default=0.0),
        faces=len(faces),
        offset=(dx, dy),
        ridges_from_elevation=len(run_info),
    )


def ridge_hints_from(elevations, wall_height: float) -> list[tuple[float, float, float]]:
    """Silhouette lines high enough above the eaves to be ridges: (x0, x1, height above eaves)."""
    hints = []
    for ev in elevations:
        for y, x0, x1 in ev.hlines:
            rise = (y - ev.zero) - wall_height
            if rise >= RIDGE_MIN_RISE and x1 - x0 >= 1.0:
                hints.append((x0, x1, rise))
    return hints
