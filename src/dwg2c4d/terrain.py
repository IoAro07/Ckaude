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
ground keeps its own height but never rises or falls faster than ``SLOPE_MAX`` from the platform (an embankment). The
platform lies where the underside of the floors is (``-floor_thickness``), so floors and walls stand on it. The mesh is a
closed slab, ``--spessore-terreno`` thick below its lowest point (see ``terrainmesh``), one object ``Terreno``.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import shapely
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
from scipy.spatial import Delaunay
from shapely.geometry import MultiPoint, box
from shapely.geometry.base import BaseGeometry

from .config import Config
from .geom import union
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
MAX_HOLE = 5  # steps: a gap between two levels wider than this is not a missing level but a stray line at another height
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
POOL_MARGIN = 1.0  # m: the flat ground reaches this far beyond a pool (which is dug flat, at the level of the house)

# --- the finished floor ---
LEVEL_PAIR = re.compile(r"([+\-±−])\s*(\d{1,3}(?:[.,]\d{1,3})?)\s*[(\[]\s*(\d{1,4}[.,]\d{1,3})\s*[)\]]")  # +0.00 (297.40)
LEVEL_NAMED = re.compile(r"quota\s+(?:di\s+)?(?:piano|pavimento|calpestio|zero)(?:\s+finito)?\s*[:=]?\s*(\d{1,4}[.,]\d{1,3})", re.I)
LEVEL_ZERO = re.compile(r"[+±]\s*/?\s*-?\s*0[.,]0{1,3}\s*[=:]\s*(?:m\s*)?(\d{1,4}[.,]\d{1,3})", re.I)  # +/-0.00 = 297.40
FLOOR_SLACK = 5.0  # m: a written floor level farther than this from the contour lines' heights is in another datum
FOOTPRINT_SAMPLE = 2.0  # m: spacing of the points at which the ground under the building is read
FLOOR_FROM_GROUND = "terreno sotto l'edificio"


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
    level: float  # the height of the platform (the underside of the floors)
    bottom: float  # the underside of the slab
    platform: BaseGeometry  # the flat ground under and around the building
    hole: BaseGeometry  # ground left out of the top (a pool is dug there)
    info: dict = field(default_factory=dict)  # what the report says

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

    def lift(self, x: np.ndarray | float, y: np.ndarray | float) -> np.ndarray:
        """How much higher the ground is at (x, y) than on the platform: what a flat garden drawn at the level of the
        house must be raised by there."""
        return self.height(x, y) - self.level


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
    is the step; the grid is the one (a shift of it) that holds most levels; of the levels on it the biggest group without
    a hole wider than ``MAX_HOLE`` steps is kept (a stray line at another height falls on the grid by chance). None when
    the gaps are not regular enough."""
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
    cuts = np.flatnonzero(np.diff(best) > MAX_HOLE * step) + 1  # the groups of levels, split at the wide holes
    best = max(np.split(best, cuts), key=len)
    if len(best) < 2:
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
        on = np.asarray(levels)
        parts, lines = [], 0
        for z, a in entries:
            zm = (z if z is not None else float(a[:, 2].mean())) * unit_scale
            if z is not None and np.abs(on - zm).min() > LATTICE_TOLERANCE or z is None and not (on[0] - step <= zm <= on[-1] + step):
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

def surface(points: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
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


def build_ground(points: np.ndarray, window: tuple[float, float, float, float], platform: BaseGeometry, hole: BaseGeometry,
                 floor: float, level: float, thickness: float) -> Terrain:
    """The terrain over ``window``: ``points`` (N x 3, the contour vertices in the frame of the plan, heights absolute);
    ``floor`` the height of the finished floor in them; ``level`` where the platform lies (relative to the floor)."""
    step, nx, ny = _grid(window)
    xs, ys = np.meshgrid(window[0] + np.arange(nx + 1) * step, window[1] + np.arange(ny + 1) * step)
    natural = surface(points, xs.ravel(), ys.ravel()).reshape(xs.shape) - floor
    away = shapely.distance(platform, shapely.points(xs.ravel(), ys.ravel())).reshape(xs.shape)
    z = level + np.clip(natural - level, -SLOPE_MAX * away, SLOPE_MAX * away)
    return Terrain(window[0], window[1], step, z, level, float(z.min()) - thickness, platform, hole)


# --- placing the contour lines on the plan ---

def contour_view(analysis, xy: np.ndarray, scale: float):
    """The view of the sheet the contour lines (``xy`` in metres) are drawn in: the one that holds most of their vertices."""
    best, most = None, 0
    for v in analysis.views:
        x0, y0, x1, y1 = (c * scale for c in v.bbox)
        n = int(((xy[:, 0] >= x0) & (xy[:, 0] <= x1) & (xy[:, 1] >= y0) & (xy[:, 1] <= y1)).sum())
        if n > most:
            best, most = v, n
    return best


def _same_frame(analysis, plan_view, view) -> bool:
    """Do the plan and the contour lines share one frame (the plan is the view of the contours, or lies in it)?"""
    if plan_view is view:
        return True
    by_id = {v.id: v for v in analysis.views}
    host = by_id.get(plan_view.parent)
    seen: set[int] = set()
    while host is not None and host.id not in seen:
        if host is view:
            return True
        seen.add(host.id)
        host = by_id.get(host.parent)
    return False


def place_contours(analysis, scale: float, contours: Contours) -> tuple[Placement | None, bool, int | None]:
    """(the move that lays the plan on the frame of the contour lines, whether it had to be found, the number of the view that
    holds the lines). A plan in the frame of the lines moves nowhere; a plan elsewhere in the sheet is registered by the lines
    it shares with the lot (``sitefit``): None when that fails."""
    if analysis is None or analysis.plan is None:
        return Placement(), False, None
    view = contour_view(analysis, contours.xyz[:, :2], scale)
    if view is None or _same_frame(analysis, analysis.plan, view):
        return Placement(), False, view.id if view is not None else None
    return register(analysis, scale, analysis.plan, view, {contours.layer}), True, view.id


# --- building the terrain ---

def _ground_under(points: np.ndarray, building: BaseGeometry) -> float:
    """The height of the natural ground under the building: the median of the triangulation at points all over it."""
    x0, y0, x1, y1 = building.bounds
    gx, gy = np.meshgrid(np.arange(x0, x1 + FOOTPRINT_SAMPLE, FOOTPRINT_SAMPLE), np.arange(y0, y1 + FOOTPRINT_SAMPLE, FOOTPRINT_SAMPLE))
    gx, gy = gx.ravel(), gy.ravel()
    inside = shapely.contains_xy(building, gx, gy)
    if not inside.any():
        centre = building.representative_point()
        gx, gy, inside = np.array([centre.x]), np.array([centre.y]), np.array([True])
    return float(np.median(surface(points, gx[inside], gy[inside])))


def _platform(parts: BaseGeometry, pools: BaseGeometry) -> BaseGeometry:
    """The flat ground: the building (walls, floors, columns) with a strip around it, and the pools dug flat."""
    return union([parts.buffer(PLATFORM_MARGIN, join_style="mitre"), pools.buffer(POOL_MARGIN, join_style="mitre")])


def build_terrain(doc, cfg: Config, analysis, unit_scale: float, building: BaseGeometry, parts: BaseGeometry,
                  pools: BaseGeometry, warnings: list[str]) -> Terrain | None:
    """The terrain of the drawing, or None. ``building``: the footprint of the walls (m); ``parts``: everything that stands
    on the platform (walls, floors, columns); ``pools``: the pools to be dug. Built when the user asks (``cfg.terrain``) or
    the sheet has contour lines (``MIN_LEVELS`` levels) that lie over the building (``cfg.terrain_auto``)."""
    asked = cfg.terrain
    found = read_contours(doc, cfg, unit_scale, MIN_LEVELS_ASKED if asked else MIN_LEVELS)
    if not found:
        if asked:
            warnings.append("Terreno: non ho trovato curve di livello (polilinee ognuna a una sua quota, a intervalli regolari) "
                            "in nessun layer del foglio: il terreno non e' stato costruito.")
        return None
    c = found[0]
    placement, registered, view_id = place_contours(analysis, unit_scale, c)
    if placement is None and asked:
        placement = Placement()  # the user says the contour lines are over the plan: they are, unless they are far away
    if placement is None:
        warnings.append(f"Terreno: il foglio ha {len(c.levels)} curve di livello (layer {c.layer}) ma non riesco a capire dove "
                        "sta la casa sul rilievo (nessuna linea in comune tra la pianta e il disegno delle curve): "
                        "nessun terreno. Se le curve e la pianta sono nello stesso riferimento usa --terreno.")
        return None
    xy = placement.to_plan(c.xyz[:, :2])
    points = np.column_stack([xy, c.xyz[:, 2]])
    near = MultiPoint(points[:: max(1, len(points) // 3000), :2]).convex_hull.distance(building)
    if near > HULL_SLACK:
        warnings.append(f"Terreno: le curve di livello del layer {c.layer} stanno a {near:.0f} m dalla casa: non coprono il "
                        "punto dove sta l'edificio, nessun terreno (curve di un altro lotto?).")
        return None
    window = box(*building.bounds).buffer(MARGIN).intersection(box(*MultiPoint(points[:, :2]).bounds)).bounds
    if window[2] - window[0] < 1.0 or window[3] - window[1] < 1.0:
        return None
    floor = written_floor_level(sheet_texts(doc), c.levels[0], c.levels[-1]) or (_ground_under(points, building), FLOOR_FROM_GROUND)
    level = -cfg.floor_thickness
    t = build_ground(points, window, _platform(parts, pools), pools, floor[0], level, cfg.terrain_thickness)
    t.info = {"layer": c.layer, "levels": len(c.levels), "step": c.step, "low": c.levels[0], "high": c.levels[-1],
              "floor": floor[0], "floor_source": floor[1], "level": level, "size": (window[2] - window[0], window[3] - window[1]),
              "thickness": cfg.terrain_thickness, "view": view_id, "registered": registered, "shift": placement.shift,
              "common": placement.common, "views": placement.views}
    _notes(t, warnings)
    return t


def _notes(t: Terrain, warnings: list[str]) -> None:
    """What was guessed, to be checked in the 3D preview."""
    i = t.info
    if i["registered"]:
        warnings.append(f"Terreno: le curve di livello stanno nella vista {i['view']} del foglio, la pianta in un'altra: l'ho "
                        f"messa sul rilievo facendo combaciare {i['common']:.0f} m di linee del lotto tra le viste "
                        f"{', '.join(map(str, i['views']))} (spostamento di {i['shift'][0]:.1f}, {i['shift'][1]:.1f} m). "
                        "Controlla nell'anteprima 3D che la casa sia dove deve stare sul terreno.")
    if i["floor_source"] == FLOOR_FROM_GROUND:
        warnings.append(f"Terreno: nel foglio non ho trovato la quota del pavimento finito (es. '+0.00 (297.40)' o 'QUOTA PIANO "
                        f"297.40'): ho preso quella del terreno sotto l'edificio, {i['floor']:.2f} m. Se e' un'altra "
                        "scrivila nel disegno.")
    warnings.append(f"Terreno: sotto la casa e per {PLATFORM_MARGIN:g} m intorno il suolo e' piano a {i['level']:+.2f} m (sotto il "
                    f"pavimento finito, {i['floor']:.2f} m s.l.m.); oltre, una scarpata al massimo 1:{1 / SLOPE_MAX:g} lo "
                    "raccorda al terreno delle curve di livello.")
