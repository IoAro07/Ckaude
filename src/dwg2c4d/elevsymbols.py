"""Doors and windows of an elevation that names none of them, and the floor level they stand on.

The elevations of a real sheet draw their openings on layer 0 or on a layer called "Quote", as loose lines forming
rectangles, as a frame with panes and shutters inside, as an arch, as a hatch for the glass. A person recognises them by
their shape and by where they stand; so does this module:

* the faces of the linework (and the closed polylines, which a crossing railing cannot cut) that are rectangles or
  arches of a door or window size are the candidates; a frame drawn inside another one collapses to the outermost;
* leaves that touch (the two halves of a door, the panels of an entrance, the sashes between two shutters) are one
  symbol; shutters folded against the wall are told from the sashes and left out of the clear width;
* what is not an opening (a strip of fascia, a bay of the wall between two pilasters, a planter on the ground line,
  a title frame) is rejected by its proportions and by how it is drawn;
* the floor comes from a level mark ("+0,00", "P.P.F. +0.00") and the line it labels, else from the bottom of the
  lowest door, the top of the ground line, or the foot of a person figure; the other storeys from their marks.

Layers that do name doors and windows stay the primary answer for those symbols: the shapes complete them (a
window drawn as its inner pane only becomes the whole frame) and add the openings the names do not give.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import shapely
from ezdxf import bbox as ezbbox
from ezdxf.document import Drawing
from shapely.geometry import LineString, Polygon, box
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

from .config import Config
from .elevation import MIN_LINE, Symbol
from .openings import _symbols
from .reader import Item, read_items
from .texts import RawText, read_texts

PART_MIN = 0.15  # m: a rectangle with a smaller side is a bar, a mullion or a moulding, never a leaf
SYMBOL_MAX = (6.8, 4.2)  # m: widest and tallest door or window (a sectional door with its piers, a portal and its steps)
SYMBOL_MIN = (0.35, 0.35)  # m: smallest door or window (a cellar light)
RECT_FILL = 0.97  # a face is a rectangle if it fills this share of its bounding box
ARCH_FILL = 0.80  # ... an arch if it fills at least this much of it (a semicircle on a square: 0.93)
CLIP_FILL = 0.85  # a polygon filling this much of its box, with straight sides and ...
CLIP_CORNERS = 6  # ... no more corners than this, is a rectangle with a corner or two cut off
ARCH_BASE = 0.95  # an arch is as wide as its box in the lowest third ...
ARCH_TOP = 0.8  # ... and narrower than this share of it at the apex, which is in the middle (a stair cuts a corner)
ARCH_CENTRED = 0.1  # the apex is this close to the middle of the box, as a share of its width
NEST_TOL = 0.015  # m: a rectangle this close to the edges of another one lies inside it
FRAME_BAND = 0.30  # m: a frame is no wider than this around what it holds (more: it is a bay of the wall)
MERGE_GAP = 0.12  # m: leaves this close are one symbol (the halves of a door, the panels of an entrance)
ALIGN_TOL = 0.10  # m: leaves of one symbol line up at the top or at the bottom (or at both sides), within this
NODE_TOL = 0.005  # m: lines ending this close to a corner meet there
MIN_FREE_CORNERS = 2  # a real opening is outlined by its own lines: at least this many of its 4 corners are bare
BAY_MIN = 1.0  # m: an empty rectangle this wide and tall, with fewer bare corners, is a bay of the wall; the panes of a
#                window with mullions have lines that go on at every corner too, but they are narrow
STRUCTURE_MIN = 4  # a symbol of at least this many leaves is an opening even if its corners are not bare (a frame with a
#                    frame inside is one too)
GLAZED_MIN = 2  # a leaf divided in at least this many panes is a sash; a plain one is a shutter
CAP_GAP = 0.05  # m: the faces of an arch's head start this close to the top of the frame ...
CAP_RISE = 0.55  # ... rise no more than this share of its width and together span it
CAP_CURVE = 0.01  # m: a curved side keeps at least 5 corners when simplified this much; a gable keeps 3
CAP_FILL = 0.55  # the faces of an arch's head fill this share of the box they span (a segment of a circle: 0.67 to 0.79)
CAP_MIDDLE = 0.1  # the head rises at least this share of the width in the middle ...
CAP_SIDES = 0.5  # ... and at a quarter of the width at least this share of that
FLOOR_DOORS = 1 / 3  # doors at one level count for the floor if they are at least this share of the doors at the busiest
JAMB_SHARE = 0.25  # a plain piece beside the sashes narrower than this share of the width left is a jamb or a pier
MAX_ASPECT = 3.5  # a symbol wider than this (width / height) is a strip: fascia, canopy, step ...
MAX_TALL = 5.6  # ... and one taller than this (height / width) is a post, a pilaster or a downpipe
DOOR_HEIGHT = 1.6  # m: a symbol this tall that stands on a floor is a door
DOOR_HEIGHT_UNKNOWN = 1.9  # m: ... and when the floor is not known, only a taller one is taken for a door
DOOR_SILL = 0.30  # m: standing on a floor is having the bottom at most this far above it
STEPS_MAX = 1.2  # m: a door this high above the floor can stand on the steps of an entrance
STEP_GAP = 0.08  # m: the steps of a stair are this close to one another ...
STEP_THRESHOLD = 0.3  # m: ... and the top one this close under the door (the threshold)
LOW_ON_FLOOR = 0.05  # m: a window whose bottom is this close to the floor (or lower) and ...
LOW_HEIGHT = 1.3  # m: ... no taller than this is a planter, a step or a vent

FIGURE_HEIGHT = (1.5, 2.0)  # m: height of a person drawn in an elevation
FIGURE_WIDTH = 1.0  # m: at most this wide
FIGURE_FOOT = 0.10  # m: the insertion point of such a block is this close to its bottom, the feet

SKY = 60.0  # m: how far above a symbol the drawing is searched for a roof
ROOF_SPAN = 2.0  # m: a roof, an eave or the top of a wall runs at least this far sideways; the cap of a chimney does not
MARK_RE = re.compile(r"(?<![\w.,])([+\-\u00b1\u2212])\s*(\d{1,3})\s*[.,]\s*(\d{1,3})(?!\d)")  # "+0,00" "- 0.40" "+-0.00"
MARK_MAX = 30.0  # m: a level mark is no more than this above or below the floor
MARK_REACH = 3.2  # text heights: the line a level mark labels lies at most this far below the text
MARK_SIDE = 0.5  # m: ... and no further than this beyond the ends of the text, sideways
MARK_LINE_MIN = 0.15  # m: shortest horizontal line a level mark may label (its own tick)
MARK_SNAP = 0.10  # m: a line much longer than the tick under the text, this close below it, is the level
TRIANGLE_MAX = 0.6  # m: the little triangle of a level mark is no bigger than this
ARROW_MAX = 0.3  # m: a filled triangle this small is the arrowhead of a dimension line
ARROW_TOL = 0.02  # m: the tip of an arrowhead is this close to the corner it points at
HORIZONTAL_TOL = 1e-3  # m: a line is horizontal if its ends differ in y by less than this
ROW_TOL = 0.005  # m: horizontal lines this close in y are on the same row
JOIN_GAP = 0.02  # m: pieces of a horizontal line closer than this are one line
GROUND_SHARE = 0.4  # the ground line is at least this share of the width of the view
GROUND_DOUBLE = 0.25  # m: a second line this close above the ground line makes it a double one (the floor is the top)
FLOOR_TOL = 0.15  # m: floors found by two sources agree within this
FLOOR_VOTE = 0.04  # m: marks whose floors differ by less than this say the same
FLOOR_SNAP = 0.08  # m: a floor from a mark is moved onto a long line this close to it (the slab the mark stands on)
STOREY_MIN = 1.8  # m: a mark this far above the floor is the floor of another storey
SLAB_SHARE = 0.3  # a line under the foot of an upper door is a slab if it is this share of the width of the view
SLAB_DEPTH = 0.08  # m: ... and lies this close under the foot
SAME_SYMBOL = (1 / 3, 3.0)  # a named symbol and a shape are the same opening if their areas are this close
SAME_OVERLAP = 0.5  # ... and the smaller one lies this much within the other
INSIDE = 0.9  # a named symbol lying this much within a shape is a part of it
CUT_LINE = (0.2, 0.3)  # m: a symbol the layers name that is narrower or lower than this is a cut mark, not an opening
FLOOR_FROM = {  # floor_source -> what to say in the notes
    "quota +0,00": "dal segno di quota",
    "porta": "dal fondo della porta piu' bassa",
    "linea di terra": "dalla sommita' della linea di terra",
    "blocco figura": "dai piedi della figura umana",
}


@dataclass
class FoundSymbol(Symbol):
    """A door or window with what the drawing says besides its clear size (x0..x1 without folded shutters)."""

    source: str = "forma"  # "forma" (found by its shape) | "layer" (the layer name said so) | "layer+forma"
    arched: bool = False  # the top is an arch: y1 is the apex
    pane: tuple[float, float, float, float] | None = None  # (x0, y0, x1, y1) of what the frame holds: the glass
    full_x: tuple[float, float] | None = None  # (x0, x1) with the shutters folded beside it, when there are some


@dataclass
class ViewSymbols:
    """What an elevation tells about its openings. Drawing coordinates, metres, y up."""

    symbols: list[Symbol] = field(default_factory=list)
    floor: float | None = None  # y of the finished floor of the ground storey
    floor_source: str = ""  # "porta" | "quota +0,00" | "linea di terra" | "blocco figura" | ""
    levels: list[float] = field(default_factory=list)  # y of the floors of the storeys, ascending, ground one included
    hlines: list[tuple[float, float, float]] = field(default_factory=list)  # (y, x0, x1): silhouette of the roof
    notes: list[str] = field(default_factory=list)


@dataclass
class _Box:
    """A rectangle (or arch) of the drawing and the rectangles directly inside it."""

    x0: float
    y0: float
    x1: float
    y1: float
    arched: bool = False
    free: int = 4  # bare corners
    headed: bool = False  # an arched head is drawn over it
    kids: list["_Box"] = field(default_factory=list)

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0


@dataclass
class _Group:
    """Leaves that make one door or window."""

    members: list[_Box]
    x0: float
    y0: float
    x1: float
    y1: float
    arched: bool = False
    core: tuple[float, float] = (0.0, 0.0)  # x range without the shutters
    pane: tuple[float, float, float, float] | None = None
    free: int = 4  # bare corners of the whole symbol


@dataclass
class _Linework:
    """The lines of the view and what they enclose."""

    lines: list[LineString]  # cut to the view
    faces: list[Polygon]  # the faces of the planar graph of the lines
    outlines: list[Polygon]  # closed polylines and hatches: complete even where other lines cross them
    nodes: Counter  # where lines end, by rounded position: a corner with more than two ends is a junction
    arrows: list[tuple[float, float]]  # corners of the filled triangles that are arrowheads of dimension lines


# --- openings from the shapes ---------------------------------------------------------------------------------

def _lines(items: list[Item], area: tuple[float, float, float, float]) -> list[LineString]:
    """All linework of the items (outlines of rings included), cut to the view: what lies beside it is another drawing."""
    out: list[LineString] = []
    for it in items:
        for p in it.prims:
            if p.kind == "fill":
                continue
            for g in getattr(p.geom, "geoms", [p.geom]):
                if g.geom_type == "Polygon":
                    out.append(LineString(g.exterior.coords))
                    out.extend(LineString(r.coords) for r in g.interiors)
                elif g.geom_type == "LineString" and g.length > 0:
                    out.append(g)
    cut = [shapely.clip_by_rect(g, *area) for g in out]
    return [g for g in cut if not g.is_empty]


def _linework(items: list[Item], area: tuple[float, float, float, float]) -> _Linework:
    lines = _lines(items, area)
    outlines = [g for it in items for p in it.prims for g in getattr(p.geom, "geoms", [p.geom])
                if g.geom_type == "Polygon"]
    arrows = [(x, y) for g in outlines if len({(round(x, 3), round(y, 3)) for x, y in g.exterior.coords}) == 3
              and max(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1]) <= ARROW_MAX for x, y in g.exterior.coords]
    if not lines:
        return _Linework([], [], outlines, Counter(), arrows)
    noded = unary_union(lines)
    pieces = list(getattr(noded, "geoms", [noded]))
    ends = np.vstack([shapely.get_coordinates(shapely.get_point(pieces, i)) for i in (0, -1)])
    nodes = Counter(zip(np.round(ends[:, 0] / NODE_TOL).astype(int).tolist(),
                        np.round(ends[:, 1] / NODE_TOL).astype(int).tolist()))
    return _Linework(lines, list(polygonize(noded)), outlines, nodes, arrows)


def _bare(x: float, y: float, nodes: Counter) -> bool:
    """A corner where no more than its own two sides meet (no line goes on past it)."""
    i, j = round(x / NODE_TOL), round(y / NODE_TOL)
    return sum(nodes.get((i + di, j + dj), 0) for di in (-1, 0, 1) for dj in (-1, 0, 1)) <= 2


def _bare_corners(x0: float, y0: float, x1: float, y1: float, nodes: Counter) -> int:
    """How many of the 4 corners of a rectangle are not a junction with a line that goes on past them."""
    return sum(_bare(x, y, nodes) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)))


def _is_strip(b: _Box) -> bool:
    """Too long and thin to be the leaf of a window or door: a fascia, a step, a plinth, a pilaster."""
    return b.w / b.h > MAX_ASPECT or b.h / b.w > MAX_TALL


def _is_arch(poly: Polygon, w: float, h: float) -> bool:
    """A rectangle at the bottom with a narrower top: the head of an arched door or window."""
    x0, y0, x1, y1 = poly.bounds
    low = poly.intersection(box(x0 - 1, y0, x1 + 1, y0 + h / 3))
    top = poly.intersection(box(x0 - 1, y1 - 0.06 * h, x1 + 1, y1))
    if low.is_empty or top.is_empty:
        return False
    t0, t1 = top.bounds[0], top.bounds[2]
    return low.bounds[2] - low.bounds[0] >= ARCH_BASE * w and t1 - t0 <= ARCH_TOP * w \
        and abs((t0 + t1) / 2 - (x0 + x1) / 2) <= ARCH_CENTRED * w


def _box_of(poly: Polygon, drawn: bool) -> _Box | None:
    """The rectangle or arch of this outline, if it is one of a size a leaf can have. ``drawn``: the outline is a closed
    polyline of the drawing, not a face between lines (which a railing or a stair in front cuts anywhere)."""
    x0, y0, x1, y1 = poly.bounds
    w, h = x1 - x0, y1 - y0
    if min(w, h) < PART_MIN or w > SYMBOL_MAX[0] or h > SYMBOL_MAX[1]:
        return None
    outline = Polygon(poly.exterior)
    fill = outline.area / (w * h)
    if fill >= RECT_FILL:
        return _Box(x0, y0, x1, y1)
    if fill >= ARCH_FILL and _is_arch(outline, w, h):
        return _Box(x0, y0, x1, y1, arched=True)
    if drawn and fill >= CLIP_FILL and len(outline.simplify(CAP_CURVE).exterior.coords) <= CLIP_CORNERS + 1:
        return _Box(x0, y0, x1, y1)  # a leaf trimmed by the roof or the wall behind it: a corner or two cut off
    return None


Head = tuple[float, float, float, float, Polygon]  # x0, y0, x1, y1 and the shape of a face with a curved side


def _heads(lw: _Linework) -> list[Head]:
    """The faces that are not rectangles and have a curved side: the segments between an arc and its chord."""
    heads = []
    for f in lw.faces:
        x0, y0, x1, y1 = f.bounds
        outline = Polygon(f.exterior)
        if x1 - x0 >= PART_MIN / 2 and y1 - y0 > 0 and outline.area < RECT_FILL * (x1 - x0) * (y1 - y0) \
                and len(outline.simplify(CAP_CURVE).exterior.coords) > 5:
            heads.append((x0, y0, x1, y1, outline))
    return heads


def _apex(x0: float, x1: float, top: float, heads: list[Head]) -> float | None:
    """The apex of the arched head over a frame spanning x0..x1 whose top is ``top``: curved faces that start at the
    top of the frame, rise no more than CAP_RISE of its width, together run from one top corner to the other and fill
    a cap that is highest in the middle (not the scattered loops of a vine along a beam)."""
    width = x1 - x0
    mine = sorted(h for h in heads if h[0] >= x0 - ALIGN_TOL and h[2] <= x1 + ALIGN_TOL and abs(h[1] - top) <= CAP_GAP
                  and h[3] - h[1] <= CAP_RISE * width)
    if not mine or mine[0][0] > x0 + ALIGN_TOL:
        return None
    reach = mine[0][2]
    for h in mine[1:]:
        if h[0] > reach + ALIGN_TOL:
            break
        reach = max(reach, h[2])
    if reach < x1 - ALIGN_TOL:
        return None
    cap = unary_union([h[4] for h in mine])
    apex = cap.bounds[3]

    def rise(f: float) -> float:
        """How high the cap stands over the frame at this share of its width."""
        cut = cap.intersection(LineString([(x0 + f * width, top - CAP_GAP), (x0 + f * width, apex + CAP_GAP)]))
        return cut.bounds[3] - top if not cut.is_empty else 0.0

    middle = rise(0.5)
    if cap.area < CAP_FILL * width * (apex - top) or middle < CAP_MIDDLE * width:
        return None
    return apex if min(rise(0.25), rise(0.75)) >= CAP_SIDES * middle else None


def _is_dimension(b: _Box, arrows: list[tuple[float, float]]) -> bool:
    """The rectangle between a window and its dimension line: arrowheads point at two of its corners."""
    corners = ((b.x0, b.y0), (b.x1, b.y0), (b.x0, b.y1), (b.x1, b.y1))
    return sum(any(abs(x - cx) <= ARROW_TOL and abs(y - cy) <= ARROW_TOL for x, y in arrows) for cx, cy in corners) >= 2


def _boxes(lw: _Linework, heads: list[Head]) -> list[_Box]:
    """Rectangles and arches of the drawing, from the faces of its linework and from its closed outlines."""
    found: dict[tuple, _Box] = {}
    for poly, drawn in [(g, True) for g in lw.outlines] + [(g, False) for g in lw.faces]:  # an outline is whole where
        b = _box_of(poly, drawn)  # a line in front cuts the face it encloses
        if b is not None and not _is_dimension(b, lw.arrows):
            b.free = _bare_corners(b.x0, b.y0, b.x1, b.y1, lw.nodes)
            b.headed = not _is_strip(b) and _apex(b.x0, b.x1, b.y1, heads) is not None
            found.setdefault((round(b.x0, 2), round(b.y0, 2), round(b.x1, 2), round(b.y1, 2)), b)
    return list(found.values())


def _nest(boxes: list[_Box]) -> list[_Box]:
    """Fill ``kids`` with the rectangles directly inside each one; return the outermost."""
    if not boxes:
        return []
    a = np.array([[b.x0, b.y0, b.x1, b.y1] for b in boxes])
    area = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    roots = []
    for b in boxes:
        b.kids = []
    for i, b in enumerate(boxes):
        holds = ((a[:, 0] <= a[i, 0] + NEST_TOL) & (a[:, 1] <= a[i, 1] + NEST_TOL)
                 & (a[:, 2] >= a[i, 2] - NEST_TOL) & (a[:, 3] >= a[i, 3] - NEST_TOL) & (area > area[i] * 1.0001))
        idx = np.flatnonzero(holds)
        if len(idx):
            boxes[int(idx[np.argmin(area[idx])])].kids.append(b)
        else:
            roots.append(b)
    return roots


def _outermost(boxes: list[_Box]) -> list[_Box]:
    """The outermost rectangles, without the bays of the wall: empty rectangles of some size whose corners are all
    junctions with lines that go on (the space between two pilasters, under a canopy). A rectangle holding nothing
    but bays is a bay too."""
    while True:
        roots = _nest(boxes)
        kept = [b for b in boxes if b.kids or b.arched or b.headed or min(b.w, b.h) < BAY_MIN or b.free >= MIN_FREE_CORNERS]
        if len(kept) == len(boxes):
            return roots
        boxes = kept


def _frames(root: _Box, out: list[_Box]) -> None:
    """The outermost rectangles that are a frame (what they hold is close to their edges) or hold nothing.
    A rectangle that holds smaller things far from its edges is a bay of the wall: look inside it."""
    if root.kids:
        ux0, uy0 = min(k.x0 for k in root.kids), min(k.y0 for k in root.kids)
        ux1, uy1 = max(k.x1 for k in root.kids), max(k.y1 for k in root.kids)
        if max(ux0 - root.x0, uy0 - root.y0, root.x1 - ux1, root.y1 - uy1) > FRAME_BAND:
            for k in root.kids:
                _frames(k, out)
            return
    out.append(root)


def _panes(b: _Box) -> int:
    """In how many panes a leaf is divided: a shutter holds one inset, a sash is cut by its glazing bars."""
    while len(b.kids) == 1:
        b = b.kids[0]
    return max(1, len(b.kids))


def _plain(g: _Group) -> bool:
    """Nothing divides it in panes: a shutter, a panel, a piece of sash. A window with its bars is not plain."""
    return all(_panes(m) < GLAZED_MIN for m in g.members)


def _within(p: _Group, q: _Group) -> bool:
    return (p.x0 >= q.x0 - ALIGN_TOL and p.x1 <= q.x1 + ALIGN_TOL
            and p.y0 >= q.y0 - ALIGN_TOL and p.y1 <= q.y1 + ALIGN_TOL)


def _shares(a: _Group, b: _Group, side_by_side: bool, nodes: Counter) -> bool:
    """Along the touching side one piece lies within the extent of the other and they share an end. Pieces one above the
    other must be alike (a window and the wall under it are not one symbol). If they share only one end, both must be
    plain and the shorter one cut at the other end, away from the other piece, by a line that goes on (a piece of sash
    cut by a railing in front of it): a window complete on its own stands alone beside a door."""
    def span(g: _Group) -> tuple[float, float]:
        return (g.y0, g.y1) if side_by_side else (g.x0, g.x1)

    if span(a)[1] - span(a)[0] > span(b)[1] - span(b)[0]:
        a, b = b, a
    (lo, hi), (big_lo, big_hi) = span(a), span(b)
    if lo < big_lo - ALIGN_TOL or hi > big_hi + ALIGN_TOL:
        return False
    at_lo, at_hi = abs(lo - big_lo) <= ALIGN_TOL, abs(hi - big_hi) <= ALIGN_TOL
    if at_lo and at_hi:
        return side_by_side or _plain(a) == _plain(b)
    if not (at_lo or at_hi) or not (_plain(a) and _plain(b)):
        return False
    end = lo if at_hi else hi
    if side_by_side:  # the corner away from the other piece: the one beside it is a junction with it, whatever it is
        return not _bare(a.x0 if a.x0 + a.x1 < b.x0 + b.x1 else a.x1, end, nodes)
    return not _bare(end, a.y0 if a.y0 + a.y1 < b.y0 + b.y1 else a.y1, nodes)


def _joins(a: _Group, b: _Group, nodes: Counter) -> bool:
    """Two pieces of one symbol: they touch (or one lies within a symbol already made of several pieces) and line up."""
    w, h = max(a.x1, b.x1) - min(a.x0, b.x0), max(a.y1, b.y1) - min(a.y0, b.y0)
    if w > SYMBOL_MAX[0] or h > SYMBOL_MAX[1]:
        return False
    if (len(b.members) > 1 and _within(a, b)) or (len(a.members) > 1 and _within(b, a)):
        return True  # a pane cut in two by a line in front of it
    gx = max(a.x0, b.x0) - min(a.x1, b.x1)
    gy = max(a.y0, b.y0) - min(a.y1, b.y1)
    if -0.03 <= gx <= MERGE_GAP and _shares(a, b, True, nodes):
        return True
    return -0.03 <= gy <= MERGE_GAP and _shares(a, b, False, nodes)


def _merge(frames: list[_Box], nodes: Counter) -> list[_Group]:
    """Frames that touch and line up (the leaves of a door, the panels of an entrance) are one symbol. Strips are left
    alone: a step or a ledge under a window is not a leaf of it."""
    strips = [_Group([f], f.x0, f.y0, f.x1, f.y1, f.arched) for f in frames if _is_strip(f)]
    groups = [_Group([f], f.x0, f.y0, f.x1, f.y1, f.arched) for f in frames if not _is_strip(f)]
    merged = True
    while merged:
        merged = False
        for i, a in enumerate(groups):
            j = i + 1
            while j < len(groups):
                b = groups[j]
                if _joins(a, b, nodes):
                    a = groups[i] = _Group(a.members + b.members, min(a.x0, b.x0), min(a.y0, b.y0),
                                           max(a.x1, b.x1), max(a.y1, b.y1), a.arched or b.arched)
                    del groups[j]
                    merged = True
                    j = i + 1
                else:
                    j += 1
    return groups + strips


def _columns(g: _Group) -> list[list[_Box]]:
    """The leaves of a symbol side by side, left to right; pieces one above the other are one column."""
    leaves = g.members[0].kids if len(g.members) == 1 and g.members[0].kids else g.members
    columns: list[list[_Box]] = []
    for leaf in sorted(leaves, key=lambda k: k.x0):
        last = columns[-1] if columns else None
        if last and min(leaf.x1, max(k.x1 for k in last)) - max(leaf.x0, min(k.x0 for k in last)) \
                >= 0.5 * min(leaf.w, max(k.x1 for k in last) - min(k.x0 for k in last)):
            last.append(leaf)
        else:
            columns.append([leaf])
    return columns


def _describe(g: _Group) -> None:
    """The clear width of the symbol and the glass. Shutters folded beside the sashes (or the piers of the wall beside a
    door) are plain leaves, one at each end of the row with at least one sash between them; a plain piece much narrower
    than what remains, next to them, is a jamb. What is left is the clear width."""
    g.core = (g.x0, g.x1)
    columns = _columns(g)

    def plain(c: list[_Box]) -> bool:
        return sum(_panes(k) for k in c) < GLAZED_MIN

    def extent(cs: list[list[_Box]]) -> tuple[float, float]:
        return min(k.x0 for c in cs for k in c), max(k.x1 for c in cs for k in c)

    if len(columns) >= 3 and plain(columns[0]) and plain(columns[-1]) and not all(plain(c) for c in columns[1:-1]):
        columns = columns[1:-1]
        while len(columns) > 1:
            x0, x1 = extent(columns)
            if plain(columns[0]) and extent(columns[:1])[1] - extent(columns[:1])[0] < JAMB_SHARE * (x1 - x0):
                columns = columns[1:]
            elif plain(columns[-1]) and extent(columns[-1:])[1] - extent(columns[-1:])[0] < JAMB_SHARE * (x1 - x0):
                columns = columns[:-1]
            else:
                break
        g.core = extent(columns)
    if len(g.members) == 1 and g.members[0].kids:
        kids = g.members[0].kids
        g.pane = (min(k.x0 for k in kids), min(k.y0 for k in kids), max(k.x1 for k in kids), max(k.y1 for k in kids))


def _arches(groups: list[_Group], heads: list[Head]) -> None:
    """An arched head is drawn over the frame as faces that are not rectangles (the segments between the arc and the
    springing line). The apex is the top of the symbol."""
    for g in groups:
        if g.arched or (g.core[1] - g.core[0]) / (g.y1 - g.y0) > MAX_ASPECT:
            continue  # a strip (a slab, a step) has no arch over it
        apex = _apex(g.core[0], g.core[1], g.y1, heads)
        if apex is not None:
            g.y1, g.arched = apex, True


def _groups(lw: _Linework) -> list[_Group]:
    """The candidate symbols of the view: frames and the leaves that touch them, with their bare corners."""
    heads = _heads(lw)
    frames: list[_Box] = []
    for r in _outermost(_boxes(lw, heads)):
        _frames(r, frames)
    groups = _merge(frames, lw.nodes)
    for g in groups:
        _describe(g)
        g.free = _bare_corners(g.x0, g.y0, g.x1, g.y1, lw.nodes)
    _arches(groups, heads)
    return groups


# --- floor level ----------------------------------------------------------------------------------------------

@dataclass
class _Mark:
    value: float  # m above (+) or below (-) the finished floor
    x: float
    y: float
    height: float
    width: float


def _marks(texts: list[RawText]) -> list[_Mark]:
    """Level marks written in the view: "+0,00", "P.P.F. +0.00", "+ 3.20", "- 0.40 (297.40)"."""
    out = []
    for t in texts:
        if abs(math.sin(math.radians(t.angle))) > 0.1:
            continue
        n = len(t.lines)
        for i, line in enumerate(t.lines):
            m = MARK_RE.search(line)
            if not m:
                continue
            value = float(f"{m.group(2)}.{m.group(3)}")
            if value > MARK_MAX:
                continue
            sign = -1.0 if m.group(1) in "-\u2212" else 1.0
            y = t.y + ((n - 1) / 2 - i) * 1.5 * t.height
            out.append(_Mark(sign * value, t.x, y, t.height, t.width or 0.55 * t.height * len(line)))
    return out


def _horizontals(lines: list[LineString], min_len: float) -> list[tuple[float, float, float]]:
    """(y, x0, x1) of the horizontal pieces of the lines, collinear pieces joined."""
    rows: dict[int, list[list[float]]] = {}
    for ls in lines:
        c = np.asarray(ls.coords)
        for k in np.flatnonzero(np.abs(np.diff(c[:, 1])) < HORIZONTAL_TOL):
            rows.setdefault(round((c[k, 1] + c[k + 1, 1]) / 2 / ROW_TOL), []).append(
                [min(c[k, 0], c[k + 1, 0]), max(c[k, 0], c[k + 1, 0])])
    out = []
    for key, spans in rows.items():
        spans.sort()
        cur = spans[0]
        for a, b in spans[1:] + [[math.inf, math.inf]]:
            if a <= cur[1] + JOIN_GAP:
                cur[1] = max(cur[1], b)
                continue
            if cur[1] - cur[0] >= min_len:
                out.append((key * ROW_TOL, float(cur[0]), float(cur[1])))
            cur = [a, b]
    return sorted(out)


def _triangles(lw: _Linework) -> list[tuple[float, float, float, float]]:
    """Bounding boxes of the little triangles (drawn with lines or filled) that point at the level of a mark."""
    found = set()
    for poly in lw.faces + lw.outlines:
        if len({(round(x, 3), round(y, 3)) for x, y in poly.exterior.coords}) == 3:
            x0, y0, x1, y1 = poly.bounds
            if max(x1 - x0, y1 - y0) <= TRIANGLE_MAX:
                found.add((round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)))
    return sorted(found)


def _mark_levels(marks: list[_Mark], hsegs: list[tuple[float, float, float]],
                 triangles: list[tuple[float, float, float, float]]) -> list[tuple[float, float]]:
    """(y of the level, value) of each mark. The level is where the little triangle under or beside the text points,
    else the horizontal line just under the text (or a much longer one a little below it: the text sits on its tick)."""
    found = []
    for m in marks:
        bottom = m.y - 0.5 * m.height
        reach = MARK_REACH * m.height
        near = [t for t in triangles if abs((t[0] + t[2]) / 2 - m.x) <= m.width / 2 + MARK_SIDE
                and bottom - reach <= t[3] <= bottom + 0.5 * m.height]
        if near:
            found.append((min(near, key=lambda t: abs((t[0] + t[2]) / 2 - m.x))[1], m.value))
            continue
        under = [s for s in hsegs if bottom - reach <= s[0] <= bottom + 0.02 * m.height
                 and s[1] <= m.x + m.width / 2 + MARK_SIDE and s[2] >= m.x - m.width / 2 - MARK_SIDE]
        if under:
            y, a, b = max(under)
            longer = [s for s in hsegs if y - MARK_SNAP <= s[0] < y - ROW_TOL and s[2] - s[1] >= 3 * (b - a)]
            found.append((max(longer)[0] if longer else y, m.value))
    return found


def _ground_line(hsegs: list[tuple[float, float, float]], span: float) -> float | None:
    """The top of the ground line: the lowest horizontal line as long as the building, or a double one's upper line."""
    longest = sorted((y, a, b) for y, a, b in hsegs if b - a >= GROUND_SHARE * span)
    if not longest:
        return None
    y = longest[0][0]
    above = [yy for yy, _, _ in longest if y < yy <= y + GROUND_DOUBLE]
    return max(above) if above else y


