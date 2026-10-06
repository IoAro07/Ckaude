"""Polygon mesh with welded vertices, built by extruding 2D footprints."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import shapely
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from .geom import polygons_of

WELD = 1e-5  # metres
GRID = 1e-5  # footprints are snapped to this grid so neighbours share exact coordinates

Vec3 = tuple[float, float, float]


@dataclass
class Slab:
    z0: float
    z1: float
    geom: BaseGeometry


@dataclass
class Mesh:
    vertices: list[Vec3] = field(default_factory=list)
    normals: list[Vec3] = field(default_factory=list)
    # group name -> faces; a face is (vertex indices, normal index)
    groups: dict[str, list[tuple[tuple[int, ...], int]]] = field(default_factory=dict)
    _vkeys: dict[tuple[int, int, int], int] = field(default_factory=dict, repr=False)
    _nkeys: dict[tuple[int, int, int], int] = field(default_factory=dict, repr=False)

    def vertex(self, p: Vec3) -> int:
        key = (round(p[0] / WELD), round(p[1] / WELD), round(p[2] / WELD))
        idx = self._vkeys.get(key)
        if idx is None:
            idx = self._vkeys[key] = len(self.vertices)
            self.vertices.append((p[0], p[1], p[2]))
        return idx

    def normal(self, n: Vec3) -> int:
        key = (round(n[0] * 1e6), round(n[1] * 1e6), round(n[2] * 1e6))
        idx = self._nkeys.get(key)
        if idx is None:
            idx = self._nkeys[key] = len(self.normals)
            self.normals.append(n)
        return idx

    def add_face(self, group: str, pts: list[Vec3], normal: Vec3) -> None:
        ids = []
        for p in pts:
            i = self.vertex(p)
            if not ids or ids[-1] != i:
                ids.append(i)
        if len(ids) > 1 and ids[0] == ids[-1]:
            ids.pop()
        if len(set(ids)) < 3:
            return  # collapsed by welding
        self.groups.setdefault(group, []).append((tuple(ids), self.normal(normal)))

    def translate_xy(self, dx: float, dy: float) -> None:
        """Move the whole model in the plan (heights unchanged)."""
        self.vertices = [(x + dx, y + dy, z) for x, y, z in self.vertices]
        self._vkeys = {}  # welding keys are stale; nothing more is added after this

    @property
    def face_count(self) -> int:
        return sum(len(f) for f in self.groups.values())

    # -- extrusion ---------------------------------------------------------

    def add_extrusion(self, group: str, slabs: list[Slab], bottom: bool = True) -> None:
        """Extrude stacked slabs (contiguous in z, ascending).

        Only exposed horizontal faces are generated: where a slab's footprint
        differs from its neighbour's, the difference becomes a top/bottom face.
        """
        snapped = [_snap(s.geom) for s in slabs]
        # Side faces: an edge that stays the same in consecutive slabs becomes one tall quad
        # instead of one quad per slab, so walls far from any opening get no extra edge loops.
        running: dict[tuple, float] = {}
        for k, slab in enumerate(slabs):
            edges = {e for poly in polygons_of(snapped[k]) for e in _ring_edges(poly)}
            for edge in [e for e in running if e not in edges]:
                self._side(group, edge, running.pop(edge), slab.z0)
            for edge in edges:
                running.setdefault(edge, slab.z0)
        for edge, z0 in running.items():
            self._side(group, edge, z0, slabs[-1].z1)
        for k, slab in enumerate(slabs):
            geom = snapped[k]
            above = snapped[k + 1] if k + 1 < len(slabs) else None
            self._cap(group, geom if above is None else geom.difference(above), slab.z1, up=True)
            if k == 0:
                if bottom:
                    self._cap(group, geom, slab.z0, up=False)
            else:
                self._cap(group, geom.difference(snapped[k - 1]), slab.z0, up=False)

    def add_roof_solid(self, group: str, roof, z_base: float, thickness: float) -> None:
        """A roof as a closed solid: sloped top, flat bottom ``thickness`` under the eaves,
        vertical sides along the outline and vertical walls where neighbouring faces do not
        meet (a gable standing over a lower roof). Heights in ``roof`` are above the eaves."""
        bottom = z_base - thickness
        for tri in roof.triangles:
            top = [(x, y, z + z_base) for x, y, z in tri]
            n = _normal(top)
            if n is None:
                continue
            self.add_face(group, top, n)
            self.add_face(group, [(x, y, bottom) for x, y, _ in reversed(tri)], (0.0, 0.0, -1.0))
        for (ax, ay, az), (bx, by, bz) in roof.edges:
            length = math.hypot(bx - ax, by - ay)
            if length < 1e-9:
                continue
            self.add_face(
                group,
                [(ax, ay, bottom), (bx, by, bottom), (bx, by, bz + z_base), (ax, ay, az + z_base)],
                ((by - ay) / length, -(bx - ax) / length, 0.0),
            )
        for wall in roof.steps:
            pts = [(x, y, z + z_base) for x, y, z in wall]
            n = _normal(pts)
            if n is not None:
                self.add_face(group, pts, n)

    def _side(self, group: str, edge: tuple, z0: float, z1: float) -> None:
        (ax, ay), (bx, by) = edge
        length = math.hypot(bx - ax, by - ay)
        if length < 1e-9 or z1 - z0 < 1e-9:
            return
        self.add_face(
            group,
            [(ax, ay, z0), (bx, by, z0), (bx, by, z1), (ax, ay, z1)],
            ((by - ay) / length, -(bx - ax) / length, 0.0),  # right-hand normal: outwards
        )

    def _cap(self, group: str, geom: BaseGeometry, z: float, up: bool) -> None:
        normal = (0.0, 0.0, 1.0 if up else -1.0)
        for poly in polygons_of(geom):
            poly = orient(poly, 1.0)
            ring = list(poly.exterior.coords)[:-1]
            if not poly.interiors and len(ring) == 4 and _is_convex(ring):
                tris = [ring]  # keep rectangles as one quad
            else:
                tris = triangulate(poly)
            for tri in tris:
                pts = [(x, y, z) for x, y in tri]
                self.add_face(group, pts if up else pts[::-1], normal)


def _normal(pts: list[Vec3]) -> Vec3 | None:
    """Unit normal of a counter-clockwise triangle/polygon (None if degenerate)."""
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = pts[0], pts[1], pts[2]
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    return (nx / length, ny / length, nz / length) if length > 1e-12 else None


def _ring_edges(poly: Polygon):
    """Directed boundary edges, exterior counter-clockwise and holes clockwise."""
    poly = orient(poly, 1.0)
    for ring in (poly.exterior, *poly.interiors):
        c = [(round(x, 9), round(y, 9)) for x, y in ring.coords]
        yield from zip(c, c[1:])


def _snap(geom: BaseGeometry) -> BaseGeometry:
    if geom.is_empty:
        return geom
    # snap to the grid, then drop collinear vertices left by boolean operations
    return shapely.set_precision(geom, GRID).simplify(GRID / 10, preserve_topology=True)


def _is_convex(ring: list[tuple[float, float]]) -> bool:
    sign = 0
    n = len(ring)
    for i in range(n):
        ax, ay = ring[i]
        bx, by = ring[(i + 1) % n]
        cx, cy = ring[(i + 2) % n]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) < 1e-12:
            return False
        s = 1 if cross > 0 else -1
        if sign and s != sign:
            return False
        sign = s
    return True


def triangulate(poly: Polygon) -> list[list[tuple[float, float]]]:
    """Counter-clockwise triangles covering ``poly`` (holes respected).

    Constrained Delaunay uses every vertex as a node, so no triangle edge runs
    through another vertex (which would leave T-junctions in the mesh).
    """
    out = []
    for tri in shapely.constrained_delaunay_triangles(poly).geoms:
        (ax, ay), (bx, by), (cx, cy) = list(tri.exterior.coords)[:3]
        area2 = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
        if abs(area2) < 1e-14:
            continue
        pts = [(ax, ay), (bx, by), (cx, cy)]
        out.append(pts if area2 > 0 else pts[::-1])
    return out
