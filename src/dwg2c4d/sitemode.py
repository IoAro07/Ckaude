"""Site mode: the buildings of a sheet that draws no plan of any building, from the roofs of its site plan.

A farm, a lot with several houses: the sheet has a planimetria generale in which every building is drawn as its roof
(a hatch of tiles on each slope, the slope arrows of the falde) and, elsewhere, the elevations of some of them. There
is no floor plan, so no wall, door or window is drawn anywhere as such. What the sheet does give is

* the roof planes: one hatch per slope, none overlapping, the gaps between them the width of a ridge or a hip line;
* the way each one falls: a slope arrow (a shaft and two barbs meeting at the tip) drawn in it, else its longest edge
  on the outline of the roof is its eave;
* the footprint: the outline of the roof less the eaves (0.5 m when nothing says better).

``find_buildings`` groups the planes into buildings (planes that are close) and into bodies (planes that meet along a
ridge, a hip or a valley: one pitched roof; a plane that falls the same way as its neighbour is a lean-to, a body of its
own), and ``building_mesh`` raises each as closed volumes: the walls round the footprint (the inside is not known:
hollow, closed by the floor and the roof) and its roof, every body at its own height.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.strtree import STRtree

from .geom import polygons_of, union
from .mesh import Mesh, Slab, building_tag
from .roof import Roof

Vec3 = tuple[float, float, float]

# --- the roof planes ---
ROOF_WORDS = ("coppi", "tegol", "tett", "copertur", "roof", "falda", "falde")  # beginnings of the words a roof layer is called by
MIN_PLANE = 0.8  # m2: a smaller hatch is a skylight or a chimney, not a slope
MIN_BUILDING = 6.0  # m2 of planes: a smaller cluster is a canopy, a well cover, not a building
CLUSTER_GAP = 1.0  # m: planes this close belong to one building
CLOSE_GAP = 0.15  # m: planes are widened by this, so the white strips drawn along ridges and hips close
SIMPLIFY = 0.02  # m: the outlines of the hatches are simplified by this
MITRE_LIMIT = 2.0  # a widened corner is never cut off further than this many times the widening
COURTYARD = 12.0  # m2: a hole in the roof bigger than this is a courtyard; a smaller one is a skylight, which the slope it is in covers

# --- the slope arrows ---
ARROW_REACH = 6.0  # m: only straight pieces up to this long are looked at
ARROW_TOL = 0.03  # m: pieces that end this close share the end
ARROW_BARB = (0.1, 0.6)  # m: the length of the two barbs of the head
ARROW_SHAFT = 2.0  # the shaft is this many times as long as a barb, at least
ARROW_BARB_ALIKE = 0.3  # the barbs differ by less than this share of their length
ARROW_ANGLE = (10.0, 55.0)  # degrees: how far each barb leans from the shaft
SNAP_ANGLE = 25.0  # degrees: an arrow within this of the normal of an edge of its plane is that normal
MIN_EDGE = 0.8  # m: a plane's edge shorter than this is no eave

# --- the bodies ---
SHARED_LENGTH = 0.5  # m: two planes that touch along this much share an edge
SAME_FALL = 0.9  # cosine: planes that fall within about 25 degrees of one direction are steps of one slope, not a ridge
BISECT = 0.25  # the shared edge of two planes of a roof bisects their falls: it leans as much from one as from the other, within this

# --- the volumes ---
OVERHANG_SHARE = 0.25  # the overhang is at most this share of the narrowest side of the roof
EDGE_MATCH = 0.25  # m: a roof edge with another plane this close along it is shared with it ...
STEP_TOLERANCE = 0.2  # m: ... if the two planes are as high as one another there, within this
GROUND_TOLERANCE = 0.05  # m: eaves this close in height are one height
SMALL_BODY = 40.0  # m2: a body of this footprint or less, with no elevation to say, is one storey high; a bigger one two
LEAN_TOL = 0.6  # m: the high edge of a lean-to is this close to the eave of the roof it leans on, at most
LEAN_EDGE = 0.3  # m: the edge of a plane is its vertices this close to its lowest (or highest) point along the fall
LEAN_BELOW = 0.3  # m: a lean-to meets the wall of the building it leans on this far below the eaves of that one
LEAN_MIN = 2.2  # m: a lean-to is never lower at its eaves than this
LEAN_REACH = 1.0  # m: the high edge of a lean-to runs along the eave of the roof it leans on for this long, at least
PITCH_VIEWS = ("site", "roof", "section", "elevation")  # the views where a slope written in the sheet ("Pendenza 25 %") tells about the roofs


@dataclass
class Plane:
    """One slope of a roof: the hatch (metres) and the way it falls. The plane is 0 high along its eave and rises
    ``tan(pitch)`` for every metre of run away from it, ``run = eave - fall . p``."""

    shape: Polygon
    fall: tuple[float, float]  # unit vector: where the water runs
    how: str  # "freccia" (a slope arrow is drawn in it) | "gronda" (its longest edge on the outline of the roof)
    eave: float = 0.0  # fall . p of its lowest edge

    def run(self, x: float, y: float) -> float:
        return self.eave - (x * self.fall[0] + y * self.fall[1])


@dataclass
class Body:
    """Planes that make one pitched roof (they meet along ridges, hips, valleys): one volume, one eaves height."""

    planes: list[int]
    eave_height: float | None = None  # m above the ground floor, where its roof starts
    eave_source: str = ""  # where it came from: "prospetto" | "indicata" | "addossato" (a lean-to under the eaves of its neighbour) | "predefinita"
    storeys: int = 0
    pitch: float = 0.0  # degrees: its slope (the one of the site, or its own when the elevations measure it: see resolve_pitch)
    measured_pitch: float | None = None  # degrees, from the rise of its roof in the elevations over its half span
    level: object | None = None  # the siteelev.RoofLevel of the elevations it was measured on


@dataclass
class Building:
    id: int
    planes: list[Plane]
    bodies: list[Body]
    outline: BaseGeometry  # the roof as drawn, gaps closed
    footprint: BaseGeometry  # the outer face of the walls: the outline less the eaves
    overhang: float
    hatch_area: float  # m2 of tile hatch
    base: float = 0.0  # m: where its finished floor lies above the zero of the model (the ground round the buildings is not flat: siteground)

    @property
    def size(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.outline.bounds
        return x1 - x0, y1 - y0


# ------------------------------------------------------------------------------------------------
# reading the roofs

def roof_hatches(doc, scale: float, boxes: list[tuple[float, float, float, float]], tile_words: tuple[str, ...],
                 dist: float) -> list[Polygon]:
    """The hatches of the tiles that lie in ``boxes`` (drawing units), as polygons in metres: those whose pattern is a
    tile pattern (``tile_words``) or whose layer is called for a roof. The hole of a skylight is left in the plane."""
    from .reader import _entity_prims

    out: list[Polygon] = []
    for e in doc.modelspace().query("HATCH"):
        layer = e.dxf.layer.lower()
        pattern = e.dxf.pattern_name.upper()
        if not (any(w in pattern for w in tile_words) or any(w in layer for w in ROOF_WORDS)):
            continue
        for prim in _entity_prims(e, dist):
            shape = shapely.affinity.scale(prim.geom, scale, scale, origin=(0, 0))
            c = shape.centroid
            if any(x0 <= c.x / scale <= x1 and y0 <= c.y / scale <= y1 for x0, y0, x1, y1 in boxes):
                out.extend(Polygon(p.exterior).simplify(SIMPLIFY) for p in polygons_of(shape) if p.area >= MIN_PLANE)
    return [p for p in out if p.is_valid and not p.is_empty]


def slope_arrows(segs: np.ndarray) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """(tail, tip) of the slope arrows among ``segs`` (N x 4, metres): a shaft and two barbs of equal length that meet at
    the tip, leaning back from it by about the same angle on both sides."""
    if not len(segs):
        return []
    length = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
    keep = np.flatnonzero((length > 0) & (length <= ARROW_REACH))
    ends: dict[tuple[int, int], list[tuple[int, int]]] = {}  # rounded end -> (segment, which end)
    seen: set[tuple] = set()  # a sheet often draws its arrows twice, on top of each other
    for i in keep:
        a = (round(segs[i, 0] / ARROW_TOL), round(segs[i, 1] / ARROW_TOL))
        b = (round(segs[i, 2] / ARROW_TOL), round(segs[i, 3] / ARROW_TOL))
        if (min(a, b), max(a, b)) in seen:
            continue
        seen.add((min(a, b), max(a, b)))
        for k in (0, 2):
            ends.setdefault((round(segs[i, k] / ARROW_TOL), round(segs[i, k + 1] / ARROW_TOL)), []).append((int(i), k))
    arrows = []
    for touching in ends.values():
        if len(touching) != 3:
            continue
        vecs = []  # from the tip along each of the three pieces
        for i, k in touching:
            x0, y0, x1, y1 = segs[i]
            tx, ty = (x0, y0) if k == 0 else (x1, y1)
            ox, oy = (x1, y1) if k == 0 else (x0, y0)
            vecs.append((ox - tx, oy - ty, tx, ty))
        vecs.sort(key=lambda v: -math.hypot(v[0], v[1]))
        shaft, barb_a, barb_b = vecs
        ls, la, lb = (math.hypot(v[0], v[1]) for v in vecs)
        if not (ARROW_BARB[0] <= lb and la <= ARROW_BARB[1] and ls >= ARROW_SHAFT * la and abs(la - lb) <= ARROW_BARB_ALIKE * la):
            continue
        angles = []
        for b in (barb_a, barb_b):
            cos = (shaft[0] * b[0] + shaft[1] * b[1]) / (ls * math.hypot(b[0], b[1]))
            angles.append(math.degrees(math.acos(max(-1.0, min(1.0, cos)))))
        if not all(ARROW_ANGLE[0] <= a <= ARROW_ANGLE[1] for a in angles) or abs(angles[0] - angles[1]) > 15.0:
            continue
        tip_xy = (shaft[2], shaft[3])
        arrows.append(((tip_xy[0] + shaft[0], tip_xy[1] + shaft[1]), tip_xy))
    return arrows


def _edges(poly: Polygon) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    c = list(orient(poly, 1.0).exterior.coords)
    return list(zip(c, c[1:]))


def _outward(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    """The unit normal on the right of a -> b: outwards, for the edge of a counter-clockwise ring."""
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    return (b[1] - a[1]) / length, -(b[0] - a[0]) / length


def _snap(poly: Polygon, direction: tuple[float, float]) -> tuple[float, float]:
    """``direction`` turned onto the outward normal of the edge of ``poly`` it is closest to (an arrow is drawn by hand: the
    eave is straight), if that is within ``SNAP_ANGLE`` of it."""
    best, best_cos = direction, math.cos(math.radians(SNAP_ANGLE))
    for a, b in _edges(poly):
        if math.dist(a, b) >= MIN_EDGE:
            n = _outward(a, b)
            cos = n[0] * direction[0] + n[1] * direction[1]
            if cos >= best_cos:
                best, best_cos = n, cos
    return best


def _eave_of(shape: Polygon, fall: tuple[float, float]) -> float:
    return max(x * fall[0] + y * fall[1] for x, y in shape.exterior.coords)


def make_planes(hatches: list[Polygon], arrows: list[tuple[tuple[float, float], tuple[float, float]]]) -> tuple[list[Plane], BaseGeometry]:
    """(the planes, the outline of the roof) of the hatches of one building. Each hatch is widened by ``CLOSE_GAP`` (and kept
    within the closed outline), so the strips left along the ridges and hips close."""
    closed = union(h.buffer(CLOSE_GAP, join_style="mitre", mitre_limit=MITRE_LIMIT) for h in hatches)
    outline = closed.buffer(-CLOSE_GAP, join_style="mitre", mitre_limit=MITRE_LIMIT)
    outline = union(polygons_of(outline))
    holes = [Polygon(r) for poly in polygons_of(outline) for r in poly.interiors if Polygon(r).area < COURTYARD]  # skylights, left bare
    outline = union([outline, *holes])
    shapes: list[tuple[Polygon, Polygon]] = []  # (hatch, its plane)
    for h in hatches:
        shape = union([h.buffer(CLOSE_GAP, join_style="mitre", mitre_limit=MITRE_LIMIT).intersection(outline)])
        poly = max(polygons_of(shape), key=lambda p: p.area, default=None)
        if poly is not None:
            shapes.append((h, Polygon(poly.exterior)))
    for hole in holes:  # a skylight is part of the slope it is in: the plane that shares most of its edge takes it
        k = max(range(len(shapes)), key=lambda i: shapes[i][1].buffer(CLOSE_GAP).intersection(hole).area, default=None)
        if k is not None:
            taken = max(polygons_of(union([shapes[k][1], hole])), key=lambda p: p.area)
            shapes[k] = (shapes[k][0], Polygon(taken.exterior))
    planes: list[Plane] = []
    for h, poly in shapes:
        poly = poly.simplify(SIMPLIFY)
        inside = [(t, tip) for t, tip in arrows if h.contains(Point((t[0] + tip[0]) / 2, (t[1] + tip[1]) / 2))]
        if inside:
            tail, tip = inside[0]
            length = math.dist(tail, tip)
            fall = _snap(poly, ((tip[0] - tail[0]) / length, (tip[1] - tail[1]) / length))
            how = "freccia"
        else:
            fall, how = _eave_fall(poly, outline), "gronda"
        planes.append(Plane(poly, fall, how, _eave_of(poly, fall)))
    return planes, outline


def _eave_fall(poly: Polygon, outline: BaseGeometry) -> tuple[float, float]:
    """The fall of a plane with no arrow: away from its longest edge that lies on the outline of the roof (else its longest
    edge): that is where its eave is."""
    boundary = outline.boundary
    edges = [(a, b) for a, b in _edges(poly) if math.dist(a, b) >= MIN_EDGE]
    on_outline = [(a, b) for a, b in edges if boundary.distance(Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)) < 1e-3]
    a, b = max(on_outline or edges or _edges(poly), key=lambda e: math.dist(*e))
    return _outward(a, b)


# ------------------------------------------------------------------------------------------------
# buildings and bodies

def _shared_edge(a: Polygon, b: Polygon) -> tuple[float, tuple[float, float]] | None:
    """(length, unit direction) of the edge two planes share, or None when they do not touch along ``SHARED_LENGTH``."""
    common = a.boundary.intersection(b.buffer(CLOSE_GAP * 2))
    if common.is_empty or common.length < SHARED_LENGTH:
        return None
    pieces = [g for g in getattr(common, "geoms", [common]) if isinstance(g, LineString)]
    longest = max(pieces, key=lambda g: g.length, default=None)
    if longest is None or longest.length < SHARED_LENGTH:
        return None
    x0, y0, x1, y1 = longest.coords[0][0], longest.coords[0][1], longest.coords[-1][0], longest.coords[-1][1]
    d = math.hypot(x1 - x0, y1 - y0)
    return common.length, ((x1 - x0) / d, (y1 - y0) / d) if d > 1e-9 else (1.0, 0.0)


def same_roof(a: Plane, b: Plane, edge: tuple[float, float]) -> bool:
    """Do two planes that share an edge make one pitched roof? They do along a ridge, a hip or a valley, where the edge
    leans as much from the fall of one as from the fall of the other; not when they fall the same way (the lower one is
    a lean-to, or a step of the same slope)."""
    if a.fall[0] * b.fall[0] + a.fall[1] * b.fall[1] >= SAME_FALL:
        return False
    lean_a = abs(edge[0] * a.fall[0] + edge[1] * a.fall[1])
    lean_b = abs(edge[0] * b.fall[0] + edge[1] * b.fall[1])
    return abs(lean_a - lean_b) <= BISECT


def group_bodies(planes: list[Plane]) -> list[Body]:
    """The planes joined into bodies by the ridges, hips and valleys they share."""
    parent = list(range(len(planes)))

    def find(k: int) -> int:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    tree = STRtree([p.shape for p in planes])
    for i, a in enumerate(planes):
        for j in tree.query(a.shape.buffer(CLOSE_GAP * 2)):
            j = int(j)
            if j <= i:
                continue
            shared = _shared_edge(a.shape, planes[j].shape)
            if shared is not None and same_roof(a, planes[j], shared[1]):
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(planes)):
        groups.setdefault(find(i), []).append(i)
    return [Body(sorted(members)) for members in groups.values()]


def clusters(hatches: list[Polygon]) -> list[list[Polygon]]:
    """The hatches grouped into buildings: those closer than ``CLUSTER_GAP`` to one another."""
    if not hatches:
        return []
    grown = union(h.buffer(CLUSTER_GAP / 2.0) for h in hatches)
    return [[h for h in hatches if cell.intersects(h)] for cell in polygons_of(grown)]


def overhang_for(outline: BaseGeometry, wanted: float) -> float:
    """The eaves the footprint is cut back by: ``wanted``, or less when the roof is narrow (a lean-to 2 m deep)."""
    x0, y0, x1, y1 = outline.bounds
    narrowest = min(math.sqrt(p.area) for p in polygons_of(outline))
    return min(wanted, OVERHANG_SHARE * narrowest, 0.5 * min(x1 - x0, y1 - y0))


def find_buildings(hatches: list[Polygon], segs: np.ndarray, overhang: float) -> list[Building]:
    """The buildings the roof hatches (metres) draw, biggest first. ``segs``: the short straight pieces of the sheet
    (N x 4, metres), where the slope arrows are looked for."""
    arrows = slope_arrows(segs)
    found: list[Building] = []
    for group in clusters(hatches):
        area = sum(h.area for h in group)
        if area < MIN_BUILDING:
            continue
        planes, outline = make_planes(group, arrows)
        if not planes or outline.is_empty:
            continue
        cut = overhang_for(outline, overhang)
        footprint = union(polygons_of(outline.buffer(-cut, join_style="mitre", mitre_limit=MITRE_LIMIT)))
        found.append(Building(0, planes, group_bodies(planes), outline, footprint, cut, area))
    found.sort(key=lambda b: -b.hatch_area)
    for n, b in enumerate(found, 1):
        b.id = n
    return found


# ------------------------------------------------------------------------------------------------
# the volumes

def body_outline(building: Building, body: Body) -> BaseGeometry:
    """The roof of a body as drawn: its planes."""
    return union(building.planes[i].shape for i in body.planes)


def body_shape(building: Building, body: Body) -> BaseGeometry:
    """What a body covers, the walls' outer face: its planes, cut back to the footprint of the building."""
    return body_outline(building, body).intersection(building.footprint)


