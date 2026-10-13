"""The terrain under the building: from the contour lines the sheet draws, with the house on a levelled platform.

Contour lines. A layer whose polylines each sit at one elevation (the entity's elevation, or the z of its vertices) and
whose elevations are evenly spaced (291.40, 291.60, ...) over at least ``MIN_LEVELS`` levels is a set of contour lines:
what the geometry says counts, the name of the layer does not (``--terreno`` accepts fewer levels). Their heights are in
the units of the drawing, like their plan coordinates.

Placing them. The contour lines lie in the frame of the survey, which is often another view of the sheet than the plan
that is converted: ``sitefit`` finds the move that lays the plan on it (nothing is guessed when it does not: no
terrain, a remark in the report). The finished floor of the house is the zero of the model: it is read from the levels
the sheet writes (``+0.00 (297.40)``, ``QUOTA PIANO 297.40``), else it is the height of the terrain under the building.

The ground. The heights are interpolated on a Delaunay triangulation of the contour vertices (long segments cut first),
read on a regular grid around the building, with a flat platform under it and a strip around it; beyond the platform the
ground keeps its own height but never rises or falls faster than ``SLOPE_MAX`` from the platform (an embankment). The mesh
is a closed slab, ``--spessore-terreno`` thick below its lowest point, one object ``Terreno``.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import shapely
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
from scipy.spatial import Delaunay, QhullError
from shapely.geometry import MultiPoint, box
from shapely.geometry.base import BaseGeometry

from .config import Config
from .geom import union
from .mesh import Mesh
from .reader import layer_used
from .sitefit import Placement, register

# --- the contour lines ---
MIN_LEVELS = 10  # distinct elevations a layer needs to be taken for contour lines without being asked...
MIN_LEVELS_ASKED = 3  # ... and with --terreno
LEVEL_RESOLUTION = 0.001  # m: elevations are compared to the millimetre
LATTICE_TOLERANCE = 0.002  # m: an elevation lies on the grid of the step within this
STEP_SHARE = 0.6  # at least this share of the gaps between neighbouring levels is the step (the others are missing levels)
STEP_RANGE = (0.05, 25.0)  # m: a plausible interval between contour lines
RELIEF_RANGE = (0.3, 400.0)  # m: a plausible difference between the lowest and the highest contour
FLAT_LINE = 0.002  # m: a polyline whose vertices differ in height by less than this lies at one elevation
DENSIFY = 2.0  # m: contour segments longer than this get vertices of their own (a straight run would make flat triangles)
SPLINE_TOLERANCE = 0.05  # m: a spline contour is cut into pieces within this of the curve
MAX_LEVELS = 3000  # a layer with more distinct elevations than this is a cloud of spot heights, not contour lines

# --- the ground ---
MARGIN = 20.0  # m: the terrain reaches this far beyond the building
HULL_SLACK = 8.0  # m: the building must lie within this of the area the contour lines cover
GRID = 1.0  # m: size of the cells of the mesh of the terrain
MAX_CELLS = 40000  # the cells grow when the terrain would have more
PLATFORM_MARGIN = 2.0  # m: the flat ground reaches this far beyond the walls (more than the diagonal of a cell: no cell is half flat there)
SLOPE_MAX = 0.5  # rise / run: how steep the embankment between the platform and the natural ground is at most (1 : 2)
CROP = 15.0  # m: contour vertices this far beyond the terrain are no use to its edge

# --- the floor level ---
LEVEL_PAIR = re.compile(r"([+\-±−])\s*(\d{1,3}(?:[.,]\d{1,3})?)\s*[(\[]\s*(\d{1,4}[.,]\d{1,3})\s*[)\]]")  # +0.00 (297.40)
LEVEL_NAMED = re.compile(r"quota\s+(?:di\s+)?(?:piano|pavimento|calpestio|zero)(?:\s+finito)?\s*[:=]?\s*(\d{1,4}[.,]\d{1,3})", re.I)
LEVEL_ZERO = re.compile(r"[+±]\s*/?\s*-?\s*0[.,]0{1,3}\s*[=:]\s*(?:m\s*)?(\d{1,4}[.,]\d{1,3})", re.I)  # +/-0.00 = 297.40
FLOOR_SLACK = 5.0  # m: a written floor level farther than this from the contour lines' heights is in another datum


@dataclass
class Contours:
    """The contour lines of one layer, in the frame of the drawing, metres."""

    layer: str
    xyz: np.ndarray  # (N, 3) vertices, long segments cut
    levels: list[float]  # the distinct elevations on the grid of the step
    step: float
    lines: int  # polylines


@dataclass
class Terrain:
    """The ground in the frame of the plan, metres, heights relative to the finished floor of the building."""

    x0: float  # lower left corner of the grid
    y0: float
    step: float  # size of a cell
    z: np.ndarray  # (ny + 1, nx + 1) heights of the grid points
    bottom: float  # the underside of the slab
    platform: BaseGeometry  # the flat ground under and around the building
    floor_level: float  # the finished floor in the heights of the contour lines (m a.s.l., or the datum of the drawing)
    floor_source: str  # scritta | terreno
    note: dict = field(default_factory=dict)  # what the report says

    @property
    def shape(self) -> tuple[int, int]:
        """(cells in y, cells in x)."""
        return self.z.shape[0] - 1, self.z.shape[1] - 1

    @property
    def window(self) -> tuple[float, float, float, float]:
        ny, nx = self.shape
        return self.x0, self.y0, self.x0 + nx * self.step, self.y0 + ny * self.step

    def split_along_first_diagonal(self) -> np.ndarray:
        """For each cell, is the diagonal from its lower left to its upper right corner the one that cuts it in two
        triangles (the one whose ends are the closer in height: the surface bends the least)?"""
        z = self.z
        return np.abs(z[:-1, :-1] - z[1:, 1:]) <= np.abs(z[:-1, 1:] - z[1:, :-1])

    def height(self, x: np.ndarray | float, y: np.ndarray | float) -> np.ndarray:
        """The height of the mesh at (x, y): the points beyond the grid take the height of its edge."""
        ny, nx = self.shape
        fx = np.clip((np.asarray(x, float) - self.x0) / self.step, 0.0, nx - 1e-9)
        fy = np.clip((np.asarray(y, float) - self.y0) / self.step, 0.0, ny - 1e-9)
        i, j = fx.astype(int), fy.astype(int)
        u, v = fx - i, fy - j
        z = self.z
        a, b, c, d = z[j, i], z[j, i + 1], z[j + 1, i + 1], z[j + 1, i]
        first = self.split_along_first_diagonal()[j, i]
        with_ab = np.where(first, a + u * (b - a) + v * (c - b), a + u * (b - a) + v * (d - a))  # the triangle that has the edge a-b
        other = np.where(first, a + u * (c - d) + v * (d - a), c + (1.0 - u) * (d - c) + (1.0 - v) * (b - c))
        return np.where(np.where(first, v <= u, u + v <= 1.0), with_ab, other)


# --- reading the contour lines ---

def _spline_points(e, dist: float) -> list[tuple[float, float, float]]:
    return [(p.x, p.y, p.z) for p in e.flattening(dist)]


def _entity_points(e, dist: float) -> list[tuple[float, float, float]] | None:
    """The vertices of a polyline, spline or line that can be a contour: (x, y, z) in drawing units, z being the elevation of
    the entity when the vertices carry none."""
    t = e.dxftype()
    flip = e.dxf.get("extrusion", (0, 0, 1))[2] < 0  # drawn in an OCS turned the other way: x and z change sign
    if t == "LWPOLYLINE":
        z = float(e.dxf.get("elevation", 0.0) or 0.0)
        pts = [(p[0], p[1], z) for p in e.get_points("xy")]
    elif t == "POLYLINE":
        if e.is_poly_face_mesh or e.is_polygon_mesh:
            return None
        lift = e.dxf.get("elevation", (0, 0, 0))[2]
        pts = [(v.dxf.location.x, v.dxf.location.y, v.dxf.location.z or lift) for v in e.vertices]
        if e.is_closed and pts:
            pts.append(pts[0])
    elif t == "SPLINE":
        pts = _spline_points(e, dist)
    elif t == "LINE":
        pts = [(e.dxf.start.x, e.dxf.start.y, e.dxf.start.z), (e.dxf.end.x, e.dxf.end.y, e.dxf.end.z)]
    else:
        return None
    if t == "LWPOLYLINE" and e.closed and pts:
        pts.append(pts[0])
    return [(-x, y, -z) for x, y, z in pts] if flip else pts


def _densified(xyz: np.ndarray, spacing: float) -> np.ndarray:
    """``xyz`` with extra vertices on the segments longer than ``spacing`` (heights interpolated)."""
    if len(xyz) < 2:
        return xyz
    length = np.hypot(*np.diff(xyz[:, :2], axis=0).T)
    pieces = np.maximum(np.ceil(length / spacing).astype(int), 1)
    if (pieces == 1).all():
        return xyz
    out = [xyz[:1]]
    for k, n in enumerate(pieces):
        if n > 1:
            t = (np.arange(1, n) / n)[:, None]
            out.append(xyz[k] + t * (xyz[k + 1] - xyz[k]))
        out.append(xyz[k + 1:k + 2])
    return np.concatenate(out)


def lattice(levels: list[float]) -> tuple[list[float], float] | None:
    """The levels that stand on one regular grid and the step of that grid: the most common gap between neighbouring levels
    is the step; the grid is the one (a shift of it) that holds most levels. None when the gaps are not regular enough."""
    mm = np.array(sorted({round(z / LEVEL_RESOLUTION) for z in levels}), dtype=np.int64)
    if len(mm) < 2 or len(mm) > MAX_LEVELS:
        return None
    gaps = Counter(np.diff(mm).tolist())
    step = max(gaps.items(), key=lambda kv: (kv[1], -kv[0]))[0]
    tol = max(1, round(LATTICE_TOLERANCE / LEVEL_RESOLUTION))
    best: np.ndarray | None = None
    for ref in mm[:: max(1, len(mm) // 300)]:
        r = (mm - ref) % step
        on = mm[np.minimum(r, step - r) <= tol]
        if best is None or len(on) > len(best):
            best = on
    if best is None or len(best) < 2:
        return None
    run = np.diff(best)
    if (np.abs(run - step) <= tol).sum() < STEP_SHARE * len(run):
        return None
    return [float(v) * LEVEL_RESOLUTION for v in best], step * LEVEL_RESOLUTION


def read_contours(doc, cfg: Config, unit_scale: float, minimum: int) -> list[Contours]:
    """The layers of the drawing that hold contour lines (``minimum`` levels at least), the one with most levels first."""
    dist = SPLINE_TOLERANCE / unit_scale
    flat_tol = FLAT_LINE / unit_scale
    by_layer: dict[str, list[tuple[float | None, np.ndarray]]] = {}
    for e in doc.modelspace().query("LWPOLYLINE POLYLINE SPLINE LINE"):
        layer = e.dxf.layer
        if not layer_used(doc, cfg, layer):
            continue
        try:
            pts = _entity_points(e, dist)
        except Exception:
            continue
        if not pts or len(pts) < 2:
            continue
        a = np.asarray(pts, float)
        if not np.isfinite(a).all() or not (a[:, 2] != 0).any():
            continue
        flat = float(np.ptp(a[:, 2])) <= flat_tol
        by_layer.setdefault(layer, []).append((float(a[0, 2]) if flat else None, a))
    found = []
    for layer, entries in by_layer.items():
        flat_levels = [z * unit_scale for z, _ in entries if z is not None]
        grid = lattice(flat_levels) if len(set(flat_levels)) >= minimum else None
        if grid is None or len(grid[0]) < minimum:
            continue
        levels, step = grid
        relief = levels[-1] - levels[0]
        if not (STEP_RANGE[0] <= step <= STEP_RANGE[1] and RELIEF_RANGE[0] <= relief <= RELIEF_RANGE[1]):
            continue
        tol = LATTICE_TOLERANCE
        on = np.asarray(levels)
        parts, lines = [], 0
        for z, a in entries:
            zm = (z if z is not None else float(a[:, 2].mean())) * unit_scale
            if z is not None and np.abs(on - zm).min() > tol or z is None and not (on[0] - step <= zm <= on[-1] + step):
                continue  # a polyline of the layer that is no contour (a spot height, a stray)
            lines += 1
            parts.append(_densified(a, DENSIFY / unit_scale))
        xyz = np.concatenate(parts) * unit_scale
        found.append(Contours(layer, xyz, levels, step, lines))
    return sorted(found, key=lambda c: -len(c.levels))


# --- the finished floor ---

def _number(text: str) -> float:
    return float(text.replace(",", "."))


def written_floor_level(texts: list[str], low: float, high: float) -> tuple[float, str] | None:
    """The finished floor in the heights of the contour lines, from the levels the sheet writes: a level relative to the
    floor with the absolute one beside it (``+ 0.00 (297.40)``, ``- 2.40 (295.00)``: each gives the height of the zero),
    ``QUOTA PIANO 297.40``, ``+/-0.00 = 297.40``. The value most texts agree on wins (within a centimetre); heights farther
    than ``FLOOR_SLACK`` from the contour lines' (``low``, ``high``) are in another datum and do not count."""
    votes: Counter = Counter()
    for text in texts:
        for sign, rel, absolute in LEVEL_PAIR.findall(text):
            zero = _number(absolute) - (-1.0 if sign in "-−" else 1.0) * _number(rel)
            votes[round(zero, 2)] += 1
        for pattern in (LEVEL_NAMED, LEVEL_ZERO):
            for value in pattern.findall(text):
                votes[round(_number(value), 2)] += 1
    votes = Counter({v: n for v, n in votes.items() if low - FLOOR_SLACK <= v <= high + FLOOR_SLACK})
    if not votes:
        return None
    top = max(votes.values())
    value = max((v for v, n in votes.items() if n == top), key=lambda v: sum(n for w, n in votes.items() if abs(w - v) <= 0.015))
    return value, f"{sum(n for w, n in votes.items() if abs(w - value) <= 0.015)} scritte"


