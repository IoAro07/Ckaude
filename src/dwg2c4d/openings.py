"""Doors and windows: where they are, how wide, and how thick the wall is there.

The wall is rebuilt across the opening and then cut only between the opening's
bottom and top. This handles both ways of drawing a plan:
  * the wall lines run through the symbol -> the wall is cut;
  * the wall lines stop at the symbol (a gap) -> the gap is filled first, so the
    lintel above a door and the parapet/lintel around a window still exist.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from .config import Config
from .geom import long_axis, oriented_rect, polygons_of, union
from .reader import Item, Prim

CLUSTER_TOL = 0.03  # loose lines closer than this belong to the same symbol
REGION = 0.15  # a symbol must lie within this distance of a wall
ON_WALL = 0.02  # a sample point this close to a wall counts as "on" it
MIN_OPENING_WIDTH = 0.2
MIN_DIVIDER = 0.02  # a cut mark across a window symbol is at least this long (m)
OVERLAP = 0.005  # a gap filler reaches this far into the wall on each side, so the pieces merge
STATION_OFFSETS = (0.03, 0.10, 0.25)  # where to look for the wall just outside the symbol


@dataclass
class Symbol:
    """One door/window symbol of the plan: a block reference, or loose entities that touch."""

    geom: BaseGeometry
    prims: list[Prim]
    block: str | None = None
    layer: str = ""
    inferred: str = ""  # how a door/window on a mixed "Infissi" layer was told apart
    by_shape: bool = False  # a swing arc found on a layer that says nothing: it counts only if its hinge is at a wall


@dataclass
class Opening:
    kind: str  # "door" | "window" | "passage"
    cut: Polygon  # footprint removed from the walls between z0 and z1
    fill: Polygon  # same, slightly longer: used to close a gap drawn in the wall lines
    z0: float
    z1: float
    glass: Polygon | None  # thin pane for windows (simple fixtures)
    axis: tuple[float, float] = (1.0, 0.0)  # unit vector along the wall
    center: tuple[float, float] = (0.0, 0.0)  # middle of the opening, on the wall's mid-plane
    from_elevation: bool = False  # z0/z1 were read from an elevation drawing
    z1_drawn: float | None = None  # the top of the symbol in that elevation, above the floor (z1 is that cut at the wall height)
    # What the fixtures and the openings table need:
    id: str = ""
    width: float = 0.0  # along the wall (m)
    thickness: float = 0.0  # wall thickness at the opening (m)
    leaves: list[dict] = field(default_factory=list)  # doors: {"hinge": -1|1, "width": m, "swing": -1|1}
    dividers: list[float] = field(default_factory=list)  # windows: cut lines, along the wall from the centre (m)
    hinge: int = 0  # leaf without an arc: -1 hinged on the -u side, 1 on +u, 0 = not known
    src: dict = field(default_factory=dict)  # where width/height/sill/leaves came from
    notes: list[str] = field(default_factory=list)
    keep: bool = True  # False: the table asked to drop it (the gap is closed with wall)
    face_sign: int = 0  # +1/-1: the local Z of its group points along +v / -v (outside, or the swing side); 0 = not decided
    label: str = ""
    layer: str = ""
    block: str = ""

    @property
    def u(self) -> tuple[float, float]:
        return self.axis

    @property
    def v(self) -> tuple[float, float]:
        return (-self.axis[1], self.axis[0])

    @property
    def sashes(self) -> int:
        return len(self.dividers) + 1

    def rebuild(self, cfg: Config) -> None:
        """Recompute the footprints after width/thickness/centre were changed."""
        ux, uy = self.axis
        cx, cy = self.center
        t2 = self.thickness / 2.0
        self.cut = oriented_rect(cx, cy, ux, uy, self.width, -t2 - 0.002, t2 + 0.002)
        self.fill = oriented_rect(cx, cy, ux, uy, self.width + 2 * OVERLAP, -t2, t2)
        h = cfg.glass_thickness / 2.0
        self.glass = oriented_rect(cx, cy, ux, uy, self.width, -h, h) if (self.kind == "window" and cfg.glass) else None


def _symbols(items: list[Item], kind: str) -> list[Symbol]:
    """One symbol per block reference; loose entities are grouped by proximity."""
    symbols: list[Symbol] = []
    loose: list[tuple[BaseGeometry, Prim, str]] = []
    for it in items:
        if it.category != kind:
            continue
        if it.block:
            symbols.append(Symbol(union([p.geom for p in it.prims]), list(it.prims), it.block, it.layer))
        else:
            loose.extend((p.geom, p, it.layer) for p in it.prims)
    if loose:
        grown = union(g.buffer(CLUSTER_TOL) for g, _, _ in loose)
        for cl in getattr(grown, "geoms", [grown]):
            members = [(g, p, lay) for g, p, lay in loose if cl.intersects(g)]
            prims = [p for _, p, _ in members]
            symbols.append(Symbol(union([g for g, _, _ in members]), prims, None, members[0][2] if members else "",
                                  by_shape=all(p.meta.get("shape_door") for p in prims)))
    return symbols


class _WallEdges:
    """Wall boundary segments, to find the direction a wall runs near a point."""

    def __init__(self, walls: BaseGeometry):
        segs, self.lengths, self.angles = [], [], []
        for poly in polygons_of(walls):
            for ring in (poly.exterior, *poly.interiors):
                c = np.asarray(ring.coords)
                for a, b in zip(c[:-1], c[1:]):
                    d = b - a
                    n = math.hypot(*d)
                    if n < 1e-9:
                        continue
                    segs.append(LineString([a, b]))
                    self.lengths.append(n)
                    self.angles.append(math.atan2(d[1], d[0]) % math.pi)
        self.lengths = np.asarray(self.lengths)
        self.angles = np.asarray(self.angles)
        self.tree = STRtree(segs) if segs else None

    def directions_near(self, region: BaseGeometry, limit: int = 3) -> list[tuple[float, float]]:
        """Unit vectors of the dominant wall directions among edges touching ``region``,
        strongest first. Edges are weighted by their full length (long faces beat short
        jamb ends); weaker directions are kept as fallbacks (a long perpendicular wall
        nearby can outweigh the wall the opening is in)."""
        if self.tree is None:
            return []
        idx = self.tree.query(region, predicate="intersects")
        if len(idx) == 0:
            return []
        ang, w = self.angles[idx], self.lengths[idx]
        nbins = 90
        bins = np.bincount((ang / math.pi * nbins).astype(int) % nbins, weights=w, minlength=nbins)
        out: list[tuple[float, float]] = []
        taken: list[int] = []
        for b in np.argsort(bins)[::-1]:
            if len(out) >= limit or bins[b] < 0.15 * bins.max():
                break
            if any(min((b - t) % nbins, (t - b) % nbins) <= 2 for t in taken):
                continue
            taken.append(int(b))
            centre = (b + 0.5) * math.pi / nbins
            diff = np.abs((ang - centre + math.pi / 2) % math.pi - math.pi / 2)
            near = diff < math.radians(3)
            if near.any():
                # directions are mod pi: 0 and pi-eps are the same line, so average the doubled angle
                theta = 0.5 * math.atan2(float((w[near] * np.sin(2 * ang[near])).sum()),
                                         float((w[near] * np.cos(2 * ang[near])).sum())) % math.pi
            else:
                theta = centre
            out.append((math.cos(theta), math.sin(theta)))
        return out


def _cross_section(walls: BaseGeometry, p: Point, vx: float, vy: float,
                   reach: float) -> tuple[float, float] | None:
    """Wall extent along v through ``p`` (absolute v coordinates), nearest piece."""
    line = LineString([(p.x - vx * reach, p.y - vy * reach), (p.x + vx * reach, p.y + vy * reach)])
    hit = walls.intersection(line)
    pieces = [g for g in getattr(hit, "geoms", [hit]) if isinstance(g, LineString) and g.length > 0]
    if not pieces:
        return None
    piece = min(pieces, key=lambda g: g.distance(p))
    vs = [x * vx + y * vy for x, y in piece.coords]
    return min(vs), max(vs)


def _try_direction(hull: Polygon, walls: BaseGeometry, ux: float, uy: float,
                   max_t: float) -> tuple[float, float, float, float, float, float] | None:
    """Opening geometry assuming the wall runs along (ux, uy)."""
    vx, vy = -uy, ux
    pts = np.asarray(hull.exterior.coords)
    us, vs = pts @ [ux, uy], pts @ [vx, vy]
    a0, a1 = float(us.min()), float(us.max())
    if a1 - a0 < MIN_OPENING_WIDTH:
        return None
    mid = (a0 + a1) / 2.0
    v_candidates = [(vs.min() + vs.max()) / 2.0, float(vs.min()), float(vs.max())]
    stations = [mid, a0 + 0.1 * (a1 - a0), a1 - 0.1 * (a1 - a0)]
    for off in STATION_OFFSETS:
        stations += [a0 - off, a1 + off]

    # Measure the wall at every station that lands on it and keep the thinnest section:
    # next to a T-junction the section runs along the crossing wall and comes out too thick.
    sections: list[tuple[float, float]] = []
    for u in stations:
        best = None
        for v in v_candidates:
            p = Point(u * ux + v * vx, u * uy + v * vy)
            d = walls.distance(p)
            if d <= ON_WALL and (best is None or d < best[0]):
                best = (d, p)
        if best:
            section = _cross_section(walls, best[1], vx, vy, max_t)
            if section:
                sections.append(section)
    if not sections:
        return None
    s0, s1 = min(sections, key=lambda sec: sec[1] - sec[0])
    return ux, uy, a0, a1, s0, s1


def _locate_opening(sym: Symbol, walls: BaseGeometry, edges: _WallEdges,
                    max_t: float) -> tuple[float, float, float, float, float, float] | None:
    """(ux, uy, a0, a1, s0, s1): wall direction u, the opening's extent [a0, a1] along u
    and the wall's extent [s0, s1] across it (absolute coordinates), or None."""
    hull = sym.geom.convex_hull
    if not isinstance(hull, Polygon):
        hull = hull.buffer(0.005)
    region = hull.buffer(REGION)
    if not region.intersects(walls):
        return None

    candidates: list[tuple[float, float]] = []
    axis = long_axis(hull)
    if axis is not None and axis[4] >= 2.0 * max(axis[5], 0.05):
        candidates.append((axis[2], axis[3]))  # a window-like symbol: it lies along its wall
    for d in edges.directions_near(region):
        if not any(abs(d[0] * c[0] + d[1] * c[1]) > 0.999 for c in candidates):
            candidates.append(d)
    fallback = None
    for ux, uy in candidates:
        if ux < -1e-9 or (abs(ux) <= 1e-9 and uy < 0):
            ux, uy = -ux, -uy  # a fixed sense (east / north): hinge and table sides are named from it
        found = _try_direction(hull, walls, ux, uy, max_t)
        if not found:
            continue
        if found[5] - found[4] <= max_t:
            return found
        # Too thick to be the wall's section: this is the section along a crossing wall (the opening
        # was assumed to run the wrong way). Try the other directions, keep this one as a last resort.
        fallback = fallback or found
    return fallback


