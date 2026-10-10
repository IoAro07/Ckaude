"""Passages without a symbol: two free wall ends facing each other across a gap.

A free end is a short edge of the wall outline joining two long parallel edges (the end face of a
partition). Two such ends that look at each other, line up and leave a gap between 0.5 m and
``cfg.passage_max`` with nothing in it (no wall, no door/window) are the sides of a doorway drawn
without any door symbol. It becomes an opening of kind "passage" (lining and casing, a lintel
above the door height), listed in the table as "vano" where it can be dropped (MODIFICA_tieni = no).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from .config import Config
from .geom import oriented_rect, polygons_of, union
from .openings import Opening
from .model import building_footprint

MIN_GAP = 0.5  # m
MIN_END = 0.04  # m: shorter edges are noise
LONG_SIDE = 0.25  # m: the two edges next to an end must be at least this long
LINE_UP = 0.6  # the ends must overlap at least this fraction of the shorter one
SAME_THICKNESS = 0.35  # the two ends' lengths differ by less than this fraction
OUTSIDE_FREE = 12.0  # m: the outside of a building has no wall of it this far across (a doorway between two rooms, one of which the walls do not close, is no window)


@dataclass
class End:
    a: np.ndarray
    b: np.ndarray
    out: np.ndarray  # unit normal pointing away from the wall body

    @property
    def length(self) -> float:
        return float(np.linalg.norm(self.b - self.a))

    @property
    def mid(self) -> np.ndarray:
        return (self.a + self.b) / 2.0


def wall_ends(walls: BaseGeometry, max_thickness: float) -> list[End]:
    ends: list[End] = []
    for poly in polygons_of(walls):
        for ring, outer in [(poly.exterior, True)] + [(r, False) for r in poly.interiors]:
            pts = np.asarray(ring.coords)[:-1]
            n = len(pts)
            # orientation: with a counter-clockwise exterior the body is on the left of every edge
            area = 0.5 * float(np.sum(pts[:, 0] * np.roll(pts[:, 1], -1) - np.roll(pts[:, 0], -1) * pts[:, 1]))
            sign = 1.0 if (area > 0) == outer else -1.0
            for i in range(n):
                p0, p1 = pts[i], pts[(i + 1) % n]
                e = p1 - p0
                length = float(np.hypot(*e))
                if not MIN_END <= length <= max_thickness:
                    continue
                prev_e, next_e = p0 - pts[i - 1], pts[(i + 2) % n] - p1
                lp, ln = float(np.hypot(*prev_e)), float(np.hypot(*next_e))
                if lp < LONG_SIDE or ln < LONG_SIDE:
                    continue
                # both corners are right angles in the same sense: a free end, not a step in the wall
                d = e / length
                if abs(float(np.dot(prev_e / lp, d))) > 0.05 or abs(float(np.dot(next_e / ln, d))) > 0.05:
                    continue
                if float(np.dot(prev_e, next_e)) >= 0:
                    continue  # the two sides run the same way: a step, they must run opposite ways
                # outward normal: the body lies to the left (counter-clockwise) of the edge
                out = np.array([e[1], -e[0]]) / length * sign
                ends.append(End(p0, p1, out))
    return ends


def find_passages(walls: BaseGeometry, cfg: Config, openings: list[Opening]) -> list[Opening]:
    """Passages between facing free ends. ``walls`` already contain the openings' fills."""
    if walls.is_empty:
        return []
    ends = wall_ends(walls, cfg.max_wall_thickness)
    taken = union([o.fill.buffer(0.05) for o in openings]) if openings else Polygon()
    found: list[Opening] = []
    used: set[int] = set()
    candidates = []
    for i, a in enumerate(ends):
        for j in range(i + 1, len(ends)):
            b = ends[j]
            if float(np.dot(a.out, b.out)) > -0.99:
                continue  # must look at each other
            u = a.out  # from a towards b
            gap = float(np.dot(b.mid - a.mid, u))
            if not MIN_GAP <= gap <= cfg.passage_max:
                continue
            t = np.array([-u[1], u[0]])  # along the ends
            a0, a1 = sorted(float(np.dot(p, t)) for p in (a.a, a.b))
            b0, b1 = sorted(float(np.dot(p, t)) for p in (b.a, b.b))
            overlap = min(a1, b1) - max(a0, b0)
            if overlap < LINE_UP * min(a1 - a0, b1 - b0):
                continue
            if abs((a1 - a0) - (b1 - b0)) > SAME_THICKNESS * max(a1 - a0, b1 - b0):
                continue
            thickness = (a.length + b.length) / 2.0
            centre = (a.mid + b.mid) / 2.0
            centre = centre + t * ((max(a0, b0) + min(a1, b1)) / 2.0 - float(np.dot(centre, t)))
            rect = oriented_rect(float(centre[0]), float(centre[1]), float(t[0]), float(t[1]), thickness,
                                 -gap / 2.0, gap / 2.0)  # length along t = wall thickness, v along u = the gap
            # the corridor between the ends must be empty
            if rect.buffer(-0.02).intersects(walls) or rect.intersects(taken):
                continue
            candidates.append((gap, i, j, centre, u, thickness))
    for gap, i, j, centre, u, thickness in sorted(candidates, key=lambda c: c[0]):
        if i in used or j in used:
            continue
        used.update((i, j))
        ax, ay = float(u[0]), float(u[1])
        if ax < -1e-9 or (abs(ax) <= 1e-9 and ay < 0):
            ax, ay = -ax, -ay  # a fixed sense (east / north), like every opening
        op = Opening("passage", Polygon(), Polygon(), 0.0, min(cfg.door_height, cfg.wall_height), None,
                     axis=(ax, ay), center=(float(centre[0]), float(centre[1])), width=gap, thickness=thickness)
        op.rebuild(cfg)
        op.src = {"width": "muri", "height": "default", "sill": "default"}
        op.notes.append("vano senza simbolo dedotto da due testate di muro allineate: controlla "
                        "(MODIFICA_tieni = no lo richiude)")
        found.append(op)
    if cfg.shape_openings and found:
        found = [_outside_window(op, building_footprint(union([walls] + [o.fill for o in found])), cfg) for op in found]
    return found