def _mode(values: list[float], tol: float) -> float:
    """The value most of the others are within ``tol`` of (the lowest on a tie), averaged with them."""
    best = max(values, key=lambda v: (sum(abs(u - v) <= tol for u in values), -v))
    return float(np.mean([u for u in values if abs(u - best) <= tol]))


def _figure_feet(doc: Drawing, area: tuple[float, float, float, float], scale: float) -> list[float]:
    """y (m) of the feet of the person figures drawn in the area: block references, as tall as a person, whose
    insertion point is at the bottom. ``area`` in metres, ``scale``: metres per drawing unit."""
    extents: dict[str, tuple[float, float, float, float] | None] = {}
    feet = []
    for e in doc.modelspace().query("INSERT"):
        x, y = e.dxf.insert.x * scale, e.dxf.insert.y * scale
        if not (area[0] <= x <= area[2] and area[1] <= y <= area[3]) or abs(float(e.dxf.get("rotation", 0))) > 1:
            continue
        name = e.dxf.name
        if name not in extents:
            try:
                ext = ezbbox.extents(doc.blocks.get(name), fast=True)
                extents[name] = (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y) if ext.has_data else None
            except Exception:  # an anonymous block without a definition
                extents[name] = None
        ext = extents[name]
        if ext is None:
            continue
        sx, sy = abs(float(e.dxf.get("xscale", 1))), float(e.dxf.get("yscale", 1))
        width, height, foot = (ext[2] - ext[0]) * sx * scale, (ext[3] - ext[1]) * abs(sy) * scale, ext[1] * abs(sy) * scale
        if sy > 0 and FIGURE_HEIGHT[0] <= height <= FIGURE_HEIGHT[1] and width <= FIGURE_WIDTH and abs(foot) <= FIGURE_FOOT:
            feet.append(y)
    return feet


