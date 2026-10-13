"""The mesh of the terrain, and the flat things of the drawing laid on it.

The terrain is a closed slab: the top is the grid of ``terrain.Terrain`` (two triangles per cell, cut along the diagonal
that bends the surface the least), the sides go down to the underside and the bottom is flat. Because the top is made of
planes, a shape drawn flat (a lawn, a paving, a kerb) can be laid on it exactly: it is cut along the edges of the
triangles, each piece takes the plane of its triangle, so it follows the ground with no new height to invent and no gap
between it and the ground. Such a shape is a closed slab of its own: its top is the height the flat model gives it
(``top``) above the platform and follows the ground elsewhere, its underside is ``EMBED`` below the ground.
"""

from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.strtree import STRtree

from .geom import polygons_of
from .mesh import Mesh, triangulate
from .terrain import Terrain

EMBED = 0.05  # m: a shape laid on the ground reaches this far below it (no gap shows between the two)
ON_EDGE = 1e-6  # m: an edge of a piece lies on the outline of the shape if its middle is this close to it

Point3 = tuple[float, float, float]


def _triangles(t: Terrain) -> tuple[STRtree, np.ndarray]:
    """The triangles of the top as polygons in a tree, and their planes (n, 3): z = a x + b y + c."""
    cached = t.__dict__.get("_triangles")
    if cached is not None:
        return cached
    ny, nx = t.shape
    xs = t.x0 + np.arange(nx + 1) * t.step
    ys = t.y0 + np.arange(ny + 1) * t.step
    j, i = np.mgrid[0:ny, 0:nx]
    a = j * (nx + 1) + i  # the corners of each cell: a lower left, b lower right, c upper right, d upper left
    b, d = a + 1, a + nx + 1
    c = d + 1
    first = t.split_along_first_diagonal()[..., None]
    both = np.stack([np.where(first, np.stack([a, b, c], -1), np.stack([a, b, d], -1)),
                     np.where(first, np.stack([a, c, d], -1), np.stack([b, c, d], -1))], axis=2)  # (ny, nx, 2, 3) counter-clockwise
    vertex = both.reshape(-1, 3)
    x, y = np.tile(xs, ny + 1)[vertex], np.repeat(ys, nx + 1)[vertex]
    z = t.z.ravel()[vertex]
    det = (x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0]) - (x[:, 2] - x[:, 0]) * (y[:, 1] - y[:, 0])
    pa = ((z[:, 1] - z[:, 0]) * (y[:, 2] - y[:, 0]) - (z[:, 2] - z[:, 0]) * (y[:, 1] - y[:, 0])) / det
    pb = ((x[:, 1] - x[:, 0]) * (z[:, 2] - z[:, 0]) - (x[:, 2] - x[:, 0]) * (z[:, 1] - z[:, 0])) / det
    plane = np.stack([pa, pb, z[:, 0] - pa * x[:, 0] - pb * y[:, 0]], axis=1)
    xy = np.stack([x, y], axis=-1)
    cached = t.__dict__["_triangles"] = (STRtree(shapely.polygons(np.concatenate([xy, xy[:, :1]], axis=1))), plane)
    return cached


def _clipped(region: BaseGeometry, bounds: tuple[float, float, float, float]) -> BaseGeometry:
    """``region`` cut to a rectangle. The fast cut of GEOS gives up on some outlines (a ring of three points comes out of it):
    the plain intersection with the rectangle does not."""
    try:
        return shapely.clip_by_rect(region, *bounds)
    except shapely.errors.GEOSException:
        return shapely.make_valid(region).intersection(box(*bounds))


def _pieces(t: Terrain, region: BaseGeometry | None, keep: str) -> list[tuple[Polygon, np.ndarray]]:
    """The parts of the top as (polygon, plane): all of it (``region`` None), what lies in ``region`` (``keep`` "in") or
    outside it ("out"). A triangle wholly in or out is itself, the ones the outline of ``region`` crosses are cut."""
    tree, plane = _triangles(t)
    shapes = tree.geometries
    if region is None or region.is_empty:
        return [(shapes[k], plane[k]) for k in range(len(shapes))] if keep == "out" else []
    shapely.prepare(region)
    hit = tree.query(region, predicate="intersects")
    covered = shapely.covers(region, shapes[hit])
    cut = hit[~covered]
    whole = hit[covered] if keep == "in" else np.setdiff1d(np.arange(len(shapes)), hit)
    out = [(shapes[k], plane[k]) for k in whole]
    for k in cut:
        near = _clipped(region, shapes[k].bounds)
        part = shapes[k].intersection(near) if keep == "in" else shapes[k].difference(near)
        out.extend((p, plane[k]) for p in polygons_of(part))
    return out