def _open_side(footprint: BaseGeometry, op: Opening, side: int) -> bool:
    """Is the outside on this side of the opening: the point just beyond the wall is not inside the building and no
    wall stands across the opening within ``OUTSIDE_FREE``?"""
    ux, uy = op.axis
    cx, cy = op.center
    reach = op.thickness / 2.0 + 0.3
    if footprint.covers(Point(cx - side * uy * reach, cy + side * ux * reach)):
        return False
    stripe = oriented_rect(cx, cy, ux, uy, 0.8 * op.width, min(side * reach, side * (reach + OUTSIDE_FREE)),
                           max(side * reach, side * (reach + OUTSIDE_FREE)))
    return not footprint.intersects(stripe)


def _outside_window(op: Opening, footprint: BaseGeometry, cfg: Config) -> Opening:
    """A gap in a wall that has the outside on one side and the rooms on the other is a window (or a glazed door) the
    drawing shows only as a break in the wall lines: the same opening, with a sill and a glass pane."""
    ahead, behind = _open_side(footprint, op, 1), _open_side(footprint, op, -1)
    if ahead == behind:
        return op  # inside, or a free-standing wall: a doorway
    win = Opening("window", Polygon(), Polygon(), cfg.window_sill,
                  min(cfg.window_sill + cfg.window_height, cfg.wall_height), None, axis=op.axis, center=op.center,
                  width=op.width, thickness=op.thickness)
    win.rebuild(cfg)
    win.src = {"width": "muri", "height": "default", "sill": "default", "kind": "varco nel muro esterno",
               "sashes": "nessuna linea di taglio: un'anta"}
    win.notes.append("finestra dedotta da un varco nel muro esterno, senza nessun simbolo: controlla il tipo, "
                     "il davanzale e le ante nella tabella (MODIFICA_*)")
    return win