HINGE_AT_WALL = 0.30  # m: the centre of a door's swing arc is this close to the wall (at the jamb), at most
ARC_OFF_WALL = 0.08  # m: the middle of a door's swing arc is at least this far from the wall (a curved wall is not)


def _is_swing(sym: Symbol, walls: BaseGeometry) -> bool:
    """The arc of a door turns about its hinge, which sits in the wall at the side of the opening, and sweeps the
    room: the arc itself is off the wall (the arc of a rounded wall corner lies on it)."""
    for p in sym.prims:
        arc = p.meta.get("arc")
        if arc and walls.distance(Point(arc[0], arc[1])) <= HINGE_AT_WALL \
                and walls.distance(Point(arc[4])) >= ARC_OFF_WALL:
            return True
    return False


def _door_leaves(sym: Symbol, centre: tuple[float, float], u, v, width: float) -> list[dict]:
    """Door leaves from the swing arcs of the symbol: the arc's centre is the hinge, its radius the
    leaf width, which face it sweeps to tells the swing. Small arcs (handles) are ignored."""
    found: dict[int, dict] = {}
    for p in sym.prims:
        arc = p.meta.get("arc")
        if not arc:
            continue
        cx, cy, r, span, (mx, my) = arc
        if r < 0.3 * width or r > 1.3 * width + 0.1 or not (55.0 <= span <= 125.0):
            continue
        hu = (cx - centre[0]) * u[0] + (cy - centre[1]) * u[1]
        if abs(hu) < 0.25 * width:  # the hinge sits at one end of the opening
            continue
        hinge = 1 if hu > 0 else -1
        swing = 1 if ((mx - cx) * v[0] + (my - cy) * v[1]) > 0 else -1
        if hinge not in found or r > found[hinge]["width"]:
            found[hinge] = {"hinge": hinge, "width": r, "swing": swing}
    return [found[k] for k in sorted(found)]


