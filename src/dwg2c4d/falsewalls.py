"""What is not a wall on the layers that were taken for the walls: furniture, curbs, outlines of lots.

A layer the program chose (by its name, by the shape of its lines, or from a proposal) holds more than walls:
the closed outlines of benches and radiators, the curb of a road, the outline of a lot, a pergola. Extruded to the
height of the walls they ruin the model. Three rules, each one on evidence in the drawing and each one reported
(a layer the user named with ``--muri`` is never touched, and ``--muri-tutti`` turns all of this off):

1. Furniture. A small closed outline that touches no wall (no hatch, no long line, no bigger outline, no door or
   window) is a piece of furniture and is left out; a lone square of the size of a column is a column instead.
2. Masonry. When the walls of the drawing are marked with hatches, a body that lies outside the envelope of the
   hatched walls and carries no door or window is a curb, a lot line, a low retaining wall: a "basso".
3. Free-standing. In such a drawing a long thin outline that touches no other wall, no hatch, no opening, and encloses
   no room is a low wall too.

The low walls get their own groups (Muretti, Cordoli) with their own heights.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from .geom import polygons_of, union
from .reader import Item, Prim

FURNITURE_AREA = 3.0  # m2: a closed outline this small, touching no wall, is a piece of furniture
ANCHOR_LINE = 1.0  # m: a straight line this long on a wall layer is the edge of a wall (or a table): what touches it is attached
TOUCH = 0.03  # m: touching is being this close
MASONRY_THICKNESS = 0.6  # m: a hatch thinner than this is the fill of a wall, a bigger one is a floor or a paving
COLUMN_SIDE = (0.2, 0.8)  # m: the sides of a lone square that is a column
COLUMN_ASPECT = 1.3  # a lone outline whose sides differ by more than this is no column
ROW_REACH = 1.5  # m: free outlines this close to one another are a row of chairs or radiators, not columns
MIN_MASONRY = 0.05  # m2: a smaller hatch is a speck
MASONRY_ENVELOPE = 0.35  # the hatches are this share of the walls (or more): the drawing marks its walls with hatches...
MASONRY_FREE = 0.2  # ... and with this share it is a drawing that marks them with hatches for the free-standing rule
ENVELOPE_GAP = 3.0  # m: hatched walls up to twice this apart are one building (a closing across doors and open plans)
ENVELOPE_MARGIN = 0.4  # m: what is within this of the envelope is inside it
OPENING_TOUCH = 0.15  # m: a door or a window symbol this close to a body means it carries an opening
MIN_PIECE = 0.02  # m2: a piece of wall smaller than this is a crumb
MAX_LOW_SHARE = 0.45  # if more than this share of the walls would be lowered the hatches are not the evidence: nothing is
LOW_LENGTH = 3.0  # m: a free-standing outline shorter than this is no curb or wall: it stays as it is
THIN_OUTLINE = 0.4  # m: a free-standing outline thinner than this (2 * area / perimeter) is a line, not a building
ROOM_HOLE = 4.0  # m2: an outline that encloses a hole this big is a room, however thin its walls
CURB_THICKNESS = 0.25  # m: a low wall thinner than this is a curb (Cordoli), a thicker one a low wall (Muretti)


@dataclass
class Outline:
    """One thing that was left out or lowered, for the report: where (metres, drawing coordinates) and how big."""

    kind: str  # arredo | basso | pilastro
    x: float
    y: float
    width: float
    depth: float
    area: float


@dataclass
class FalseWalls:
    """What was taken out of the walls."""

    low: BaseGeometry = field(default_factory=Polygon)  # curbs, low walls: built low, apart from the walls
    columns: BaseGeometry = field(default_factory=Polygon)  # lone squares of the size of a column
    furniture: list[Outline] = field(default_factory=list)  # closed outlines left out
    lowered: list[Outline] = field(default_factory=list)  # the pieces of ``low``, one for each body

    @property
    def empty(self) -> bool:
        return not self.furniture and self.columns.is_empty and self.low.is_empty

    def notes(self, low_height: float, curb_height: float) -> list[str]:
        """The remarks for the report (Italian): how many things, how big, what was done, how to undo it."""
        out = []
        if self.furniture:
            area = sum(o.area for o in self.furniture)
            out.append(f"Muri: {len(self.furniture)} contorni chiusi piccoli ({area:.1f} m2 in tutto) che non toccano nessun "
                       "muro (panche, radiatori, sedute, mobili) non sono stati estrusi come muri: sono elencati nel file "
                       "del riepilogo. Se sono muri usa --muri-tutti.")
        if not self.columns.is_empty:
            n = len(polygons_of(self.columns))
            out.append(f"Muri: {n} quadrati isolati della misura di un pilastro sono diventati pilastri.")
        if self.lowered:
            area = sum(o.area for o in self.lowered)
            out.append(f"Muri: {len(self.lowered)} elementi senza campiture ne' aperture, isolati o fuori dall'edificio "
                       f"({area:.1f} m2: cordoli, muretti, contorni di lotti) sono alti {low_height:g} m (Muretti) o "
                       f"{curb_height:g} m (Cordoli, se sottili) invece dell'altezza dei muri: sono elencati nel file del "
                       "riepilogo. Se sono muri veri usa --muri-tutti.")
        return out


def _outline(kind: str, geom: BaseGeometry) -> Outline:
    x0, y0, x1, y1 = geom.bounds
    return Outline(kind, (x0 + x1) / 2.0, (y0 + y1) / 2.0, x1 - x0, y1 - y0, geom.area)


def _thickness(geom: BaseGeometry) -> float:
    """Average thickness: 2 * area / perimeter (a strip of width t gives t)."""
    return 2.0 * geom.area / geom.length if geom.length else 0.0


def _is_square(geom: BaseGeometry) -> bool:
    x0, y0, x1, y1 = geom.bounds
    w, h = x1 - x0, y1 - y0
    lo, hi = COLUMN_SIDE
    return lo <= w <= hi and lo <= h <= hi and max(w, h) <= COLUMN_ASPECT * min(w, h) and geom.area >= 0.4 * w * h


def _group(geoms: list[BaseGeometry], kind: str) -> list[Outline]:
    """One outline for each cluster of touching shapes (the nested outlines of one seat are one thing)."""
    if not geoms:
        return []
    return [_outline(kind, p) for p in polygons_of(union(g.buffer(TOUCH) for g in geoms))]


def drop_loose_outlines(items: list[Item]) -> tuple[list[Item], FalseWalls]:
    """The items without the closed outlines of furniture. A closed outline of at most ``FURNITURE_AREA`` that is
    not attached (within ``TOUCH`` of a hatch, a line of ``ANCHOR_LINE``, a bigger outline, a door or a window) is
    furniture; a lone square of the size of a column is a column. Outlines of one row (chairs side by side, the
    nested squares of a seat) are furniture, never columns."""
    anchors: list[BaseGeometry] = []
    small: list[tuple[int, int, BaseGeometry]] = []  # item, prim, geometry
    for i, it in enumerate(items):
        for j, p in enumerate(it.prims):
            if it.category in ("door", "window"):
                anchors.append(p.geom)
            elif it.category != "wall":
                continue
            elif p.kind == "fill":
                anchors.extend(f for f in polygons_of(p.geom) if _thickness(f) <= MASONRY_THICKNESS)
            elif p.kind == "line":
                if p.geom.length >= ANCHOR_LINE:
                    anchors.append(p.geom)
            elif p.kind == "ring":
                if p.geom.area > FURNITURE_AREA:
                    anchors.append(p.geom.boundary)
                elif not p.geom.is_empty:
                    small.append((i, j, p.geom))
    if not small:
        return items, FalseWalls()
    tree = STRtree(anchors) if anchors else None
    loose = [(i, j, g) for i, j, g in small
             if tree is None or len(tree.query(g, predicate="dwithin", distance=TOUCH)) == 0]
    if not loose:
        return items, FalseWalls()
    centres = [g.centroid for _, _, g in loose]
    columns: list[BaseGeometry] = []
    furniture: list[BaseGeometry] = []
    dropped: set[tuple[int, int]] = set()
    for (i, j, g), c in zip(loose, centres):
        alone = not any(c.distance(o) <= ROW_REACH for o, (_, _, other) in zip(centres, loose) if other is not g)
        (columns if alone and _is_square(g) else furniture).append(g)
        dropped.add((i, j))
    kept = []
    for i, it in enumerate(items):
        prims = [p for j, p in enumerate(it.prims) if (i, j) not in dropped]
        kept.append(it if len(prims) == len(it.prims) else Item(it.layer, it.block, it.category, prims))
    return kept, FalseWalls(columns=union(columns), furniture=_group(furniture, "arredo"))


def _masonry(items: list[Item]) -> BaseGeometry:
    """The hatches that fill walls: the fills of the wall layers thinner than ``MASONRY_THICKNESS``."""
    return union(f for it in items if it.category == "wall" for p in it.prims if p.kind == "fill"
                 for f in polygons_of(p.geom) if f.area >= MIN_MASONRY and _thickness(f) <= MASONRY_THICKNESS)


def _openings(items: list[Item]) -> BaseGeometry:
    return union(p.geom for it in items if it.category in ("door", "window") for p in it.prims).buffer(OPENING_TOUCH)


def _components(geom: BaseGeometry) -> list[BaseGeometry]:
    """The bodies of a shape: pieces that touch (within ``TOUCH``) are one."""
    parts = polygons_of(geom)
    out = []
    for cluster in polygons_of(union(p.buffer(TOUCH) for p in parts)):
        mine = [p for p in parts if p.intersects(cluster)]
        out.append(union(mine))
    return out


def lower_far_walls(walls: BaseGeometry, items: list[Item], false: FalseWalls) -> BaseGeometry:
    """The walls without what is not part of the building; ``false.low`` gets the curbs, lot lines and free-standing
    low walls. Only in a drawing whose walls are marked with hatches (see the module); a piece that carries a door
    or a window, or touches a hatch, is a wall whatever else it is."""
    masonry = _masonry(items)
    if walls.is_empty or masonry.area < MASONRY_FREE * walls.area:
        return walls
    openings = _openings(items)
    low: list[BaseGeometry] = []
    if masonry.area >= MASONRY_ENVELOPE * walls.area:
        closed = masonry.buffer(ENVELOPE_GAP, join_style="mitre").buffer(-ENVELOPE_GAP, join_style="mitre")
        envelope = union(Polygon(p.exterior) for p in polygons_of(closed)).buffer(ENVELOPE_MARGIN, join_style="mitre")
        outside = [p for p in polygons_of(walls.difference(envelope)) if p.area >= MIN_PIECE and not p.intersects(openings)]
        if sum(p.area for p in outside) <= MAX_LOW_SHARE * walls.area:
            low.extend(outside)
    rest = walls.difference(union(low)) if low else walls
    for body in _components(rest):
        holes = [Polygon(r).area for p in polygons_of(body) for r in p.interiors]
        x0, y0, x1, y1 = body.bounds
        free = not body.intersects(masonry) and not body.intersects(openings)
        if free and _thickness(body) <= THIN_OUTLINE and max(x1 - x0, y1 - y0) >= LOW_LENGTH \
                and not any(h >= ROOM_HOLE for h in holes):
            low.append(body)
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