def sheet_texts(doc) -> list[str]:
    """Every text of the modelspace, formatting codes left in (only numbers and a few words are looked for)."""
    out = []
    for e in doc.modelspace().query("TEXT MTEXT"):
        try:
            out.append(e.dxf.text if e.dxftype() == "TEXT" else e.plain_text())
        except Exception:
            continue
    return out


# --- the ground ---

def _surface(points: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Heights of the triangulation of ``points`` (N x 3) at the grid points (xs, ys): linear inside the triangles, the
    height of the nearest contour vertex outside the area they cover."""
    xy = points[:, :2]
    _, keep = np.unique(np.round(xy / 0.001).astype(np.int64), axis=0, return_index=True)
    xy, z = xy[keep], points[keep, 2]
    linear = LinearNDInterpolator(Delaunay(xy), z)(xs, ys)
    missing = np.isnan(linear)
    if missing.any():
        linear[missing] = NearestNDInterpolator(xy, z)(xs[missing], ys[missing])
    return linear


def _grid(window: tuple[float, float, float, float]) -> tuple[float, int, int]:
    """(cell size, cells in x, cells in y) for ``window``."""
    w, h = window[2] - window[0], window[3] - window[1]
    step = GRID
    while math.ceil(w / step) * math.ceil(h / step) > MAX_CELLS:
        step *= 1.5
    return step, max(int(math.ceil(w / step)), 1), max(int(math.ceil(h / step)), 1)


def build_ground(points: np.ndarray, footprint: BaseGeometry, platform: BaseGeometry, floor: float, level: float,
                 thickness: float) -> Terrain:
    """The terrain: ``points`` (N x 3, the contour vertices in the frame of the plan, heights absolute); ``floor`` the height
    of the finished floor in them; ``level`` where the platform lies (relative to the floor)."""
    x0, y0, x1, y1 = box(*footprint.bounds).buffer(MARGIN).bounds
    step, nx, ny = _grid((x0, y0, x1, y1))
    xs, ys = np.meshgrid(x0 + np.arange(nx + 1) * step, y0 + np.arange(ny + 1) * step)
    natural = _surface(points, xs.ravel(), ys.ravel()).reshape(xs.shape) - floor
    away = shapely.distance(platform, shapely.points(xs.ravel(), ys.ravel())).reshape(xs.shape)
    z = level + np.clip(natural - level, -SLOPE_MAX * away, SLOPE_MAX * away)
    return Terrain(x0, y0, step, z, float(z.min()) - thickness, platform, floor, "")


# --- the mesh ---

def _triangle(mesh: Mesh, group: str, a, b, c) -> None:
    a, b, c = np.asarray(a, float), np.asarray(b, float), np.asarray(c, float)
    n = np.cross(b - a, c - a)
    length = float(np.linalg.norm(n))
    if length > 1e-12:
        mesh.add_face(group, [tuple(a), tuple(b), tuple(c)], tuple(n / length))


def add_terrain(mesh: Mesh, t: Terrain, group: str = "Terreno") -> None:
    """The slab: the top (two triangles per cell, cut along the diagonal that bends the surface the least), the four sides
    down to ``t.bottom`` and a bottom that shares every vertex of the edge (fanned from the middle): a closed solid."""
    ny, nx = t.shape
    xs = t.x0 + np.arange(nx + 1) * t.step
    ys = t.y0 + np.arange(ny + 1) * t.step
    first = t.split_along_first_diagonal()
    z = t.z

    def p(i: int, j: int) -> tuple[float, float, float]:
        return float(xs[i]), float(ys[j]), float(z[j, i])

    for j in range(ny):
        for i in range(nx):
            a, b, c, d = p(i, j), p(i + 1, j), p(i + 1, j + 1), p(i, j + 1)
            for tri in ((a, b, c), (a, c, d)) if first[j, i] else ((a, b, d), (b, c, d)):
                _triangle(mesh, group, *tri)
    ring = [(i, 0) for i in range(nx)] + [(nx, j) for j in range(ny)] + [(i, ny) for i in range(nx, 0, -1)] \
        + [(0, j) for j in range(ny, 0, -1)]  # the edge, counter-clockwise from above
    zb = t.bottom
    middle = (float(xs[nx // 2]), float(ys[ny // 2]), zb)
    for k, (i, j) in enumerate(ring):
        i2, j2 = ring[(k + 1) % len(ring)]
        a, b = p(i, j), p(i2, j2)
        mesh.add_face(group, [(a[0], a[1], zb), (b[0], b[1], zb), b, a], _outward(a, b))
        mesh.add_face(group, [middle, (b[0], b[1], zb), (a[0], a[1], zb)], (0.0, 0.0, -1.0))


def _outward(a, b) -> tuple[float, float, float]:
    """The horizontal normal pointing out of a boundary edge a -> b walked counter-clockwise."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    return dy / length, -dx / length, 0.0