# --- the symbols ----------------------------------------------------------------------------------------------

@dataclass
class _Named:
    """A symbol the layer names say is a door or a window."""

    sym: FoundSymbol
    mixed: bool  # on a layer that holds every opening ("Infissi"): door or window is told by where it stands


def _is_opening(g: _Group) -> bool:
    """The proportions and the drawing of a door or window: not a strip, not a post, not a bay of the wall."""
    w, h = g.core[1] - g.core[0], g.y1 - g.y0
    if g.x1 - g.x0 < SYMBOL_MIN[0] or h < SYMBOL_MIN[1] or w / h > MAX_ASPECT or h / w > MAX_TALL:
        return False
    return g.arched or g.free >= MIN_FREE_CORNERS or any(m.kids for m in g.members) \
        or len(g.members) >= STRUCTURE_MIN


def _kind(y0: float, h: float, floor: float | None, levels: list[float]) -> str:
    """A tall symbol that stands on a floor (the ground one or another storey's) is a door, else a window."""
    if floor is None:
        return "door" if h >= DOOR_HEIGHT_UNKNOWN else "window"
    on_floor = y0 <= floor + DOOR_SILL or any(abs(y0 - lv) <= DOOR_SILL for lv in levels)
    return "door" if h >= DOOR_HEIGHT and on_floor else "window"