def half_span(building: Building, body: Body) -> float:
    """How far the roof of a body runs from its eaves to its ridge: the middle one of the runs of its planes (a hip roof has big
    and small planes; the middle one is a long side's)."""
    runs = sorted(max(building.planes[i].run(x, y) for x, y in building.planes[i].shape.exterior.coords) for i in body.planes)
    return runs[len(runs) // 2]


def storeys_default(area: float) -> int:
    """How many storeys a body whose roof sheet draws no elevation gets: a small one (a lean-to, a shed) one, a house two."""
    return 1 if area < SMALL_BODY else 2


def eave_groups(building: Building) -> list[tuple[float, BaseGeometry]]:
    """(eaves height, footprint) of the walls of a building: its bodies, those at one height as one volume."""
    groups: list[tuple[float, list[BaseGeometry]]] = []
    for body in building.bodies:
        height = body.eave_height or 0.0
        for h, shapes in groups:
            if abs(h - height) <= GROUND_TOLERANCE:
                shapes.append(body_shape(building, body))
                break
        else:
            groups.append((height, [body_shape(building, body)]))
    return [(h, union(shapes)) for h, shapes in sorted(groups)]


def wall_ring(shape: BaseGeometry, thickness: float) -> BaseGeometry:
    """The walls of a volume: its outline ``thickness`` thick. A part thinner than two walls is solid."""
    return shape.difference(shape.buffer(-thickness, join_style="mitre", mitre_limit=MITRE_LIMIT))


def _tan(body: Body) -> float:
    return math.tan(math.radians(body.pitch))


def _closed_by_neighbour(building: Building, tans: dict[int, float], heights: dict[int, float],
                         a: tuple[float, float], b: tuple[float, float], own: int) -> bool:
    """Does another plane run along the edge a-b of plane ``own`` at the same height, so that nothing is to be closed there?
    (``tans``: the slope of every plane; ``heights``: where the eaves of its body are.)"""
    edge = LineString([a, b])
    plane = building.planes[own]
    for j, other in enumerate(building.planes):
        if j == own or not other.shape.buffer(EDGE_MATCH).covers(edge):
            continue
        m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        if all(abs((heights[own] + tans[own] * max(plane.run(*p), 0.0)) - (heights[j] + tans[j] * max(other.run(*p), 0.0)))
               <= STEP_TOLERANCE for p in (a, m, b)):
            return True
    return False


def body_roof(building: Building, body: Body, source: str) -> Roof:
    """The roof of a body as roof.Roof: heights above its eaves; the sides of the solid along every edge no plane of the
    same height closes (the eaves, the gables, a lean-to against a higher wall). ``source``: where the pitch comes from."""
    heights = {i: (b.eave_height or 0.0) for b in building.bodies for i in b.planes}
    tans = {i: _tan(b) for b in building.bodies for i in b.planes}
    triangles, edges = [], []
    for i in body.planes:
        plane, tan = building.planes[i], tans[i]
        for tri in shapely.constrained_delaunay_triangles(plane.shape).geoms:
            p = [(x, y, tan * max(plane.run(x, y), 0.0)) for x, y in list(tri.exterior.coords)[:3]]
            nz = (p[1][0] - p[0][0]) * (p[2][1] - p[0][1]) - (p[2][0] - p[0][0]) * (p[1][1] - p[0][1])
            if abs(nz) > 1e-12:
                triangles.append((p[0], p[1], p[2]) if nz > 0 else (p[0], p[2], p[1]))
        for a, b in _edges(plane.shape):
            if math.dist(a, b) > 1e-6 and not _closed_by_neighbour(building, tans, heights, a, b, i):
                edges.append(((a[0], a[1], tan * max(plane.run(*a), 0.0)), (b[0], b[1], tan * max(plane.run(*b), 0.0))))
    top = max((p[2] for t in triangles for p in t), default=0.0)
    return Roof(triangles, edges, [], pitch_deg=body.pitch, pitch_range=(body.pitch, body.pitch), pitch_source=source, ridge_height=top,
                faces=len(body.planes), offset=(0.0, 0.0), ridges_from_elevation=0)


def building_mesh(building: Building, pitch_source: str, cfg, openings: list | None = None) -> Mesh:
    """The volumes of one building: the floor slab, the walls of every eaves height (a ring ``site_wall`` thick round each
    volume: hollow inside, closed by the floor and the roof) with its openings (``openings``: its own) cut in them and their
    frames and glass in, and the roof of every body at its own eaves."""
    from .fixtures import add_fixtures
    from .model import wall_slabs

    mesh = Mesh()
    if cfg.floor_thickness > 0:
        mesh.add_extrusion("Pavimento", [Slab(-cfg.floor_thickness, 0.0, building.footprint)])
    cutting = [o for o in openings or [] if o.keep]
    for height, shape in eave_groups(building):
        ring = wall_ring(shape, cfg.site_wall)  # (only the openings in this ring: one of a higher volume would lift it to its top)
        mesh.add_extrusion("Muri", wall_slabs(ring, [o for o in cutting if o.cut.intersects(ring) and o.z0 < height], height), bottom=True)
    if cfg.fixtures == "detailed":
        add_fixtures(mesh, cutting, cfg)
    else:
        for o in cutting:
            if o.glass is not None:
                group = f"Vetri_{o.id}" if cfg.fixtures_per_opening and o.id else "Vetri"
                mesh.add_extrusion(group, [Slab(o.z0, o.z1, o.glass)], bottom=True)
    for body in building.bodies:
        mesh.add_roof_solid("Tetto", body_roof(building, body, pitch_source), z_base=body.eave_height or 0.0, thickness=cfg.roof_thickness)
    return mesh


# ------------------------------------------------------------------------------------------------
# the site

@dataclass
class Site:
    """The buildings of a sheet, their heights and the model of them all."""

    buildings: list[Building]
    views: list[int]  # the views of the sheet the roofs were read in
    pitch: float  # degrees
    pitch_source: str  # "indicata" | "scritta" | "prospetto" | "predefinita"
    mesh: Mesh = field(default_factory=Mesh)
    plan: object | None = None  # a model.Plan of all the walls and openings, for the check picture
    openings: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # what the report says: every guess is one of them
    matched: list = field(default_factory=list)  # [(building, body, siteelev.RoofLevel, the bands that fit)]: the roofs the elevations show
    roofs: BaseGeometry = field(default_factory=Polygon)  # every roof hatch of the site plan, those of no building too: none of them is ground
    around: object | None = None  # the siteground.Around: garden, terrain, low walls

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return union(b.outline for b in self.buildings).bounds


def site_views(analysis) -> list:
    """The views the roofs of the buildings are drawn in: the site plans of the sheet, else its roof plans."""
    sites = [v for v in analysis.views if v.kind == "site"]
    return sites or [v for v in analysis.views if v.kind == "roof"]


def read_buildings(doc, cfg, analysis) -> tuple[list[Building], list, BaseGeometry]:
    """(the buildings, the views they were read in, every roof hatch there): the buildings the tiles of the site plan draw.
    Empty when it draws none."""
    from .autodetect import TILE_PATTERNS

    scale = analysis.unit.scale
    views = site_views(analysis)
    hatches = roof_hatches(doc, scale, [v.bbox for v in views], TILE_PATTERNS, cfg.arc_tolerance / scale)
    return find_buildings(hatches, analysis.soup.seg * scale, cfg.site_overhang), views, union(hatches)


def resolve_pitch(buildings: list[Building], cfg, analysis) -> tuple[float, str]:
    """(the pitch of the site, where it comes from), and the pitch of every body set. The slope the user gave, else the one
    written in the sheet, both for every body; else what the elevations measure (each body that has a measure keeps its own,
    the others the middle one); else the default."""
    from .roofview import pitch_from_texts

    bodies = [body for b in buildings for body in b.bodies]
    boxes = [v for v in analysis.views if v.kind in PITCH_VIEWS]
    written = pitch_from_texts([t["text"] for t in analysis.soup.texts if any(v.contains(t["x"], t["y"]) for v in boxes)])
    if cfg.roof_pitch is not None:
        pitch, source = cfg.roof_pitch, "indicata"
    elif written is not None:
        pitch, source = written[0], "scritta"
    elif any(body.measured_pitch is not None for body in bodies):
        measured = sorted(body.measured_pitch for body in bodies if body.measured_pitch is not None)
        pitch, source = measured[len(measured) // 2], "prospetto"
    else:
        pitch, source = cfg.roof_default_pitch, "predefinita"
    for body in bodies:
        body.pitch = body.measured_pitch if source == "prospetto" and body.measured_pitch is not None else pitch
    return pitch, source


def default_heights(building: Building, cfg) -> None:
    """The eaves height of every body: the one the user gave (--altezza-muri) for all of them; else the one the elevations
    measured; else ``storey_height`` for every storey ``storeys_default`` says."""
    for body in building.bodies:
        if not cfg.wall_height_auto:
            body.eave_height, body.eave_source, body.storeys = cfg.wall_height, "indicata", 0
        elif body.eave_height is None:
            body.storeys = storeys_default(body_shape(building, body).area)
            body.eave_height, body.eave_source = body.storeys * cfg.storey_height, "predefinita"
        else:
            body.storeys = max(1, round(body.eave_height / cfg.storey_height))
    if cfg.wall_height_auto:
        lean_to_heights(building, cfg)


def _edge(plane: Plane, high: bool) -> LineString | None:
    """The lowest (``high`` False: the eave) or highest edge of a plane along its fall: the line through its vertices that lie within
    ``LEAN_EDGE`` of that extreme; None when they are not two."""
    coords = list(plane.shape.exterior.coords)[:-1]
    runs = [plane.run(x, y) for x, y in coords]
    extreme = max(runs) if high else min(runs)
    pts = [c for c, r in zip(coords, runs) if abs(r - extreme) <= LEAN_EDGE]
    return LineString(pts) if len(pts) >= 2 else None


def leans_on(building: Building, body: Body, host: Body) -> float | None:
    """The rise of the roof of ``body`` (m of run) if it is a lean-to against the eaves of ``host``: a plane that falls the way a plane
    of the host falls and whose high edge runs along that plane's eave (``LEAN_TOL`` apart, ``LEAN_REACH`` long). None if it is not."""
    for i in body.planes:
        q = building.planes[i]
        high = _edge(q, True)
        if high is None:
            continue
        for j in host.planes:
            p = building.planes[j]
            low = _edge(p, False)
            if low is None or q.fall[0] * p.fall[0] + q.fall[1] * p.fall[1] < SAME_FALL:
                continue
            if high.buffer(LEAN_TOL).intersection(low).length >= LEAN_REACH:
                return max(q.run(x, y) for x, y in q.shape.exterior.coords)
    return None


def lean_to_heights(building: Building, cfg) -> None:
    """The eaves of the bodies no elevation measures that are lean-tos: a roof of one plane (or more) leaning on the eaves of another
    body meets its wall ``LEAN_BELOW`` under those eaves, so its own eaves are lower by that and by the rise of its roof; never lower
    than ``LEAN_MIN``. Without this a lean-to would get the height of a storey, and its roof would cut the roof it leans on."""
    for _ in building.bodies:  # a lean-to may lean on another one: until nothing changes
        changed = False
        for body in building.bodies:
            if body.eave_source != "predefinita":
                continue
            for host in building.bodies:
                run = leans_on(building, body, host) if host is not body else None
                if run is None or host.eave_height is None:
                    continue
                height = max(host.eave_height - LEAN_BELOW - run * _tan(body), LEAN_MIN)
                if abs(height - body.eave_height) > 1e-9:
                    body.eave_height, body.eave_source, body.storeys = height, "addossato", 1
                    changed = True
                break
        if not changed:
            break


def read_levels(doc, cfg, analysis, buildings: list[Building], warnings: list[str]) -> tuple[list, list]:
    """(what the elevations of the sheet say of the buildings, the elevations): the bodies the roofs of the elevations match
    have their eaves height and their pitch (siteelev). An elevation that cannot be read is never the reason the conversion
    fails. [(building, body, level, bands)], [ElevationRead]."""
    from .siteelev import match_levels, read_elevations, roof_bands, roof_levels

    try:
        reads = read_elevations(doc, cfg, analysis)
        levels = roof_levels(roof_bands(analysis, reads))
        return (match_levels(buildings, levels) if levels else []), reads
    except Exception as exc:
        warnings.append(f"Altezze dai prospetti non lette ({type(exc).__name__}: {exc}): le gronde sono presunte.")
        return [], []


def read_openings(cfg, analysis, site: Site, reads: list) -> list[str]:
    """The doors and windows of the elevations, put on the buildings they show (siteelev): ``site.openings``. They come with
    a note: the elevations have no plan to be matched to. The notes."""
    from .model import facing_sign
    from .openings import assign_ids
    from .siteelev import find_facades, place_openings

    if not reads or not site.matched:
        return []
    titles = {v.id: (v.titles[0] if v.titles else f"vista {v.id}") for v in analysis.views}
    try:
        facades, notes = find_facades(site.matched)
        openings, more = place_openings(facades, reads, cfg, titles)
    except Exception as exc:
        return [f"Aperture dai prospetti non messe ({type(exc).__name__}: {exc}): i volumi sono completi, senza porte e finestre."]
    for b in site.buildings:
        mine = [o for o in openings if o.building == b.id]
        assign_ids(mine)
        for o in mine:
            o.face_sign = facing_sign(o, b.footprint)
    site.openings.extend(openings)
    return notes + more


def build_site(doc, cfg, analysis, warnings: list[str]) -> Site | None:
    """The model of the buildings the roofs of the site plan draw, or None when it draws none. ``warnings`` gets
    every reading that is a guess."""
    from .model import Plan
    from .siteground import add_around, around_site

    buildings, views, roofs = read_buildings(doc, cfg, analysis)
    if not buildings:
        return None
    matched, reads = read_levels(doc, cfg, analysis, buildings, warnings)
    pitch, source = resolve_pitch(buildings, cfg, analysis)
    site = Site(buildings, [v.id for v in views], pitch, source, matched=matched, roofs=roofs)
    for b in buildings:
        default_heights(b, cfg)
    opening_notes = read_openings(cfg, analysis, site, reads)
    around_notes: list[str] = []
    site.around = around_site(doc, cfg, analysis, site, around_notes)  # (sets the floor level of every building)
    for b in buildings:
        mesh = building_mesh(b, source, cfg, [o for o in site.openings if o.building == b.id])
        site.mesh.add_mesh(mesh, shift=(0.0, 0.0, b.base), rename=lambda name, b=b: f"{building_tag(b.id)}_{name}")
    add_around(site.mesh, site.around, cfg, around_notes)
    walls = union(wall_ring(shape, cfg.site_wall) for b in buildings for _, shape in eave_groups(b))
    site.plan = Plan(walls=walls, columns=Polygon(), openings=site.openings, merge_tolerance=cfg.merge_tolerance)
    site.notes.extend(site_notes(site, cfg, analysis) + opening_notes + around_notes)
    warnings.extend(site.notes)
    return site


def site_notes(site: Site, cfg, analysis) -> list[str]:
    """What the report says of the site: how it was read, and every guess, so that it can be checked."""
    titles = {v.id: (v.titles[0] if v.titles else f"vista {v.id}") for v in analysis.views}
    n = len(site.buildings)
    where = ", ".join(str(v) for v in site.views)
    notes = [f"Modalita' sito: il foglio non ha la pianta di nessun edificio ma la planimetria generale (vista {where}) disegna "
             f"i tetti di {n} edific{'io' if n == 1 else 'i'} ({', '.join(f'{b.hatch_area:.0f}' for b in site.buildings)} m2 di campiture "
             "di coppi): li ho costruiti come volumi chiusi con il loro tetto. L'interno non si conosce: i muri sono vuoti dentro, "
             f"spessi {cfg.site_wall * 100:.0f} cm, chiusi dal pavimento e dal tetto."]
    for b in site.buildings:
        w, h = b.size
        notes.append(f"Edificio {b.id}: tetto {w:.1f} x {h:.1f} m, {len(b.planes)} falde in {len(b.bodies)} corp{'o' if len(b.bodies) == 1 else 'i'}; "
                     f"sporto {b.overhang:.2f} m (presunto: i muri sono il contorno del tetto meno lo sporto).")
    for b, body, level, bands in site.matched:
        views = ", ".join(titles[v] for v in sorted({x.view for x in bands}))
        notes.append(f"Edificio {b.id}, corpo {b.bodies.index(body) + 1}: gronda a {level.eave:.2f} m e colmo a {level.ridge:.2f} m "
                     f"misurati sui prospetti ({views}), dove il tetto e' lungo {', '.join(f'{x.length:.1f}' for x in bands)} m come i "
                     "lati del corpo; controlla che sia davvero questo edificio."
                     + ("" if body.measured_pitch is None else f" Pendenza {body.measured_pitch:.0f} gradi dal rialzo del tetto."))
    guessed = [f"edificio {b.id}" + (f" (corpo {', '.join(str(k + 1) for k, body in enumerate(b.bodies) if body.eave_source == 'predefinita')})"
                                    if len(b.bodies) > 1 else "")
               for b in site.buildings if any(body.eave_source == "predefinita" for body in b.bodies)]
    if guessed:
        notes.append(f"Altezze di gronda presunte per {', '.join(guessed)}: nessun prospetto le misura; "
                     f"{cfg.storey_height:g} m a piano, un piano per i corpi piccoli (fino a {SMALL_BODY:.0f} m2), due per gli altri. "
                     "Per darle tu usa --altezza-muri.")
    leaning = [(b, k, body) for b in site.buildings for k, body in enumerate(b.bodies) if body.eave_source == "addossato"]
    if leaning:
        notes.append("Tetti addossati: " + "; ".join(f"edificio {b.id}, corpo {k + 1}: gronda a {body.eave_height:.2f} m" for b, k, body in leaning)
                     + f". Sono tetti a una falda appoggiati alla gronda di un altro corpo: li ho messi {LEAN_BELOW:g} m sotto quella gronda (meno il "
                     "rialzo del loro tetto) invece di dare loro l'altezza di un piano. Controlla nell'anteprima 3D.")
    if not cfg.wall_height_auto:
        notes.append(f"Altezza di gronda: {cfg.wall_height:g} m per tutti i corpi, quella che hai indicato (--altezza-muri).")
    how = {"predefinita": f"nessuna pendenza scritta ne' ricavabile dai prospetti: uso {site.pitch:.0f} gradi (--pendenza per cambiarla)",
           "scritta": f"pendenza {site.pitch:.0f} gradi, quella scritta nel foglio",
           "prospetto": f"pendenza dedotta dai prospetti: {site.pitch:.0f} gradi (il rialzo del tetto sulla meta' della sua larghezza)",
           "indicata": ""}
    if how[site.pitch_source]:
        notes.append("Tetto: " + how[site.pitch_source] + ".")
    arrowless = sum(1 for b in site.buildings for p in b.planes if p.how == "gronda")
    if arrowless:
        notes.append(f"Tetto: {arrowless} falde senza freccia di pendenza: la gronda e' il loro lato piu' lungo sul bordo del tetto; "
                     "controlla nell'anteprima 3D che scendano dal lato giusto.")
    return notes