def _dividers(sym: Symbol, centre: tuple[float, float], u, width: float, thickness: float) -> list[float]:
    """Positions (along the wall, from the centre) of the lines that cut across the symbol: the two
    outermost sets are the jambs, the others divide the opening into sashes. Lines closer than 10 cm
    are one mark (a double line)."""
    pos: list[float] = []
    for p in sym.prims:
        if p.meta.get("arc"):
            continue
        g = p.geom
        coords = list(g.exterior.coords) if g.geom_type == "Polygon" else list(g.coords)
        for (ax, ay), (bx, by) in zip(coords, coords[1:]):
            dx, dy = bx - ax, by - ay
            length = math.hypot(dx, dy)
            # any mark across the wall counts, even a 3 cm connector between two sliding panels
            if length < MIN_DIVIDER or abs(dx * u[0] + dy * u[1]) > 0.1 * length:
                continue
            pos.append(((ax + bx) / 2 - centre[0]) * u[0] + ((ay + by) / 2 - centre[1]) * u[1])
    pos.sort()
    marks: list[list[float]] = []
    for x in pos:
        if marks and x - marks[-1][-1] <= 0.10:
            marks[-1].append(x)
        else:
            marks.append([x])
    centres = [sum(m) / len(m) for m in marks]
    return [c for c in centres if abs(c) < width / 2 - 0.12]