def _level_of_doors(feet: list[float]) -> float:
    """The floor the doors stand on: the lowest level that holds a fair share of them (a stray low one is noise)."""
    levels: list[list[float]] = []
    for y in sorted(feet):
        if levels and y - levels[-1][-1] <= FLOOR_VOTE:
            levels[-1].append(y)
        else:
            levels.append([y])
    busiest = max(len(lv) for lv in levels)
    return float(np.mean(next(lv for lv in levels if len(lv) >= FLOOR_DOORS * busiest)))


def _on_steps(g: _Group, strips: list[_Group], floor: float) -> bool:
    """The door stands on the steps of an entrance: strips one under the other, as wide as the door, down to the floor."""
    y, gap = g.y0, STEP_THRESHOLD
    while y > floor + FLOOR_TOL:
        below = [s for s in strips if 0 <= y - s.y1 <= gap and s.x0 <= g.core[0] + ALIGN_TOL and s.x1 >= g.core[1] - ALIGN_TOL]
        if not below:
            return False
        y, gap = min(s.y0 for s in below), STEP_GAP
    return True


def _find_floor(door_feet: list[float], levels_found: list[tuple[float, float]], ground: float | None,
                feet: list[float]) -> tuple[float | None, str]:
    """The floor of the ground storey and where it comes from, in order of trust."""
    if levels_found:
        return _mode([y - v for y, v in levels_found], FLOOR_VOTE), "quota +0,00"
    if door_feet:
        return _level_of_doors(door_feet), "porta"
    if ground is not None:
        return ground, "linea di terra"
    if feet:
        return float(np.median(feet)), "blocco figura"
    return None, ""


