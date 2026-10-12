"""Walls drawn as one line, on a layer whose walls are drawn in pairs.

On a layer the program read by the shape of its lines (walls are two parallel lines) a line with no partner makes no
wall. Most such lines are none: the edge of a table, of a counter, a fence, a hatching. But one that closes a space no
wall closes is a thin wall the author drew with one line (a partition, the side of a room left open otherwise): the
space is the evidence. A line counts when it is long, continuous (not dashed), outside the walls found, and when
taking it away opens a space of ``MIN_GAIN`` m2 at least (gaps of a door or a window do not count as open); lines that
only divide a closed space add no closed space and are left out, so are those that close one only together with a
longer one that does it alone.
"""

from __future__ import annotations

import math

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from .geom import closed_by_square, polygons_of, union
from .reader import Item
from .walls import faces_from_pairs

MIN_LENGTH = 2.0  # m: a shorter line is a piece of furniture or a detail, not a wall that closes a space
BODY_MARGIN = 0.03  # m: a line this close to a wall found is the edge of it, not a wall of its own
FREE_SHARE = 0.8  # at least this share of a line lies off the walls found
MAX_LINES = 16  # more lines than this on the layers: the drawing is not one of closed spaces drawn with single lines (nothing is read)
THICKNESS = 0.10  # m: a wall drawn with one line is taken as a thin partition (a guess: the report says so)
CLOSING = 1.0  # m: gaps up to twice this (a door, a window) do not open a space
MIN_GAIN = 4.0  # m2: a line must close this much more space than there is without it
SNAP_END = 0.30  # m: an end this close to a wall is prolonged to it, so that the thin wall and the wall are one body
DUPLICATE = 0.01  # m: two lines whose ends differ by less than this are one line drawn twice
ATTACH_REACH = 1.5  # m: the stubs of wall that a thin wall continues are looked for this far from it


def _free_lines(items: list[Item], walls: BaseGeometry, layers: tuple[str, ...]) -> list[LineString]:
    """The long straight continuous lines of ``layers`` that no wall body holds, each once."""
    body = walls.buffer(BODY_MARGIN)
    seen: set[tuple[int, ...]] = set()
    found: list[LineString] = []
    for it in items:
        if it.category != "wall" or it.layer not in layers:
            continue
        for p in it.prims:
            pts = list(p.geom.coords) if p.kind == "line" and p.geom.geom_type == "LineString" else []
            if len(pts) != 2 or p.meta.get("arc") or p.meta.get("broken") or math.dist(*pts) < MIN_LENGTH:
                continue
            key = tuple(sorted(round(v / DUPLICATE) for pt in pts for v in pt))
            line = LineString(pts)
            if key not in seen and line.difference(body).length >= FREE_SHARE * line.length:
                seen.add(key)
                found.append(line)
    return found


def _enclosed(walls: BaseGeometry, lines: list[LineString]) -> float:
    """The area closed by the walls and these lines as thin walls, gaps up to ``2 * CLOSING`` bridged."""
    shape = union([walls] + [line.buffer(THICKNESS / 2.0, cap_style="flat") for line in lines])
    closed = closed_by_square(shape, CLOSING)
    return sum(Polygon(ring).area for poly in polygons_of(closed) for ring in poly.interiors)


def _snapped(line: LineString, walls: BaseGeometry) -> LineString:
    """The line with each end that is within ``SNAP_END`` of a wall in its own direction prolonged into it."""
    (x0, y0), (x1, y1) = line.coords
    length = line.length
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    ends = [(x0, y0), (x1, y1)]
    for k, sign in ((0, -1), (1, 1)):
        ex, ey = ends[k]
        probe = LineString([(ex, ey), (ex + sign * ux * SNAP_END, ey + sign * uy * SNAP_END)])
        hit = probe.intersection(walls)
        if not hit.is_empty and hit.distance(Point(ex, ey)) > 0:
            reach = hit.distance(Point(ex, ey)) + THICKNESS / 2.0
            ends[k] = (ex + sign * ux * reach, ey + sign * uy * reach)
    return LineString(ends)


def _attached_pieces(items: list[Item], walls: BaseGeometry, thin: BaseGeometry, layers: tuple[str, ...],
                     max_thickness: float) -> BaseGeometry:
    """The small wall pieces drawn as pairs of lines (a stub of wall at a door jamb) that touch the thin walls: on their
    own they were dropped as lone specks, but a speck that a wall continues is the end of a wall."""
    near = thin.buffer(ATTACH_REACH)
    lines = [p.geom for it in items if it.category == "wall" and it.layer in layers for p in it.prims
             if p.kind == "line" and not p.meta.get("arc") and not p.meta.get("broken") and near.intersects(p.geom)]
    pieces = [f for f in faces_from_pairs(lines, max_thickness)
              if f.distance(thin) <= BODY_MARGIN and f.difference(walls.buffer(BODY_MARGIN)).area >= 0.5 * f.area]
    return union(pieces)


def single_line_walls(items: list[Item], walls: BaseGeometry, layers: tuple[str, ...],
                      max_thickness: float) -> tuple[BaseGeometry, list[LineString], float]:
    """(the thin walls, their lines, the area they close): the single lines of ``layers`` that close a space, as thin
    walls (with the stubs of wall that touch them). Empty when no line does. ``walls``: the wall bodies found."""
    lines = _free_lines(items, walls, layers)
    if not lines or len(lines) > MAX_LINES:
        return union([]), [], 0.0
    lines = [_snapped(line, walls) for line in lines]
    keep = list(lines)
    base = _enclosed(walls, [])
    enclosed = _enclosed(walls, keep)
    dropped = True
    while dropped and keep:
        dropped = False
        for line in sorted(keep, key=lambda ln: ln.length):  # the shortest first: a long line is not given up for a short one
            rest = [other for other in keep if other is not line]
            without = _enclosed(walls, rest)
            if enclosed - without < MIN_GAIN:  # it closes nothing that the others do not close without it (or it divides a space)
                keep, enclosed, dropped = rest, without, True
                break
    if not keep:
        return union([]), [], 0.0
    thin = union(line.buffer(THICKNESS / 2.0, cap_style="flat") for line in keep)
    return union([thin, _attached_pieces(items, walls, thin, layers, max_thickness)]), keep, enclosed - base
