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
        for k, slab in enumerate(slabs):
            geom = snapped[k]
            for poly in polygons_of(geom):
                self._sides(group, poly, slab.z0, slab.z1)
            above = snapped[k + 1] if k + 1 < len(slabs) else None
            self._cap(group, geom if above is None else geom.difference(above), slab.z1, up=True)
            if k == 0:
                if bottom:
                    self._cap(group, geom, slab.z0, up=False)
            else:
                self._cap(group, geom.difference(snapped[k - 1]), slab.z0, up=False)

    def _sides(self, group: str, poly: Polygon, z0: float, z1: float) -> None:
        poly = orient(poly, 1.0)  # exterior CCW, holes CW: the right-hand normal points outwards
        for ring in (poly.exterior, *poly.interiors):
            c = list(ring.coords)
            for (ax, ay), (bx, by) in zip(c, c[1:]):
                dx, dy = bx - ax, by - ay
                length = math.hypot(dx, dy)
                if length < 1e-9:
                    continue
                self.add_face(
                    group,
                    [(ax, ay, z0), (bx, by, z0), (bx, by, z1), (ax, ay, z1)],
                    (dy / length, -dx / length, 0.0),
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


def _snap(geom: BaseGeometry) -> BaseGeometry:
    if geom.is_empty:
        return geom
    return shapely.set_precision(geom, GRID)


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