def _storeys(floor: float, levels_found: list[tuple[float, float]], upper_feet: list[float],
             hsegs: list[tuple[float, float, float]], span: float) -> list[float]:
    """y of the floors of all the storeys the view shows, from the marks of the other floors ("+3,20") and from the
    long line (a slab, a balcony) a door of an upper storey stands on; empty if the view shows one storey."""
    ys = [y for y, v in levels_found if v >= STOREY_MIN and abs(y - v - floor) <= FLOOR_TOL]
    for foot in upper_feet:
        slab = [y for y, a, b in hsegs if foot - SLAB_DEPTH <= y <= foot + 2 * ROW_TOL and b - a >= SLAB_SHARE * span]
        if slab:
            ys.append(max(slab))
    merged: list[float] = []
    for y in sorted(ys):
        if not merged or y - merged[-1] > FLOOR_TOL:
            merged.append(y)
    return [floor] + merged if merged else []


def _named(items: list[Item], cfg: Config) -> list[_Named]:
    """The doors and windows the layers (or block names) say, a frame holding another one counted once."""
    found = []
    for kind in ("door", "window"):
        for s in _symbols(items, kind):
            if s.geom.is_empty:
                continue
            x0, y0, x1, y1 = s.geom.bounds
            if x1 - x0 >= CUT_LINE[0] and y1 - y0 >= CUT_LINE[1]:
                found.append(_Named(FoundSymbol(kind, x0, x1, y0, y1, "layer"), cfg.layers.is_mixed_openings(s.layer)))

    def holds(b: Symbol, a: Symbol) -> bool:
        return (b.kind == a.kind and b.x0 <= a.x0 + NEST_TOL and b.x1 >= a.x1 - NEST_TOL and b.y0 <= a.y0 + NEST_TOL
                and b.y1 >= a.y1 - NEST_TOL and (b.x1 - b.x0) * (b.y1 - b.y0) > (a.x1 - a.x0) * (a.y1 - a.y0))

    return [a for a in found if not any(b is not a and holds(b.sym, a.sym) for b in found)]


