"""What is not a wall on the layers that were taken for the walls: furniture, stairs, curbs, outlines of lots.

A layer the program chose (by its name, by the shape of its lines, or from a proposal) holds more than walls:
the closed outlines of benches and radiators, the steps of a stair, the curb of a road, the outline of a lot, a ring
of stones. Extruded to the height of the walls they ruin the model. Five rules, each one on evidence in the drawing
and each one reported (a layer the user named with ``--muri``, or a wall mode he chose, is never touched, and
``--muri-tutti`` turns all of this off):

1. Furniture. A small group of closed outlines that touch one another and nothing else (no hatch, no long line, no
   bigger outline, no door or window) is a piece of furniture and is left out. A lone square of the size of a
   column, when the drawing has more of the same size (or a column layer with that size), is a column instead.
2. Stairs. A row of five or more equal parallel lines a step apart is the steps of a stair: drawn as lines they
   pair up into a solid block as high as the walls, so they are left out (the stair is not modelled).
3. Masonry. When the walls of the drawing are marked with hatches, a piece of wall that lies outside the envelope
   of the hatched walls and carries no door or window is a curb, a lot line, a low retaining wall: a "basso".
4. Free-standing. In a drawing with some hatches, a long thin body that touches no hatch, no opening, lies mostly
   outside the hatched building, and encloses no room is a low wall too.
5. Blocks. In such a drawing a part of the walls thicker than a wall that no hatch fills and no opening cuts (the
   landing of a stair, a plinth, the outline of a pergola) is a solid block, not a wall: it is a low one.

The low walls get their own groups (Muretti, Cordoli) with their own heights.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from .geom import polygons_of, union
from .reader import Item

TOUCH = 0.03  # m: touching is being this close
FURNITURE_AREA = 3.0  # m2: closed outlines that touch one another and cover this little together are furniture...
FURNITURE_SIDE = 2.5  # m: ... if they are not longer than this
ANCHOR_LINE = 1.0  # m: a straight line this long on a wall layer is the edge of a wall (or a table): what touches it is attached
MASONRY_THICKNESS = 0.6  # m: a hatch thinner than this is the fill of a wall, a bigger one is a floor or a paving
COLUMN_SIDE = (0.15, 0.8)  # m: the sides of a lone square that is a column
COLUMN_ASPECT = 1.3  # a lone outline whose sides differ by more than this is no column
COLUMN_TOLERANCE = 0.05  # m: squares whose sides differ by less than this are of one size
ROW_REACH = 1.5  # m: free outlines this close to one another are a row of chairs or radiators, not columns
MIN_MASONRY = 0.05  # m2: a smaller hatch is a speck
MASONRY_ENVELOPE = 0.35  # the hatches are this share of the walls (or more): the drawing marks its walls with hatches...
MASONRY_FREE = 0.2  # ... and with this share it is a drawing that marks them with hatches for the free-standing rule
ENVELOPE_GAP = 3.0  # m: hatched walls up to twice this apart are one building (a closing across doors and open plans)
ENVELOPE_MARGIN = 0.4  # m: what is within this of the envelope is inside it
OPENING_TOUCH = 0.15  # m: a door or a window symbol this close to a body means it carries an opening
MIN_PIECE = 0.02  # m2: a piece of wall smaller than this is a crumb
MAX_LOW_SHARE = 0.45  # if more than this share of the walls would be lowered the hatches are not the evidence: nothing is
LOW_LENGTH = 3.0  # m: a free-standing body shorter than this is no curb or wall: it stays as it is
THIN_OUTLINE = 0.4  # m: a free-standing body thinner than this (2 * area / perimeter) is a line, not a building
BLOCK_RADIUS = 0.4  # m: what no hatch fills and a circle of this radius fits in is a block, not a wall
MIN_BLOCK = 0.5  # m2: a block smaller than this is a crumb of the walls
ROOM_HOLE = (4.0, 300.0)  # m2: a body that encloses a hole this big is a room, however thin its walls (a bigger one is a lot)
CURB_THICKNESS = 0.25  # m: a low wall thinner than this is a curb (Cordoli), a thicker one a low wall (Muretti)
STEP_LENGTH = (0.6, 3.0)  # m: the length of the line of one step (the width of a stair)
STEP_PITCH = (0.15, 0.45)  # m: the distance between two steps, tread after tread
STEP_PITCH_SPREAD = 0.12  # share: the steps of one stair are equally far apart within this
STEP_ALIGN = 0.12  # m: the steps of one stair begin and end on the same two lines, within this
STEP_ANGLE = 1.0  # degrees: lines that differ by less than this are parallel
STEP_EDGE = 0.05  # m: what lies this close to the sides of a stair is its wall, not its steps
STEP_COPY = 0.02  # m: a line this close to a step, parallel to it, is the same step drawn twice
MIN_STEPS = 5  # a row of fewer lines than this may be anything (a window, a table): it is not taken for a stair
MAX_STEP_LINES = 20_000  # a drawing with more lines of step length than this is not searched for stairs


@dataclass
class Outline:
    """One thing that was left out or lowered, for the report: where (metres, drawing coordinates) and how big."""

    kind: str  # arredo | basso | scala
    x: float
    y: float
    width: float
    depth: float
    area: float
    steps: int = 0  # for a stair: how many steps were counted
    geom: BaseGeometry | None = None  # for a stair: its footprint (metres), so that the steps can be built (sitesurfaces)
    run: tuple[float, float] = (0.0, 0.0)  # for a stair: the unit vector along which its steps follow one another (nearest first)
    pitch: float = 0.0  # for a stair: the distance between two steps (the tread), metres


@dataclass
class FalseWalls:
    """What was taken out of the walls."""

    low: BaseGeometry = field(default_factory=Polygon)  # curbs, low walls: built low, apart from the walls
    columns: BaseGeometry = field(default_factory=Polygon)  # lone squares of the size of a column
    furniture: list[Outline] = field(default_factory=list)  # closed outlines left out
    lowered: list[Outline] = field(default_factory=list)  # the pieces of ``low``, one for each body
    stairs: list[Outline] = field(default_factory=list)  # rows of steps left out

    @property
    def empty(self) -> bool:
        return not (self.furniture or self.lowered or self.stairs) and self.columns.is_empty

    def notes(self, low_height: float, curb_height: float) -> list[str]:
        """The remarks for the report (Italian): how many things, how big, what was done, how to undo it."""
        out = []
        if self.furniture:
            area = sum(o.area for o in self.furniture)
            out.append(f"Muri: {len(self.furniture)} gruppi di contorni chiusi piccoli ({area:.1f} m2 in tutto) che non toccano "
                       "nessun muro (panche, sedie, radiatori, mobili) non sono stati estrusi come muri: le posizioni sono nel "
                       "file *_report.txt. Se sono muri usa --muri-tutti.")
        if not self.columns.is_empty:
            n = len(polygons_of(self.columns))
            out.append(f"Muri: {n} quadrati isolati della misura di un pilastro, ripetuti nel disegno, sono diventati pilastri: "
                       "controlla.")
        if self.stairs:
            steps = sum(o.steps for o in self.stairs)
            out.append(f"Muri: {len(self.stairs)} scale ({steps} gradini disegnati come linee parallele) non sono state estruse "
                       "come blocchi pieni alti come i muri: la scala non e' modellata, restano i muri che la fiancheggiano. "
                       "Posizioni nel file *_report.txt. Se sono muri usa --muri-tutti.")
        if self.lowered:
            area = sum(o.area for o in self.lowered)
            out.append(f"Muri: {len(self.lowered)} elementi senza campiture ne' aperture, isolati, fuori dall'edificio o spessi "
                       f"come una massa piena ({area:.1f} m2: cordoli, muretti, contorni di lotti, pianerottoli) sono alti {low_height:g} m (Muretti) o "
                       f"{curb_height:g} m (Cordoli, se sottili) invece dell'altezza dei muri: posizioni nel file "
                       "*_report.txt. Se sono muri veri usa --muri-tutti (o --altezza-muretti / --altezza-cordoli).")
        return out

    def listing(self, unit_scale: float, unit: str) -> list[str]:
        """One line for each thing left out or lowered, with its place in the units of the drawing."""
        what = {"arredo": "arredo (contorni chiusi non estrusi)", "basso": "basso (muretto o cordolo)",
                "scala": "scala (gradini non estrusi)", "pilastro": "pilastro (quadrato isolato)"}
        rows = [*self.furniture, *self.lowered, *self.stairs, *(_outline("pilastro", c) for c in polygons_of(self.columns))]
        out = []
        for o in rows:
            steps = f", {o.steps} gradini" if o.steps else ""
            out.append(f"  {what[o.kind]}: x {o.x / unit_scale:.1f}  y {o.y / unit_scale:.1f} ({unit}), "
                       f"{o.width:.2f} x {o.depth:.2f} m, {o.area:.2f} m2{steps}")
        return out


def _outline(kind: str, geom: BaseGeometry, steps: int = 0) -> Outline:
    x0, y0, x1, y1 = geom.bounds
    return Outline(kind, (x0 + x1) / 2.0, (y0 + y1) / 2.0, x1 - x0, y1 - y0, geom.area, steps)


def _thickness(geom: BaseGeometry) -> float:
    """Average thickness: 2 * area / perimeter (a strip of width t gives t)."""
    return 2.0 * geom.area / geom.length if geom.length else 0.0


def _touching(geoms: list[BaseGeometry]) -> list[list[int]]:
    """The indices of ``geoms`` grouped by touching (within ``TOUCH``, a chain counts): the connected pieces of a drawing."""
    parent = list(range(len(geoms)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    if len(geoms) > 1:
        first, second = STRtree(geoms).query(geoms, predicate="dwithin", distance=TOUCH)
        for i, j in zip(first.tolist(), second.tolist()):
            parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(geoms)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _components(geom: BaseGeometry) -> list[BaseGeometry]:
    """The bodies of a shape: pieces that touch (within ``TOUCH``) are one."""
    parts = polygons_of(geom)
    return [union(parts[i] for i in group) for group in _touching(parts)]


def _without(items: list[Item], dropped: set[tuple[int, int]]) -> list[Item]:
    """The items without the primitives (item, primitive) in ``dropped``."""
    kept = []
    for i, it in enumerate(items):
        prims = [p for j, p in enumerate(it.prims) if (i, j) not in dropped]
        kept.append(it if len(prims) == len(it.prims) else Item(it.layer, it.block, it.category, prims))
    return kept


def _is_square(geom: BaseGeometry) -> bool:
    x0, y0, x1, y1 = geom.bounds
    w, h = x1 - x0, y1 - y0
    lo, hi = COLUMN_SIDE
    return lo <= w <= hi and lo <= h <= hi and max(w, h) <= COLUMN_ASPECT * min(w, h) and geom.area >= 0.4 * w * h


def _side(geom: BaseGeometry) -> float:
    x0, y0, x1, y1 = geom.bounds
    return (x1 - x0 + y1 - y0) / 2.0


def _column_sides(items: list[Item]) -> list[float]:
    """The sides of the squares the drawing keeps on its column layers."""
    return [_side(p.geom) for it in items if it.category == "column" for p in it.prims
            if p.kind in ("ring", "fill") and _is_square(p.geom)]


def _group(geoms: list[BaseGeometry], kind: str) -> list[Outline]:
    """One outline for each cluster of shapes that are close (rows of chairs are one thing for the report)."""
    if not geoms:
        return []
    return [_outline(kind, p) for p in polygons_of(union(g.buffer(TOUCH) for g in geoms))]


def drop_loose_outlines(items: list[Item], exempt: frozenset[str] = frozenset()) -> tuple[list[Item], FalseWalls]:
    """The items without the furniture: a group of closed outlines (and the short lines on them) that touch one another
    but no hatch, no long line, no door or window, no bigger outline: at most ``FURNITURE_AREA`` together and
    ``FURNITURE_SIDE`` long. A lone square of the size of a column is kept as a column when the drawing has more squares
    of that size, loose or on a column layer; a lone one may be a stool. ``exempt``: layers whose walls the names
    vouch for (partitions): nothing attached to them is loose."""
    geoms: list[BaseGeometry] = []
    kinds: list[str] = []  # ring | line | anchor
    ref: list[tuple[int, int] | None] = []
    for i, it in enumerate(items):
        for j, p in enumerate(it.prims):
            if p.geom.is_empty:
                continue
            if it.category in ("door", "window") or it.layer in exempt:
                geoms.append(p.geom)
                kinds.append("anchor")
                ref.append(None)
            elif it.category != "wall":
                continue
            elif p.kind == "fill":
                thin = [f for f in polygons_of(p.geom) if _thickness(f) <= MASONRY_THICKNESS]
                if thin:  # the hatch of a wall; a floor or a paving is neither a wall nor a thing to attach to
                    geoms.append(union(thin))
                    kinds.append("anchor")
                    ref.append(None)
            elif p.kind == "line" and p.geom.length >= ANCHOR_LINE:
                geoms.append(p.geom)
                kinds.append("anchor")
                ref.append(None)
            elif p.kind == "ring" and p.geom.area > FURNITURE_AREA:  # a room, a building: what is in it does not touch it
                geoms.append(p.geom.boundary)
                kinds.append("anchor")
                ref.append(None)
            else:
                geoms.append(p.geom)
                kinds.append(p.kind)
                ref.append((i, j))
    loose: list[tuple[list[int], BaseGeometry]] = []  # prims of the group, its outline
    for group in _touching(geoms):
        if any(kinds[k] == "anchor" for k in group) or not any(kinds[k] == "ring" for k in group):
            continue
        shape = union(geoms[k] for k in group)
        x0, y0, x1, y1 = shape.bounds
        rings = union(geoms[k] for k in group if kinds[k] == "ring")
        if rings.area <= FURNITURE_AREA and max(x1 - x0, y1 - y0) <= FURNITURE_SIDE:
            loose.append((group, rings))
    if not loose:
        return items, FalseWalls()
    centres = [g.centroid for _, g in loose]
    squares = [k for k, (_, g) in enumerate(loose) if _is_square(g)
               and not any(centres[k].distance(c) <= ROW_REACH for m, c in enumerate(centres) if m != k)]
    sizes = [_side(loose[k][1]) for k in squares] + _column_sides(items)
    columns = [loose[k][1] for k in squares
               if sum(abs(s - _side(loose[k][1])) <= COLUMN_TOLERANCE for s in sizes) >= 2]  # itself and another one
    taken = {id(g) for g in columns}
    furniture = [g for _, g in loose if id(g) not in taken]
    dropped = {ref[k] for group, _ in loose for k in group if ref[k] is not None}
    return _without(items, dropped), FalseWalls(columns=union(columns), furniture=_group(furniture, "arredo"))


def _step_chains(seg: np.ndarray) -> list[list[int]]:
    """Rows of ``MIN_STEPS`` or more parallel pieces of one length, on the same two lines, equally far apart: the steps of a
    stair. ``seg``: (n, 4) array x0, y0, x1, y1. Each row is a list of indices into ``seg``, nearest step first."""
    dx, dy = seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1]
    length = np.hypot(dx, dy)
    theta = np.degrees(np.arctan2(dy, dx)) % 180.0
    mx, my = (seg[:, 0] + seg[:, 2]) / 2.0, (seg[:, 1] + seg[:, 3]) / 2.0
    bins = (theta // STEP_ANGLE).astype(int) % int(180 / STEP_ANGLE)
    used = np.zeros(len(seg), bool)
    chains: list[list[int]] = []
    for b in np.unique(bins):
        near = np.isin(bins, [(b - 1) % int(180 / STEP_ANGLE), b, (b + 1) % int(180 / STEP_ANGLE)])
        pool = np.flatnonzero(near)
        rad = math.radians((b + 0.5) * STEP_ANGLE)
        ux, uy = math.cos(rad), math.sin(rad)
        across = -mx[pool] * uy + my[pool] * ux
        along = mx[pool] * ux + my[pool] * uy
        order = np.argsort(across)
        pool, across, along = pool[order], across[order], along[order]
        for k in np.flatnonzero(bins[pool] == b):
            if used[pool[k]]:
                continue
            same = (np.abs(((theta[pool] - theta[pool[k]] + 90.0) % 180.0) - 90.0) <= STEP_ANGLE) \
                & (np.abs(length[pool] - length[pool[k]]) <= 0.1 * length[pool[k]]) \
                & (np.abs(along - along[k]) <= STEP_ALIGN) & ~used[pool]
            chain, last, pitch = [k], k, None
            while True:
                gap = across - across[last]
                lo, hi = STEP_PITCH if pitch is None else (pitch * (1 - STEP_PITCH_SPREAD), pitch * (1 + STEP_PITCH_SPREAD))
                ahead = np.flatnonzero(same & (gap >= lo) & (gap <= hi))
                if not len(ahead):
                    break
                nxt = int(ahead[np.argmin(gap[ahead])])
                pitch = gap[nxt] if pitch is None else pitch
                chain.append(nxt)
                last = nxt
            if len(chain) < MIN_STEPS:
                continue
            steps = across[chain]  # ascending, like the row
            at = np.searchsorted(steps, across)
            gap = np.minimum(np.abs(across - steps[np.clip(at - 1, 0, len(steps) - 1)]),
                             np.abs(across - steps[np.clip(at, 0, len(steps) - 1)]))
            members = sorted(set(chain) | set(np.flatnonzero(same & (gap <= STEP_COPY)).tolist()))
            used[pool[members]] = True
            chains.append([int(pool[m]) for m in members])
    return chains


def _stair_footprint(seg: np.ndarray) -> tuple[Polygon, tuple[float, float], float]:
    """(the footprint of a stair, the unit vector along which its steps follow one another, the distance between two steps)
    from the lines of its steps (``seg``: (n, 4), nearest step first): the width of the steps, less ``STEP_EDGE`` at each side
    (the walls it lies between are not part of it), and the length of the row, one step more at each end (the first riser and
    the landing), give or take the drawing of a line twice."""
    ux, uy = seg[0, 2] - seg[0, 0], seg[0, 3] - seg[0, 1]
    norm = math.hypot(ux, uy)
    ux, uy = ux / norm, uy / norm
    nx, ny = -uy, ux
    ends = seg.reshape(-1, 2)
    along = ends[:, 0] * ux + ends[:, 1] * uy
    across = (seg[:, [0, 2]] * nx + seg[:, [1, 3]] * ny).mean(axis=1)
    pitch = (across.max() - across.min()) / (len(seg) - 1)
    s0, s1 = along.min() + STEP_EDGE, along.max() - STEP_EDGE
    c0, c1 = across.min() - pitch - STEP_COPY, across.max() + pitch + STEP_COPY
    shape = Polygon([(s * ux + c * nx, s * uy + c * ny) for s, c in ((s0, c0), (s1, c0), (s1, c1), (s0, c1))])
    return shape, (nx, ny), float(pitch)


def drop_stair_steps(items: list[Item], false: FalseWalls, exempt: frozenset[str] = frozenset()) -> list[Item]:
    """The items without the steps of stairs (rows of equal parallel lines a step apart) and the lines and outlines lying
    between them (the rails, the strip between two lines that pairs up into a block); the stairs found are added to
    ``false.stairs``. Only the lines of two points on the wall layers are taken for steps."""
    refs: list[tuple[int, int]] = []  # every line and outline of the wall layers
    geoms: list[BaseGeometry] = []
    steps: list[int] = []  # the index in refs of each candidate step
    lo, hi = STEP_LENGTH
    for i, it in enumerate(items):
        if it.category != "wall" or it.layer in exempt:
            continue
        for j, p in enumerate(it.prims):
            if p.kind not in ("line", "ring") or p.geom.is_empty:
                continue
            if p.kind == "line" and p.geom.geom_type == "LineString" and len(p.geom.coords) == 2 and lo <= p.geom.length <= hi:
                steps.append(len(refs))
            refs.append((i, j))
            geoms.append(p.geom)
    if len(steps) < MIN_STEPS or len(steps) > MAX_STEP_LINES:
        return items
    seg = np.asarray([[*geoms[k].coords[0], *geoms[k].coords[1]] for k in steps], float)
    chains = _step_chains(seg)
    if not chains:
        return items
    tree = STRtree(geoms)
    dropped: set[tuple[int, int]] = set()
    for chain in chains:
        footprint, run, pitch = _stair_footprint(seg[chain])
        dropped.update(refs[steps[k]] for k in chain)
        dropped.update(refs[k] for k in tree.query(footprint, predicate="contains").tolist())
        x0, y0, x1, y1 = footprint.bounds
        false.stairs.append(Outline("scala", (x0 + x1) / 2.0, (y0 + y1) / 2.0, x1 - x0, y1 - y0, footprint.area, len(chain),
                                    footprint, run, pitch))
    return _without(items, dropped)


def separate_prims(items: list[Item], exempt: frozenset[str] = frozenset()) -> tuple[list[Item], FalseWalls]:
    """The items without what is surely no wall before the walls are built: furniture, stairs (see the module)."""
    items, false = drop_loose_outlines(items, exempt)
    return drop_stair_steps(items, false, exempt), false


def _masonry(items: list[Item]) -> BaseGeometry:
    """The hatches that fill walls: the fills of the wall layers thinner than ``MASONRY_THICKNESS``."""
    return union(f for it in items if it.category == "wall" for p in it.prims if p.kind == "fill"
                 for f in polygons_of(p.geom) if f.area >= MIN_MASONRY and _thickness(f) <= MASONRY_THICKNESS)


def _openings(items: list[Item]) -> BaseGeometry:
    return union(p.geom for it in items if it.category in ("door", "window") for p in it.prims).buffer(OPENING_TOUCH)


def lower_far_walls(walls: BaseGeometry, items: list[Item], false: FalseWalls,
                    keep: BaseGeometry | None = None, site: bool = False) -> BaseGeometry:
    """The walls without what is not part of the building; ``false.low`` gets the curbs, lot lines and free-standing
    low walls. Only in a drawing whose walls are marked with hatches (see the module); a piece that carries a door
    or a window, or touches a hatch, is a wall whatever else it is. ``keep``: what the layer names vouch for.
    ``site``: the drawing is a site plan (the user asked for it as it is): there the hatches are not needed, what
    lies in the open and carries no opening is a road edge, a boundary, a retaining wall."""
    masonry = _masonry(items)
    if walls.is_empty or (masonry.area < MASONRY_FREE * walls.area and not site):
        return walls
    vouched = union([_openings(items), keep if keep is not None else Polygon()])
    closed = masonry.buffer(ENVELOPE_GAP, join_style="mitre").buffer(-ENVELOPE_GAP, join_style="mitre")
    envelope = union(Polygon(p.exterior) for p in polygons_of(closed)).buffer(ENVELOPE_MARGIN, join_style="mitre")
    low: list[BaseGeometry] = []
    if masonry.area >= MASONRY_ENVELOPE * walls.area:
        outside = [p for p in polygons_of(walls.difference(envelope)) if p.area >= MIN_PIECE and not p.intersects(vouched)]
        if sum(p.area for p in outside) <= MAX_LOW_SHARE * walls.area:
            low.extend(outside)
    rest = walls.difference(union(low)) if low else walls
    for body in _components(rest):
        holes = [Polygon(r).area for p in polygons_of(body) for r in p.interiors]
        x0, y0, x1, y1 = body.bounds
        free = not body.intersects(masonry) and not body.intersects(vouched) and body.intersection(envelope).area < 0.5 * body.area
        if free and _thickness(body) <= THIN_OUTLINE and max(x1 - x0, y1 - y0) >= LOW_LENGTH \
                and not any(ROOM_HOLE[0] <= h <= ROOM_HOLE[1] for h in holes):
            low.append(body)
    bare = (walls.difference(union(low)) if low else walls).difference(masonry.buffer(TOUCH))
    low.extend(p for p in polygons_of(bare.buffer(-BLOCK_RADIUS, join_style="mitre").buffer(BLOCK_RADIUS, join_style="mitre"))
               if p.area >= MIN_BLOCK and not p.intersects(vouched))
    if not low:
        return walls
    lowered = union(low)
    false.low = lowered
    false.lowered = [_outline("basso", b) for b in _components(lowered)]
    return walls.difference(lowered.buffer(TOUCH / 3.0))


def split_low(low: BaseGeometry) -> tuple[BaseGeometry, BaseGeometry]:
    """(thin ones: curbs, thicker ones: low walls) of the low bodies."""
    thin, thick = [], []
    for body in _components(low):
        (thin if _thickness(body) <= CURB_THICKNESS else thick).append(body)
    return union(thin), union(thick)
