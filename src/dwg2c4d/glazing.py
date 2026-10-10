"""Ribbon windows drawn as thin glazing strips along the outside wall.

Some plans show a window only by its glass: two or more parallel lines (or a long thin rectangle) a few
centimetres apart, on a layer that is called "Linee" like everything else, inside a wall whose lines stop
(or are not drawn at all) there. No name says "window", and the gap is often wider than any doorway, so neither
the layers nor the gaps in the walls find it; worse, the wall detector pairs the strip with the outside face
line of the wall and builds a solid slab out of the glass.

A strip is a bundle of parallel lines within ``STRIP_THICKNESS`` of one another, ``STRIP_LENGTH`` long. The
sashes of one window lie on one line, a mullion apart: together they are a bay. A bay is a window when the
walls hold it at both ends (it sits in a wall) and the outside is on exactly one side of that wall: nothing
within ``FREE_REACH`` on one side, the building on the other. Interior glass partitions, door leaves and
furniture edges fail one of the two tests. The bay gets the thickness of the wall that holds it, the wall is
rebuilt over it and the glass lines no longer make a wall of their own.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from .config import Config
from .geom import oriented_rect, polygons_of, union
from .openings import ON_WALL, Opening, _cross_section
from .reader import Item
from .walls import _pair_candidates

STRIP_THICKNESS = 0.06  # m: the lines of a strip lie within this across (a pane and its frame)
STRIP_MIN_GAP = 0.004  # m: two lines closer than this are one line drawn twice
STRIP_LENGTH = (0.6, 3.5)  # m: a sash is this long; a longer thin pair of lines is a wall drawn thin
STRIP_ANGLE = math.radians(1.0)  # lines whose directions differ by less than this are parallel
MAX_STRIPS = 4000  # more strips than this: the drawing is not made of windows (hatches, contours): nothing is read
BAY_GAP = 0.30  # m: sashes this close along one line are one bay (the mullions between them)
BAY_LINE = 0.05  # m: strips whose middle lines are this close across lie on one line
MULLION_MIN = 0.02  # m: a space between two sashes at least this wide is a mullion (the bay is divided there)
FLANK_REACH = (0.03, 0.10, 0.25)  # m: where beyond the ends of a bay the wall that holds it is looked for...
FLANK_DEPTH = (0.10, 0.25, 0.40)  # m: ... and how far across from the strip
SNAP_END = 0.30  # m: a bay reaches the wall face when this is how far it lies beyond its last line, at most
SECTION_TOLERANCE = 0.10  # m: the wall sections at the two ends of a bay differ by less than this (one wall)
IN_WALL = 0.15  # m: the strip lies this close to the section of the wall that holds it, at most
NEAR_STEP = 0.03  # m: of two walls, the one the strip lies in or nearer to (this much of a difference counts)
JAMB = (0.10, 0.60)  # m: a line across the wall that closes its end is this long (not the whole thickness of a wall: shorter)
JAMB_TURN = 0.1  # sine of the angle: a jamb is across the bay's line within this
JAMB_BACK = 0.03  # m: a jamb may lie this far inside the end of the glass
FACE_MARGIN = 0.15  # m: the free side is looked at from this far beyond the wall faces
FREE_REACH = 12.0  # m: the outside is free of walls this far
HIT_REACH = 80.0  # m: the inside meets a wall within this
ZONE_MARGIN = 0.005  # m: the glass lines are cleared from the wall this far beyond their own extent
END_MARGIN = 0.10  # m: the free/hit test leaves out this much of the bay at each end (the piers are no obstacle)


@dataclass
class Strip:
    """A bundle of thin parallel lines: along ``u`` from ``a0`` to ``a1``, across ``v`` from ``v0`` to ``v1``
    (``v`` is ``u`` turned 90 degrees to the left; both are absolute coordinates)."""

    u: tuple[float, float]
    a0: float
    a1: float
    v0: float
    v1: float


@dataclass
class Bay:
    """Strips on one line, a mullion apart: the glass of one window."""

    u: tuple[float, float]
    a0: float
    a1: float
    v0: float
    v1: float
    mullions: list[float]  # along u, between the sashes

    @property
    def v(self) -> tuple[float, float]:
        return (-self.u[1], self.u[0])


def _canonical(theta: float) -> tuple[float, float]:
    """The direction of a line as a unit vector in the one sense windows and doors use (east / north)."""
    ux, uy = math.cos(theta), math.sin(theta)
    return (-ux, -uy) if ux < -1e-9 or (abs(ux) <= 1e-9 and uy < 0) else (ux, uy)


def _components(n: int, pairs: np.ndarray, alone: bool = False) -> list[list[int]]:
    """Connected groups of the ``n`` items, given index pairs; items in no pair are left out unless ``alone``."""
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in pairs:
        parent[find(int(i))] = find(int(j))
    groups: dict[int, set[int]] = {}
    for i, j in pairs:
        groups.setdefault(find(int(i)), set()).update((int(i), int(j)))
    if alone:
        for i in range(n):
            groups.setdefault(find(i), {i})
    return [sorted(g) for g in groups.values()]


def find_strips(seg: np.ndarray) -> list[Strip]:
    """Bundles of thin parallel lines among ``seg`` (n x 4: x0, y0, x1, y1, metres): at least two lines that
    overlap for ``STRIP_LENGTH[0]``, no more than ``STRIP_THICKNESS`` apart across, in all no longer than
    ``STRIP_LENGTH[1]``."""
    if len(seg) < 2:
        return []
    d = seg[:, 2:] - seg[:, :2]
    theta = np.arctan2(d[:, 1], d[:, 0]) % math.pi
    found = _pair_candidates(seg, theta, STRIP_THICKNESS)
    if found is None or not len(found[0]):
        return []
    ia, ib = found
    ux, uy = np.cos(theta), np.sin(theta)
    mx, my = (seg[:, 0] + seg[:, 2]) / 2, (seg[:, 1] + seg[:, 3]) / 2
    turn = np.abs((theta[ib] - theta[ia] + math.pi / 2) % math.pi - math.pi / 2)
    across = np.abs((mx[ib] - mx[ia]) * -uy[ia] + (my[ib] - my[ia]) * ux[ia])
    ok = (turn <= STRIP_ANGLE) & (across >= STRIP_MIN_GAP) & (across <= STRIP_THICKNESS)
    ia, ib = ia[ok], ib[ok]
    a = [seg[ia, 0] * ux[ia] + seg[ia, 1] * uy[ia], seg[ia, 2] * ux[ia] + seg[ia, 3] * uy[ia]]
    b = [seg[ib, 0] * ux[ia] + seg[ib, 1] * uy[ia], seg[ib, 2] * ux[ia] + seg[ib, 3] * uy[ia]]
    overlap = np.minimum(np.maximum(*a), np.maximum(*b)) - np.maximum(np.minimum(*a), np.minimum(*b))
    keep = overlap >= STRIP_LENGTH[0]
    strips: list[Strip] = []
    for members in _components(len(seg), np.stack([ia[keep], ib[keep]], axis=1)):
        ref = max(members, key=lambda k: math.hypot(*d[k]))
        u = _canonical(float(theta[ref]))
        v = (-u[1], u[0])
        along = [p for k in members for p in (seg[k, 0] * u[0] + seg[k, 1] * u[1], seg[k, 2] * u[0] + seg[k, 3] * u[1])]
        offsets = [(seg[k, 0] + seg[k, 2]) / 2 * v[0] + (seg[k, 1] + seg[k, 3]) / 2 * v[1] for k in members]
        if max(offsets) - min(offsets) > STRIP_THICKNESS or not STRIP_LENGTH[0] <= max(along) - min(along) <= STRIP_LENGTH[1]:
            continue
        strips.append(Strip(u, min(along), max(along), min(offsets), max(offsets)))
    return strips


def find_bays(strips: list[Strip]) -> list[Bay]:
    """Strips on one line (parallel, middle lines within ``BAY_LINE`` across), less than ``BAY_GAP`` apart along it."""
    n = len(strips)
    if n == 0 or n > MAX_STRIPS:
        return []
    theta = np.array([math.atan2(s.u[1], s.u[0]) for s in strips])
    centre = np.array([[(s.a0 + s.a1) / 2 * s.u[0] - (s.v0 + s.v1) / 2 * s.u[1],
                        (s.a0 + s.a1) / 2 * s.u[1] + (s.v0 + s.v1) / 2 * s.u[0]] for s in strips])
    half = np.array([(s.a1 - s.a0) / 2 for s in strips])
    ux, uy = np.cos(theta), np.sin(theta)
    # for every pair (i, j): j seen along and across the line of i
    dx, dy = centre[None, :, 0] - centre[:, None, 0], centre[None, :, 1] - centre[:, None, 1]
    along = dx * ux[:, None] + dy * uy[:, None]
    across = np.abs(-dx * uy[:, None] + dy * ux[:, None])
    turn = np.abs((theta[None, :] - theta[:, None] + math.pi / 2) % math.pi - math.pi / 2)
    near = (turn <= STRIP_ANGLE) & (across <= BAY_LINE) & (np.abs(along) - half[:, None] - half[None, :] <= BAY_GAP)
    bays: list[Bay] = []
    for members in _components(n, np.argwhere(np.triu(near, 1)), alone=True):
        ref = max(members, key=lambda k: strips[k].a1 - strips[k].a0)
        u = strips[ref].u
        v = (-u[1], u[0])
        mid = [float(centre[k] @ np.array(u)) for k in members]
        spans = sorted(zip((m - half[k] for m, k in zip(mid, members)), (m + half[k] for m, k in zip(mid, members))))
        mullions, last = [], spans[0][1]
        for s0, s1 in spans[1:]:
            if s0 - last >= MULLION_MIN:
                mullions.append((last + s0) / 2.0)
            last = max(last, s1)
        across = [float(centre[k] @ np.array(v)) for k in members]
        bays.append(Bay(u, spans[0][0], last, min(across) - 0.5 * min(strips[k].v1 - strips[k].v0 for k in members),
                        max(across) + 0.5 * min(strips[k].v1 - strips[k].v0 for k in members), mullions))
    return bays


def _outlines(geom: BaseGeometry) -> list[LineString]:
    """The lines of a drawn shape: a line itself, the outline (and the holes) of a closed one."""
    out: list[LineString] = []
    for g in getattr(geom, "geoms", [geom]):
        if g.geom_type == "Polygon":
            out.extend([g.exterior, *g.interiors])
        elif g.geom_type == "LineString":
            out.append(g)
        elif hasattr(g, "geoms"):
            out.extend(_outlines(g))
    return out


class _Ink:
    """What counts as wall for the tests around a bay: the wall bodies, and every line of the wall layers (an outside
    face line is a wall's outline although it is no wall body yet). ``pieces`` are the straight pieces of those
    lines, to find the jamb a wall ends with."""

    def __init__(self, walls: BaseGeometry, items: list[Item]):
        parts: list[BaseGeometry] = list(polygons_of(walls))
        pieces: list[LineString] = []
        for it in items:
            if it.category != "wall":
                continue
            for p in it.prims:
                for line in _outlines(p.geom):
                    parts.append(line)
                    pts = list(line.coords)
                    pieces.extend(LineString([a, b]) for a, b in zip(pts, pts[1:]) if math.dist(a, b) >= JAMB[0])
        self.tree = STRtree(parts)
        self.pieces = pieces
        self.piece_tree = STRtree(pieces)


Section = tuple[float, float, float]  # across the wall from s0 to s1 (absolute v), and the gap along u to its face


def _beside(walls: BaseGeometry, bay: Bay, max_t: float) -> BaseGeometry:
    """The walls around the bay without the part over its glass: the slabs the glass lines make with the outside face
    line would otherwise hide the pier that is two centimetres from the glass."""
    ux, uy = bay.u
    mid = (bay.a0 + bay.a1) / 2.0
    reach = max_t + 0.5
    around = oriented_rect(mid * ux, mid * uy, ux, uy, bay.a1 - bay.a0 + 2 * reach, bay.v0 - reach, bay.v1 + reach)
    glass = oriented_rect(mid * ux, mid * uy, ux, uy, bay.a1 - bay.a0, bay.v0 - reach, bay.v1 + reach)
    return walls.intersection(around).difference(glass)


def _sections_at(walls: BaseGeometry, ink: _Ink, bay: Bay, end: float, sign: int, max_t: float) -> list[Section]:
    """The walls that may hold one end of the bay (``sign`` is +1 beyond the end a1, -1 beyond a0), as sections: a wall
    body beside the end, cut across; or a jamb line, a short line across the wall that closes its end."""
    ux, uy = bay.u
    vx, vy = bay.v
    centre_v = (bay.v0 + bay.v1) / 2.0
    found: dict[tuple[int, int], Section] = {}
    for reach in FLANK_REACH:
        for depth in FLANK_DEPTH:
            for side in (-1, 1):
                u, v = end + sign * reach, centre_v + side * depth
                p = Point(u * ux + v * vx, u * uy + v * vy)
                if walls.distance(p) > ON_WALL:
                    continue
                section = _cross_section(walls, p, vx, vy, max_t)
                if section is None or section[1] - section[0] > max_t:
                    continue
                vm = (section[0] + section[1]) / 2.0
                ray = LineString([(end * ux + vm * vx, end * uy + vm * vy),
                                  ((end + sign * SNAP_END) * ux + vm * vx, (end + sign * SNAP_END) * uy + vm * vy)])
                hit = walls.intersection(ray)
                gap = 0.0 if hit.is_empty else hit.distance(Point(ray.coords[0]))
                found.setdefault((round(section[0] * 100), round(section[1] * 100)),
                                 (section[0], section[1], gap if gap <= SNAP_END else 0.0))
    mid = end + sign * (SNAP_END - JAMB_BACK) / 2.0  # from just inside the bay's end to SNAP_END beyond it
    area = oriented_rect(mid * ux, mid * uy, ux, uy, SNAP_END + JAMB_BACK, centre_v - max_t - IN_WALL, centre_v + max_t + IN_WALL)
    for k in ink.piece_tree.query(area, predicate="intersects"):
        (ax, ay), (bx, by) = ink.pieces[int(k)].coords
        length = math.hypot(bx - ax, by - ay)
        if abs((bx - ax) * ux + (by - ay) * uy) > JAMB_TURN * length:
            continue  # not across the wall
        along = ((ax + bx) / 2) * ux + ((ay + by) / 2) * uy
        lo, hi = sorted((ax * vx + ay * vy, bx * vx + by * vy))
        if JAMB[0] <= hi - lo <= max_t and bay.v0 >= lo - IN_WALL and bay.v1 <= hi + IN_WALL \
                and -JAMB_BACK <= sign * (along - end) <= SNAP_END:
            found.setdefault((round(lo * 100), round(hi * 100)), (lo, hi, max(sign * (along - end), 0.0)))
    return list(found.values())


def _one_wall(first: list[Section], second: list[Section], bay: Bay) -> tuple[Section, Section] | None:
    """The two sections, one at each end of the bay, that are the same wall (their faces differ by less than
    ``SECTION_TOLERANCE``) and hold the glass. Among those: the glass lies in it or the closest to it (to
    ``NEAR_STEP``), then the thinnest (the wall, not a pilaster or a box beside it), then the faces agree best."""
    best: tuple[int, float, float, float, Section, Section] | None = None
    for a in first:
        for b in second:
            miss = max(abs(a[0] - b[0]), abs(a[1] - b[1]))
            s0, s1 = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            off = max(s0 - bay.v1, bay.v0 - s1, 0.0)  # how far the glass lies from the wall (0: in it)
            if miss > SECTION_TOLERANCE or off > IN_WALL:
                continue
            key = (int(off / NEAR_STEP), round(s1 - s0, 2), miss, a[2] + b[2])
            if best is None or key < best[:4]:
                best = (*key, a, b)
    return None if best is None else (best[4], best[5])


def _side_is(ink: STRtree, bay: Bay, a0: float, a1: float, edge: float, side: int, reach: float) -> bool:
    """Is there a wall (or an outline of one) across the bay, on ``side`` of the wall section, between
    ``FACE_MARGIN`` beyond its face at ``edge`` and ``reach`` farther?"""
    ux, uy = bay.u
    vx, vy = bay.v
    v0, v1 = edge + side * FACE_MARGIN, edge + side * (FACE_MARGIN + reach)
    zone = oriented_rect(((a0 + a1) / 2) * ux, ((a0 + a1) / 2) * uy, ux, uy, max(a1 - a0 - 2 * END_MARGIN, 0.05),
                         min(v0, v1), max(v0, v1))
    return len(ink.query(zone, predicate="intersects")) > 0


def ribbon_windows(seg: np.ndarray, walls: BaseGeometry, items: list[Item], cfg: Config) -> tuple[list[Opening], BaseGeometry]:
    """The windows made of glazing strips, and the zone of the walls the glass lines would otherwise fill with a
    wall of their own (``walls`` minus the zone, plus the openings' fills, is the wall continuous over the bay).

    ``seg``: the long straight pieces of the drawing (see ``reader``); ``walls``: the wall bodies as built."""
    bays = find_bays(find_strips(seg))
    if not bays or walls.is_empty:
        return [], union([])
    ink = _Ink(walls, items)
    found: list[Opening] = []
    zones = []
    for bay in bays:
        beside = _beside(walls, bay, cfg.max_wall_thickness)
        wall = _one_wall(_sections_at(beside, ink, bay, bay.a0, -1, cfg.max_wall_thickness),
                         _sections_at(beside, ink, bay, bay.a1, 1, cfg.max_wall_thickness), bay)
        if wall is None:
            continue  # no wall at both ends of the glass, or not the same: it does not sit in a wall
        end0, end1 = wall
        s0, s1 = (end0[0] + end1[0]) / 2, (end0[1] + end1[1]) / 2
        a0, a1 = bay.a0 - end0[2], bay.a1 + end1[2]
        sides = ((s0, -1), (s1, 1))
        free = [not _side_is(ink.tree, bay, a0, a1, edge, side, FREE_REACH) for edge, side in sides]
        meets = [_side_is(ink.tree, bay, a0, a1, edge, side, HIT_REACH) for edge, side in sides]
        if (free[0] and meets[1]) == (free[1] and meets[0]):
            continue  # the outside is not on exactly one side, with the building on the other
        ux, uy = bay.u
        vx, vy = bay.v
        mid, vm = (a0 + a1) / 2.0, (s0 + s1) / 2.0
        z1 = min(cfg.window_sill + cfg.window_height, cfg.wall_height)
        if z1 <= cfg.window_sill:
            continue
        op = Opening("window", Polygon(), Polygon(), cfg.window_sill, z1, None, axis=(ux, uy),
                     center=(mid * ux + vm * vx, mid * uy + vm * vy), width=a1 - a0, thickness=s1 - s0)
        op.rebuild(cfg)
        op.dividers = [m - mid for m in bay.mullions if a0 < m < a1]
        op.src = {"width": "geometria", "height": "default", "sill": "default", "kind": "strisce di vetro",
                  "sashes": "spazi tra le strisce di vetro" if op.dividers else "nessuna linea di taglio: un'anta"}
        op.notes.append(f"finestra a nastro riconosciuta dalle strisce sottili del vetro ({len(op.dividers) + 1} ante, "
                        f"{op.width:.2f} m, nel muro di {op.thickness * 100:.0f} cm): controlla il tipo, il davanzale e "
                        "le ante nella tabella (MODIFICA_*)")
        found.append(op)
        zones.append(oriented_rect(mid * ux, mid * uy, ux, uy, a1 - a0, min(s0, bay.v0) - ZONE_MARGIN,
                                   max(s1, bay.v1) + ZONE_MARGIN))
    return found, union(zones)