def _covers(named: Symbol, shape: FoundSymbol) -> bool:
    """The layer's symbol is (a part of) the opening the shape draws: it lies within it, or they cover each other and
    are about the same size."""
    sx0, sx1 = shape.full_x or (shape.x0, shape.x1)
    inter = max(0.0, min(named.x1, sx1) - max(named.x0, sx0)) * max(0.0, min(named.y1, shape.y1) - max(named.y0, shape.y0))
    area_n, area_s = (named.x1 - named.x0) * (named.y1 - named.y0), (sx1 - sx0) * (shape.y1 - shape.y0)
    if inter >= INSIDE * area_n:
        return True
    return inter >= SAME_OVERLAP * min(area_n, area_s) and SAME_SYMBOL[0] <= area_n / area_s <= SAME_SYMBOL[1]


def _is_low(y0: float, h: float, floor: float | None) -> bool:
    """A window whose bottom is on the floor (or lower) and no taller than a metre or so is a planter, a step or a vent."""
    return floor is not None and y0 <= floor + LOW_ON_FLOOR and h <= LOW_HEIGHT


def _combine(named: list[_Named], shapes: list[FoundSymbol], floor: float | None, levels: list[float]) -> list[Symbol]:
    """The layers are the primary answer: their kind stays, and the shape of the same opening gives it the complete frame
    (a window named by its glass only, a door named by its leaves). Shapes that match no named symbol are the openings
    the names do not give."""
    out: list[Symbol] = []
    taken: dict[int, list[_Named]] = {}
    alone: list[_Named] = []
    for n in named:
        twins = [i for i, s in enumerate(shapes) if _covers(n.sym, s)]
        if twins:
            taken.setdefault(min(twins, key=lambda i: (shapes[i].x1 - shapes[i].x0) * (shapes[i].y1 - shapes[i].y0)), []).append(n)
        else:
            alone.append(n)
    for i, s in enumerate(shapes):
        if i not in taken:
            out.append(s)
            continue
        says = [n.sym.kind for n in taken[i] if not n.mixed]
        kind = ("door" if "door" in says else "window") if says else s.kind
        x0, x1, full = s.x0, s.x1, s.full_x
        n0, n1 = min(n.sym.x0 for n in taken[i]), max(n.sym.x1 for n in taken[i])
        if n0 - x0 > FRAME_BAND or x1 - n1 > FRAME_BAND:  # the shape adds a leaf the layer does not call part of the window
            x0, x1, full = n0, n1, (x0, x1) if full is None else full
        out.append(FoundSymbol(kind, x0, x1, s.y0, s.y1, "layer+forma", s.arched, s.pane, full))
    for n in alone:
        if n.mixed:
            n.sym.kind = _kind(n.sym.y0, n.sym.y1 - n.sym.y0, floor, levels)
            if n.sym.kind == "window" and _is_low(n.sym.y0, n.sym.y1 - n.sym.y0, floor):
                continue
        out.append(n.sym)
    return sorted(out, key=lambda s: (s.x0, s.y0))