def _triangles_of(poly: Polygon) -> list[list[tuple[float, float]]]:
    """Counter-clockwise triangles that cover ``poly`` (a triangle is itself)."""
    ring = list(orient(poly, 1.0).exterior.coords)[:-1]
    return [ring] if len(ring) == 3 and not poly.interiors else triangulate(poly)


def _face(mesh: Mesh, group: str, pts: list[Point3]) -> None:
    """A face with the normal its (counter-clockwise) points give; nothing if they are collinear."""
    a, b, c = (np.asarray(p, float) for p in pts[:3])
    n = np.cross(b - a, c - a)
    length = float(np.linalg.norm(n))
    if length > 1e-12:
        mesh.add_face(group, pts, tuple(n / length))


def _outward(a, b) -> Point3:
    """The horizontal normal pointing out of a boundary edge a -> b walked counter-clockwise."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    return dy / length, -dx / length, 0.0


def add_terrain(mesh: Mesh, t: Terrain, group: str = "Terreno") -> None:
    """The slab: the top (without the ``t.hole`` a pool is dug in), the four sides down to ``t.bottom`` and a bottom that
    shares every vertex of the edge (fanned from the middle): a closed solid."""
    for poly, (pa, pb, pc) in _pieces(t, t.hole, "out"):
        for tri in _triangles_of(poly):
            _face(mesh, group, [(x, y, pa * x + pb * y + pc) for x, y in tri])
    ny, nx = t.shape
    xs = t.x0 + np.arange(nx + 1) * t.step
    ys = t.y0 + np.arange(ny + 1) * t.step

    def p(i: int, j: int) -> Point3:
        return float(xs[i]), float(ys[j]), float(t.z[j, i])

    ring = [(i, 0) for i in range(nx)] + [(nx, j) for j in range(ny)] + [(i, ny) for i in range(nx, 0, -1)] \
        + [(0, j) for j in range(ny, 0, -1)]  # the edge, counter-clockwise from above
    zb = t.bottom
    middle = (float(xs[nx // 2]), float(ys[ny // 2]), zb)
    for k, (i, j) in enumerate(ring):
        a, b = p(i, j), p(*ring[(k + 1) % len(ring)])
        mesh.add_face(group, [(a[0], a[1], zb), (b[0], b[1], zb), b, a], _outward(a, b))
        mesh.add_face(group, [middle, (b[0], b[1], zb), (a[0], a[1], zb)], (0.0, 0.0, -1.0))


def add_draped(mesh: Mesh, t: Terrain, group: str, geom: BaseGeometry, top: float, embed: float = EMBED) -> None:
    """``geom`` (a flat shape of the plan) laid on the ground as a closed slab: its top is ``top`` above the platform where
    the ground is at the platform (``top`` is the height the flat model gives it) and follows the ground elsewhere; its
    underside is ``embed`` below the ground."""
    boundary = geom.boundary
    rise = top - t.level
    for piece, (pa, pb, pc) in _pieces(t, geom, "in"):
        for tri in _triangles_of(piece):
            _face(mesh, group, [(x, y, pa * x + pb * y + pc + rise) for x, y in tri])
            _face(mesh, group, [(x, y, pa * x + pb * y + pc - embed) for x, y in tri[::-1]])
        ring = orient(piece, 1.0)
        for line in (ring.exterior, *ring.interiors):
            pts = list(line.coords)
            mids = shapely.points([((u[0] + v[0]) / 2.0, (u[1] + v[1]) / 2.0) for u, v in zip(pts, pts[1:])])
            for (u, v), dist in zip(zip(pts, pts[1:]), shapely.distance(boundary, mids)):
                if dist <= ON_EDGE and math.hypot(v[0] - u[0], v[1] - u[1]) > 1e-9:  # an edge of the shape, not of a triangle
                    zu, zv = pa * u[0] + pb * u[1] + pc, pa * v[0] + pb * v[1] + pc
                    mesh.add_face(group, [(u[0], u[1], zu - embed), (v[0], v[1], zv - embed), (v[0], v[1], zv + rise),
                                          (u[0], u[1], zu + rise)], _outward(u, v))