def _has_swing_arc(sym: Symbol) -> bool:
    """A quarter-circle (55..125 degrees, 0.4..1.3 m radius) is a door leaf's swing; a window has none."""
    for p in sym.prims:
        arc = p.meta.get("arc")
        if arc and 55.0 <= arc[3] <= 125.0 and 0.4 <= arc[2] <= 1.3:
            return True
    return False


def _reclassify_mixed(by_kind: dict[str, list[Symbol]], cfg: Config) -> None:
    """Symbols on a layer that holds every opening ("Infissi"): with a swing arc they are doors, without
    they are windows. The symbols that turn out to be doors move to the door list."""
    for sym in list(by_kind["window"]):
        if not cfg.layers.is_mixed_openings(sym.layer):
            continue
        if _has_swing_arc(sym):
            sym.inferred = "arco di rotazione"
            by_kind["window"].remove(sym)
            by_kind["door"].append(sym)
        else:
            sym.inferred = "nessun arco di rotazione"


def build_openings(items: list[Item], walls: BaseGeometry, cfg: Config,
                   warnings: list[str]) -> list[Opening]:
    openings: list[Opening] = []
    if walls.is_empty:
        return openings
    edges = _WallEdges(walls)
    skipped = 0
    by_kind = {"door": _symbols(items, "door"), "window": _symbols(items, "window")}
    _reclassify_mixed(by_kind, cfg)
    by_kind["door"].sort(key=lambda sym: sym.by_shape)  # the doors the layers name first: a shape door never doubles one
    for kind in ("door", "window"):
        z0 = 0.0 if kind == "door" else cfg.window_sill
        z1 = min(cfg.door_height if kind == "door" else cfg.window_sill + cfg.window_height,
                 cfg.wall_height)
        if z1 <= z0:
            continue
        for sym in by_kind[kind]:
            if sym.by_shape and (not _is_swing(sym, walls)
                                 or any(sym.geom.convex_hull.distance(o.fill) <= 0.15 for o in openings)):
                continue  # a curved piece of furniture, a washbasin, a door already found: nothing to report
            found = _locate_opening(sym, walls, edges, cfg.max_wall_thickness)
            if found is None:
                skipped += 1
                continue
            ux, uy, a0, a1, s0, s1 = found
            vx, vy = -uy, ux
            # centre c has u = (a0+a1)/2 and v = 0, so v offsets are absolute
            mid = (a0 + a1) / 2.0
            width, thick = a1 - a0, s1 - s0
            centre = (mid * ux + (s0 + s1) / 2.0 * vx, mid * uy + (s0 + s1) / 2.0 * vy)
            op = Opening(kind, Polygon(), Polygon(), z0, z1, None, axis=(ux, uy), center=centre,
                         width=width, thickness=thick, layer=sym.layer, block=sym.block or "")
            op.rebuild(cfg)
            op.src = {"width": "geometria", "height": "default", "sill": "default"}
            if sym.by_shape:
                sym.inferred = "arco di rotazione"
                op.src["kind"] = "forma"
                op.notes.append(f"porta riconosciuta dal solo arco di rotazione (layer '{sym.layer}'): "
                                "controlla (MODIFICA_tipo nella tabella)")
            elif sym.inferred:
                op.src["kind"] = sym.inferred
                op.notes.append(f"{'porta' if kind == 'door' else 'finestra'} riconosciuta dalla forma sul layer "
                                f"'{sym.layer}' ({sym.inferred}): controlla (MODIFICA_tipo nella tabella)")
            if kind == "door":
                op.leaves = _door_leaves(sym, centre, (ux, uy), (vx, vy), width)
                if op.leaves:
                    op.src["leaves"] = "arco"
                else:
                    op.leaves = [{"hinge": -1, "width": width, "swing": 1}]
                    op.hinge = -1
                    op.src["leaves"] = "default"
                    op.notes.append("cerniera non deducibile (nessun arco di apertura nel simbolo): "
                                    "assunta a sinistra; correggi con la tabella")
            else:
                op.dividers = _dividers(sym, centre, (ux, uy), width, thick)
                op.src["sashes"] = "linee di taglio" if op.dividers else "nessuna linea di taglio: un'anta"
                if width > 1.6 and not op.dividers:
                    op.notes.append(f"anta unica larga {width:.2f} m (nessuna linea di taglio nel disegno): "
                                    "se sono piu' ante indicalo nella tabella")
            openings.append(op)
    if skipped:
        warnings.append(f"{skipped} porte/finestre non toccano nessun muro e sono state ignorate.")
    assign_ids(openings)
    return openings


def assign_ids(openings: list[Opening]) -> None:
    """F01, F02 (windows), P01 (doors), V01 (passages): reading order, top to bottom then left to right."""
    prefix = {"window": "F", "door": "P", "passage": "V"}
    count = {"window": 0, "door": 0, "passage": 0}
    for o in sorted(openings, key=lambda o: (-round(o.center[1], 1), o.center[0])):
        count[o.kind] += 1
        o.id = f"{prefix[o.kind]}{count[o.kind]:02d}"