def _roofed(groups: list[_Group], lw: _Linework) -> list[_Group]:
    """The symbols with a roof, an eave or the top of the wall above them: a line as long as a roof, not the cap of a
    chimney. What stands in the open sky above the roof (a chimney pot, a stack) is no opening."""
    long_lines = [g for g in lw.lines if g.bounds[2] - g.bounds[0] >= ROOF_SPAN]
    if not long_lines:
        return []
    tree = STRtree(long_lines)
    return [g for g in groups
            if len(tree.query(LineString([((g.x0 + g.x1) / 2, g.y1 + 0.02), ((g.x0 + g.x1) / 2, g.y1 + SKY)]),
                              predicate="intersects"))]


def _floor_notes(floor: float | None, source: str, door_feet: list[float]) -> list[str]:
    if floor is None:
        return ["Quota del pavimento non determinabile: nessun segno di quota, nessuna porta, nessuna linea di terra."]
    notes = [f"Pavimento a y {floor:.2f} m {FLOOR_FROM[source]}."]
    if source == "quota +0,00" and door_feet and abs(min(door_feet) - floor) > FLOOR_TOL:
        notes.append(f"Il fondo della porta piu' bassa (y {min(door_feet):.2f} m) non coincide con la quota del pavimento.")
    return notes


def detect_view_symbols(items: list[Item], texts: list[RawText], feet: list[float],
                        area: tuple[float, float, float, float], cfg: Config) -> ViewSymbols:
    """Doors, windows and floor level of one elevation, from its items (metres, as ``read_items`` gives them with
    ``keep_other`` and ``keep_fills``), its texts, the y of the feet of its person figures and its area in metres."""
    lw = _linework(items, area)
    everything = _groups(lw)
    groups = [g for g in everything if g.x1 - g.x0 >= SYMBOL_MIN[0] and g.y1 - g.y0 >= SYMBOL_MIN[1]]
    strips = [g for g in everything if _is_strip(g.members[0]) and g.x1 - g.x0 > g.y1 - g.y0]
    shapes = _roofed([g for g in groups if _is_opening(g)], lw)
    hsegs = _horizontals(lw.lines, MARK_LINE_MIN)
    span = area[2] - area[0]
    named = _named(items, cfg)
    marks = _mark_levels(_marks(texts), hsegs, _triangles(lw))

    door_feet = [n.sym.y0 for n in named if n.sym.kind == "door" and not n.mixed]
    door_feet += [g.y0 for g in shapes if g.y1 - g.y0 >= DOOR_HEIGHT_UNKNOWN]
    ground = _ground_line(hsegs, span)
    lowest = [n.sym.y0 for n in named] + [g.y0 for g in shapes]
    if ground is not None and lowest and ground > min(lowest) + FLOOR_TOL:
        ground = None  # a line above the bottom of an opening is the eaves or the roof: the ground is out of the view
    floor, source = _find_floor(door_feet, marks, ground, feet)
    if floor is not None and source == "quota +0,00":
        near = [y for y, a, b in hsegs if b - a >= GROUND_SHARE * span and abs(y - floor) <= FLOOR_SNAP]
        floor = min(near, key=lambda y: abs(y - floor)) if near else floor
    levels: list[float] = []
    if floor is not None:
        upper = [g.y0 for g in shapes if g.y1 - g.y0 >= DOOR_HEIGHT_UNKNOWN and g.y0 >= floor + STOREY_MIN]
        levels = _storeys(floor, marks, upper, hsegs, span)

    found = []
    for g in shapes:
        kind = _kind(g.y0, g.y1 - g.y0, floor, levels)
        if kind == "window" and _is_low(g.y0, g.y1 - g.y0, floor):
            continue
        if kind == "window" and floor is not None and g.y1 - g.y0 >= DOOR_HEIGHT and g.y0 - floor <= STEPS_MAX \
                and _on_steps(g, strips, floor):
            kind = "door"
        found.append(FoundSymbol(kind, g.core[0], g.core[1], g.y0, g.y1, "forma", g.arched, g.pane,
                                 (g.x0, g.x1) if g.core != (g.x0, g.x1) else None))
    notes = _floor_notes(floor, source, door_feet)
    if len(groups) > len(found):
        notes.append(f"{len(groups) - len(found)} figure scartate: fasce, specchiature di parete, vasi, gradini, "
                     "comignoli: non sono porte o finestre.")
    symbols = _combine(named, found, floor, levels)
    return ViewSymbols(symbols, floor, source, levels, [(y, a, b) for y, a, b in hsegs if b - a >= MIN_LINE], notes)


def read_view_symbols(doc: Drawing, cfg: Config, bbox: tuple[float, float, float, float], unit: str,
                      unit_scale: float) -> ViewSymbols:
    """Doors, windows and floor level of the elevation inside ``bbox`` (drawing units), whatever its layers are
    called. ``unit``/``unit_scale``: the drawing unit already decided by the main read, and its size in metres."""
    result = read_items(doc, cfg, area=bbox, ignore_veto=True, keep_other=True, unit=unit, keep_fills=True)
    area = tuple(v * unit_scale for v in bbox[:4])
    return detect_view_symbols(result.items, read_texts(doc, cfg, unit_scale, area=bbox),
                               _figure_feet(doc, area, unit_scale), area, cfg)
