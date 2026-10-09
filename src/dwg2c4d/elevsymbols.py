"""Doors and windows of an elevation that names none of them, and the floor level they stand on.

The elevations of a real sheet draw their openings on layer 0 or on a layer called "Quote", as loose lines forming
rectangles, as a frame with panes and shutters inside, as an arch, as a hatch for the glass. A person recognises them by
their shape and by where they stand; so does this module:

* the faces of the linework (and the closed polylines, which a crossing railing cannot cut) that are rectangles or
  arches of a door or window size are the candidates; a frame drawn inside another one collapses to the outermost;
* leaves that touch (the two halves of a door, the panels of an entrance, the sashes between two shutters) are one
  symbol; shutters folded against the wall are told from the sashes and left out of the clear width;
* what is not an opening (a strip of fascia, a bay of the wall between two pilasters, a planter on the ground line,
  a railing, a chimney above the roof, a pergola with its vine, a title frame, a grid of tiles, the outline of the
  whole facade) is rejected by its proportions and by how it is drawn; a rectangle drawn with four lines whose ends
  miss the corners by a few millimetres is still a rectangle;
* the floor comes from a level mark ("+0,00", "P.P.F. +0.00") and the line it labels, else from the bottom of the
  lowest door (the top of the ground line when the doors found all stand a storey above a ground line that walls
  stand on), the top of the ground line, or the foot of a person figure; the other storeys from their marks, or
  from the slab that runs between two rows of openings (a floor is 2.3 to 4.5 m above the one under it and has
  openings or a slab at it: the eaves and the ridge are marked too but are no floors).

Layers that do name doors and windows stay the primary answer for those symbols and keep their size: the shapes
complete them (the frame a few centimetres around a window drawn as its glass only; the arch the layer does not
draw; a shutter the layer does not call part of the window only widens ``full_x``), never with a stone surround or
a step that lies across them, and add the openings the names do not give.

A sheet has many views and reading it takes seconds: ``read_view_symbols`` reads the modelspace once per drawing and
cuts each view out of that. A view that is a plan (areas written in it) or has too many lines is not read.

Limits, because the drawing does not tell: a window between a continuous sill line and a continuous lintel line is
not found, nor told from the bays of the wall between such windows (a rectangle with all four corners crossed by lines
is a bay, also when it was drawn with four lines of its own: the panel under a window is drawn so); a shape with no
roof, eave or wall top over any opening is accepted (with a note), as chimneys and details cannot then be told.
"""

from __future__ import annotations

import copy
import math
import re
import weakref
from dataclasses import dataclass, field

import numpy as np
import shapely
from ezdxf import bbox as ezbbox
from ezdxf.document import Drawing
from shapely.geometry import LineString, MultiLineString, Polygon, box
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

from .autodetect import AREA_MARK, PLAN_AREAS
from .config import Config
from .elevation import MIN_LINE, Symbol
from .openings import _symbols
from .reader import Item, read_items
from .texts import RawText, read_texts

PART_MIN = 0.15  # m: a rectangle with a smaller side is a bar, a mullion or a moulding, never a leaf
SYMBOL_MAX = (6.8, 4.2)  # m: widest and tallest door or window (a sectional door with its piers, a portal and steps)
SYMBOL_MIN = (0.28, 0.35)  # m: smallest door or window (a slit, a cellar light)
RECT_FILL = 0.97  # a face is a rectangle if it fills this share of its bounding box
ARCH_FILL = 0.80  # ... an arch if it fills at least this much of it (a semicircle on a square: 0.93)
CLIP_FILL = 0.85  # a polygon filling this much of its box, with straight sides and ...
CLIP_CORNERS = 6  # ... no more corners than this, is a rectangle with a corner or two cut off
ARCH_BASE = 0.95  # an arch is as wide as its box in the lowest third ...
ARCH_TOP = 0.8  # ... and narrower than this share of it at the apex, which is in the middle (a stair cuts a corner)
ARCH_CENTRED = 0.1  # the apex is this close to the middle of the box, as a share of its width
NEST_TOL = 0.015  # m: a rectangle this close to the edges of another one lies inside it
FRAME_BAND = 0.30  # m: a frame is no wider than this around what it holds (more: it is a bay of the wall)
CASING = 0.10  # m: the frame of a window is no wider than this around its glass; a stone surround or a step is wider
MERGE_GAP = 0.20  # m: leaves this close are one symbol (the halves of a door, the panels of an entrance)
TILING_MIN = 0.5  # the leaves of a symbol fill at least this share of the box they span
ALIGN_TOL = 0.10  # m: leaves of one symbol line up at the top or at the bottom (or at both sides), within this
NODE_TOL = 0.005  # m: lines ending this close to a corner meet there
GRID_STEP = 0.001  # m: horizontal and vertical lines are put on this grid before their faces are made
OUTWARD = 0.1  # a line leaves a corner outwards if its direction (a unit vector) points this far out of the rectangle
MIN_FREE_CORNERS = 2  # a real opening is outlined by its own lines: at least this many of its 4 corners are bare
BAY_MIN = 1.0  # m: an empty rectangle this wide and tall, with fewer bare corners, is a bay of the wall; the panes of a
#                window with mullions have lines that go on at every corner too, but they are narrow
STRUCTURE_MIN = 4  # a symbol of at least this many leaves is an opening even if its corners are not bare
#                    (a frame with a frame inside is one too)
TILE_GRID = 3  # a grid of alike leaves at least this many across and down, none bigger than TILE_MAX ...
TILE_MAX = 0.8  # m: ... is a wall of tiles (glass blocks, stack-bond bricks), not the panels of a door
TILED_MIN = 0.9  # a rectangle drawn with four lines is cut by lines across it (not left open by a gap) if the faces
#                  inside it fill this share of it
GLAZED_MIN = 2  # a leaf divided in at least this many panes is a sash; a plain one is a shutter
SLAT_MIN = 3  # a leaf cut across by at least this many lines ...
SLAT_PITCH = 0.30  # m: ... no further apart than this, its cells ...
SLAT_ASPECT = 1.5  # ... at least this many times as wide as tall, is louvred: a shutter, not glazed
SHUTTER_SHARE = 0.6  # alike plain leaves at both ends of a row of plain ones are shutters if each is at most this share
#                      of the width between them (two shutters close one window: half of it each) ...
SHUTTER_ROW = 4  # ... and the row has no more leaves than this (a window of one or two sashes: not planks or slats)
TOUCH = 0.03  # m: leaves this close, or overlapping, touch
CAP_GAP = 0.05  # m: the faces of an arch's head start this close to the top of the frame ...
CAP_RISE = 0.55  # ... rise no more than this share of its width and together span it
CAP_CURVE = 0.01  # m: a curved side keeps at least 5 corners when simplified this much; a gable keeps 3
CAP_FILL = 0.55  # the faces of an arch's head fill this share of the box they span (a circle segment: 0.67 to 0.79)
CAP_MIDDLE = 0.1  # the head rises at least this share of the width in the middle ...
CAP_SIDES = 0.5  # ... and at a quarter of the width at least this share of that
FLOOR_DOORS = 1 / 3  # doors at one level count for the floor if they are this share of the doors at the busiest
JAMB_SHARE = 0.25  # a plain piece beside the sashes narrower than this share of the width left is a jamb or a pier
SLIT_WIDTH = 0.35  # m: an opening narrower than this ...
SLIT_ASPECT = 2.0  # ... is at least this many times as tall as it is wide
MAX_ASPECT = 3.5  # a symbol wider than this (width / height) is a strip: fascia, canopy, step ...
MAX_TALL = 5.6  # ... and one taller than this (height / width) is a post, a pilaster or a downpipe
DOOR_HEIGHT = 1.6  # m: a symbol this tall that stands on a floor is a door
DOOR_HEIGHT_UNKNOWN = 1.9  # m: ... and when the floor is not known, only a taller one is taken for a door
DOOR_SILL = 0.30  # m: standing on a floor is having the bottom at most this far above it
STEPS_MAX = 1.2  # m: a door this high above the floor can stand on the steps of an entrance
PLINTH_MAX = 0.5  # m: a line across the facade this close under the top of a plinth cuts a strip off the foot of a door
STEP_GAP = 0.08  # m: the steps of a stair are this close to one another ...
STEP_THRESHOLD = 0.3  # m: ... and the top one this close under the door (the threshold)
LOW_ON_FLOOR = 0.05  # m: a window whose bottom is this close to the floor (or lower) and ...
LOW_HEIGHT = 1.3  # m: ... no taller than this is a planter, a step or a vent

FIGURE_HEIGHT = (1.5, 2.0)  # m: height of a person drawn in an elevation
FIGURE_WIDTH = 1.0  # m: at most this wide
FIGURE_FOOT = 0.10  # m: the insertion point of such a block is this close to its bottom, the feet

TEXT_MARGIN = 1.0  # m: a level mark may sit this far outside the drawing it belongs to
RAIL_BARS = 7  # a run of at least this many bars, evenly spaced ...
RAIL_PITCH = 0.25  # m: ... no further apart than this ...
RAIL_REGULAR = 0.15  # ... with spacings equal within this share ...
RAIL_HEIGHT = 0.6  # ... and as tall as this share of the symbol, is a railing, not glazing bars
RAIL_MAX = 1.3  # m: ... if the symbol is no taller than a balustrade (a plank door or a louvred window is) ...
RAIL_TOP = 0.8  # ... and a line along the top, this share of its width at least, is the handrail
BAR_MERGE = 0.03  # m: lines this close are one bar (a bar drawn as two lines)
SKY = 60.0  # m: how far above a symbol the drawing is searched for a roof
OUTLINE_MIN = 2.0  # m: a lone frame with nothing in it at least this wide ...
OUTLINE_SHARE = 0.8  # ... and this share of all that is drawn from half its height up, is the outline of the facade
SEGMENTS_MAX = 60_000  # a view with more straight pieces of line than this is a plan or a site, not an elevation (and
#                        reading it takes minutes)
ROOF_SPAN = 2.0  # m: a roof, an eave or a wall top runs at least this far sideways; the cap of a chimney does not
# "+0,00" "- 0.40" "+-0.00" "P.F.+0,00": not the end of a number ("2.5-3.0")
MARK_RE = re.compile(r"(?<![\w,])(?<!\d\.)([+\-\u00b1\u2212])\s*(\d{1,3})\s*[.,]\s*(\d{1,3})(?!\d)")
PLUS_MINUS_RE = re.compile(r"%%[pP]|\\U\+00[bB]1")  # how a DXF file stores the plus-minus sign: %%p, \U+00B1
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
STOREY_MIN = 2.3  # m: a storey is at least this tall from floor to floor (a mark closer is a lintel or a landing)
STOREY_MAX = 4.5  # m: ... and no taller than this (a mark higher is the eaves or the ridge, or a storey is missing)
SLAB_SHARE = 0.3  # a line under the foot of an upper door is a slab if it is this share of the width of the building
SLAB_DEPTH = 0.08  # m: ... and lies this close under the foot
SLAB_SLACK = 0.1  # m: openings stand on a slab, or hang under it, within this
SLAB_SPAN = 0.5  # a slab between two rows of openings is a line at least this share of the width the openings span
SAME_SYMBOL = (1 / 3, 3.0)  # a named symbol and a shape are the same opening if their areas are this close
SAME_OVERLAP = 0.5  # ... and the smaller one lies this much within the other
INSIDE = 0.9  # a named symbol lying this much within a shape is a part of it
PART_RATIO = 3.0  # a shape lying within a named symbol this many times as large is a piece of it
HEAD_MAX = 0.6  # m: a box this low that is as wide as a named symbol and touches it is its lintel box, not a window
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
    glass: bool = False  # it is a hatch or a solid: the glass of a sash
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


Nodes = dict[tuple[int, int], list[tuple[float, float]]]  # where lines end (rounded) -> the directions they leave in


@dataclass
class _Linework:
    """The lines of the view and what they enclose."""

    lines: list[LineString]  # cut to the view
    faces: list[Polygon]  # the faces of the planar graph of the lines
    outlines: list[Polygon]  # closed polylines and hatches: complete even where other lines cross them
    nodes: Nodes  # where lines end, by rounded position, and the directions they leave in
    arrows: list[tuple[float, float]]  # corners of the filled triangles that are arrowheads of dimension lines
    grid: list[Polygon] = field(default_factory=list)  # faces of the horizontal and vertical lines alone, if others exist
    glass: list[Polygon] = field(default_factory=list)  # the outlines that are hatches and solids
    rows: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))  # (y, x0, x1) of each horizontal piece drawn
    cols: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))  # (x, y0, y1) of each vertical piece drawn


# --- openings from the shapes ---------------------------------------------------------------------------------

def _lines(items: list[Item], area: tuple[float, float, float, float]) -> list[LineString]:
    """All linework of the items (outlines of rings included), cut to the view: what lies beside is another drawing."""
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
    cut = shapely.get_parts([shapely.clip_by_rect(g, *area) for g in out])  # a line may leave the view and come back
    return [g for g in cut if g.geom_type == "LineString" and not g.is_empty]


def _sides(lines: list[LineString]) -> tuple[np.ndarray, np.ndarray]:
    """The horizontal pieces of the lines as rows (y, x0, x1) and the vertical ones as rows (x, y0, y1)."""
    xy = shapely.get_coordinates(lines)
    owner = np.repeat(np.arange(len(lines)), shapely.get_num_coordinates(lines))
    a, b = xy[:-1], xy[1:]
    along = owner[:-1] == owner[1:]
    flat = along & (np.abs(a[:, 1] - b[:, 1]) < HORIZONTAL_TOL) & (a[:, 0] != b[:, 0])
    upright = along & (np.abs(a[:, 0] - b[:, 0]) < HORIZONTAL_TOL) & (a[:, 1] != b[:, 1])
    return (np.column_stack([(a[flat, 1] + b[flat, 1]) / 2, np.minimum(a[flat, 0], b[flat, 0]),
                             np.maximum(a[flat, 0], b[flat, 0])]),
            np.column_stack([(a[upright, 0] + b[upright, 0]) / 2, np.minimum(a[upright, 1], b[upright, 1]),
                             np.maximum(a[upright, 1], b[upright, 1])]))


def _nodes(pieces: list[LineString]) -> Nodes:
    """Where the pieces of the noded lines end, and the direction (a unit vector) each one leaves in. A piece no longer
    than NODE_TOL with a free end (nothing else ends there) is a line run a hair past a corner: it leaves no direction."""
    at = np.concatenate([shapely.get_coordinates(shapely.get_point(pieces, k)) for k in (0, -1)])
    next_to = np.concatenate([shapely.get_coordinates(shapely.get_point(pieces, k)) for k in (1, -2)])
    way = next_to - at
    length = np.hypot(way[:, 0], way[:, 1])
    way /= np.where(length > 0, length, 1)[:, None]
    _, inverse, count = np.unique(at, axis=0, return_inverse=True, return_counts=True)  # the noding made the ends equal
    alone = (count[inverse.ravel()] == 1).reshape(2, -1).any(axis=0)  # the piece has an end where nothing else ends
    cell = np.round(at / NODE_TOL).astype(int)
    stub = np.tile(alone & (shapely.length(pieces) <= NODE_TOL), 2)
    nodes: Nodes = {}
    keep = ~stub
    for (i, j), (dx, dy) in zip(cell[keep].tolist(), way[keep].tolist()):
        nodes.setdefault((i, j), []).append((dx, dy))
    return nodes


def _grid(lines: list[LineString]) -> list[Polygon]:
    """The faces of the horizontal and vertical lines alone that oblique lines run across. The opening marks of a sash
    (V, X) cut it into triangles in the planar graph of all the lines, and the sash is a face of the straight ones only.
    The lines are put on a millimetre grid first: the ends of lines meant to meet differ by less than that."""
    straight: list[LineString] = []
    oblique: list[LineString] = []
    for ls in lines:
        c = np.asarray(ls.coords)
        step = np.abs(np.diff(c, axis=0))
        flat = (step[:, 0] < HORIZONTAL_TOL) | (step[:, 1] < HORIZONTAL_TOL)
        if flat.all():
            straight.append(ls)
            continue
        straight.extend(LineString(c[k:k + 2]) for k in np.flatnonzero(flat & (step.sum(axis=1) > 0)))
        oblique.extend(LineString(c[k:k + 2]) for k in np.flatnonzero(~flat))
    if not straight or not oblique:
        return []
    faces = np.array(list(polygonize(unary_union(shapely.set_precision(MultiLineString(straight), GRID_STEP)))),
                     dtype=object)
    middles = shapely.line_interpolate_point(np.array(oblique, dtype=object), 0.5, normalized=True)
    face_i, piece_i = STRtree(middles).query(faces, predicate="contains")  # an oblique line runs inside the face
    return list(faces[np.unique(face_i)])


def _linework(items: list[Item], lines: list[LineString]) -> _Linework:
    """The faces and nodes of the ``lines`` of the view and the closed outlines of its items."""
    polygons = [(p.kind == "fill", g) for it in items for p in it.prims for g in getattr(p.geom, "geoms", [p.geom])
                if g.geom_type == "Polygon"]
    outlines, glass = [g for _, g in polygons], [g for fill, g in polygons if fill]
    arrows = [(x, y) for g in outlines if len({(round(x, 3), round(y, 3)) for x, y in g.exterior.coords}) == 3
              and max(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1]) <= ARROW_MAX for x, y in g.exterior.coords]
    if not lines:
        return _Linework([], [], outlines, {}, arrows, [], glass)
    noded = unary_union(lines)
    rows, cols = _sides(lines)
    return _Linework(lines, list(polygonize(noded)), outlines, _nodes(list(getattr(noded, "geoms", [noded]))), arrows,
                     _grid(lines), glass, rows, cols)


def _ends_at(x: float, y: float, nodes: Nodes) -> list[tuple[float, float]]:
    """The directions in which the lines that end at (x, y) leave it."""
    i, j = round(x / NODE_TOL), round(y / NODE_TOL)
    return [d for di in (-1, 0, 1) for dj in (-1, 0, 1) for d in nodes.get((i + di, j + dj), ())]


def _junction(x: float, y: float, sx: int, sy: int, nodes: Nodes) -> bool:
    """A line goes on past the corner (x, y) of a rectangle that lies towards (sx, sy) from it: one that leaves it
    outwards, not along the two sides of the rectangle or into it (the mark of a sash, a diagonal brace)."""
    return any(dx * sx < -OUTWARD or dy * sy < -OUTWARD for dx, dy in _ends_at(x, y, nodes))


def _bare_corners(x0: float, y0: float, x1: float, y1: float, nodes: Nodes) -> int:
    """How many of the 4 corners of a rectangle are not a junction with a line that goes on past them."""
    return sum(not _junction(x, y, sx, sy, nodes) for x, y, sx, sy in
               ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)))


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


def _is_open_frame(outline: Polygon) -> bool:
    """The frame of a door whose leaf stands on the same threshold: a bar on each side and one on top, open at the
    bottom (the leaf between them is not a closed ring, so the planar graph does not give the frame as one face)."""
    x0, y0, x1, y1 = outline.bounds
    gap = box(x0, y0, x1, y1).difference(outline)
    if gap.geom_type != "Polygon":
        return False
    g0, h0, g1, h1 = gap.bounds
    return gap.area >= RECT_FILL * (g1 - g0) * (h1 - h0) and abs(h0 - y0) <= NODE_TOL \
        and 0 < g0 - x0 <= FRAME_BAND and 0 < x1 - g1 <= FRAME_BAND and 0 < y1 - h1 <= FRAME_BAND


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
    if not drawn and _is_open_frame(outline):
        return _Box(x0, y0, x1, y1)
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


def _rectangles(rows: np.ndarray, cols: np.ndarray) -> list[tuple[float, float, float, float]]:
    """(x0, y0, x1, y1) of the rectangles drawn as four lines of their own size: two horizontal and two vertical ones
    whose ends are within NODE_TOL of the corners. Ends that stop short of a corner or run past it by less leave no face
    in the planar graph of the lines (or none of the right size), but they are a rectangle to the eye."""
    if not len(rows) or not len(cols):
        return []
    by_x = cols[np.argsort(cols[:, 0])]
    rows = rows[(rows[:, 2] - rows[:, 1] >= PART_MIN) & (rows[:, 2] - rows[:, 1] <= SYMBOL_MAX[0])]
    rows = rows[np.argsort(rows[:, 1])]

    def drawn(x: float, y0: float, y1: float) -> bool:
        near = by_x[np.searchsorted(by_x[:, 0], x - NODE_TOL):np.searchsorted(by_x[:, 0], x + NODE_TOL, side="right")]
        return bool(np.any((np.abs(near[:, 1] - y0) <= NODE_TOL) & (np.abs(near[:, 2] - y1) <= NODE_TOL)))

    found = []
    for k, (y, a, b) in enumerate(rows.tolist()):
        twins = rows[k + 1:np.searchsorted(rows[:, 1], a + NODE_TOL, side="right")]
        for y2 in twins[(np.abs(twins[:, 2] - b) <= NODE_TOL) & (np.abs(twins[:, 0] - y) >= PART_MIN)
                        & (np.abs(twins[:, 0] - y) <= SYMBOL_MAX[1]), 0].tolist():
            if drawn(a, min(y, y2), max(y, y2)) and drawn(b, min(y, y2), max(y, y2)):
                found.append((a, min(y, y2), b, max(y, y2)))
    return found


def _boxes(lw: _Linework, heads: list[Head]) -> list[_Box]:
    """Rectangles and arches of the drawing, from the faces of its linework, from its closed outlines and from the
    rectangles drawn as four lines whose ends do not quite meet (they close no face) that no box holds yet."""
    found: dict[tuple, _Box] = {}
    glass = {id(g) for g in lw.glass}

    def complete(b: _Box | None) -> _Box | None:
        if b is None or _is_dimension(b, lw.arrows):
            return None
        b.free = _bare_corners(b.x0, b.y0, b.x1, b.y1, lw.nodes)
        b.headed = not _is_strip(b) and _apex(b.x0, b.x1, b.y1, heads) is not None
        return b

    for poly, drawn in [(g, True) for g in lw.outlines] + [(g, False) for g in lw.faces + lw.grid]:  # an outline is whole where
        b = complete(_box_of(poly, drawn))  # a line in front cuts the face it encloses
        if b is not None:
            b.glass = id(poly) in glass
            found.setdefault((round(b.x0, 2), round(b.y0, 2), round(b.x1, 2), round(b.y1, 2)), b).glass |= b.glass
    drawn_as_four_lines = _rectangles(lw.rows, lw.cols)
    if drawn_as_four_lines:
        have = np.array([[b.x0, b.y0, b.x1, b.y1] for b in found.values()]).reshape(-1, 4)
        faces = STRtree(lw.faces)

        def tiled(x0: float, y0: float, x1: float, y1: float) -> bool:
            """The faces inside it fill it: lines across it (a mullion, a railing) cut it, no gap leaves it open."""
            inside = faces.query(box(x0 - NODE_TOL, y0 - NODE_TOL, x1 + NODE_TOL, y1 + NODE_TOL), predicate="contains")
            return sum(lw.faces[i].area for i in inside) >= TILED_MIN * (x1 - x0) * (y1 - y0)

        for x0, y0, x1, y1 in drawn_as_four_lines:
            # a box that is, or holds, the rectangle already has its place in the nest: leave that as it is
            held = np.any((have[:, 0] <= x0 + NODE_TOL) & (have[:, 1] <= y0 + NODE_TOL)
                          & (have[:, 2] >= x1 - NODE_TOL) & (have[:, 3] >= y1 - NODE_TOL))
            if not held and not tiled(x0, y0, x1, y1) and (b := complete(_Box(x0, y0, x1, y1))) is not None:
                found[(x0, y0, x1, y1)] = b
                have = np.vstack([have, [x0, y0, x1, y1]])
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


def _flanked(b: _Box, solid: list[_Box]) -> bool:
    """Leaves of the same height touch it at both sides: the plain middle of a window between its shutters."""
    def beside(x: float, side: int) -> bool:
        return any(abs((s.x1 if side < 0 else s.x0) - x) <= TOUCH and abs(s.y0 - b.y0) <= ALIGN_TOL
                   and abs(s.y1 - b.y1) <= ALIGN_TOL for s in solid)

    return beside(b.x0, -1) and beside(b.x1, 1)


def _outermost(boxes: list[_Box]) -> list[_Box]:
    """The outermost rectangles, without the bays of the wall: empty rectangles of some size whose corners are all
    junctions with lines that go on (the space between two pilasters, under a canopy). A rectangle holding nothing
    but bays is a bay too. One with a leaf of its own height at each side is no bay: it is between its shutters."""
    while True:
        roots = _nest(boxes)
        real = [bool(b.kids or b.arched or b.headed or b.free >= MIN_FREE_CORNERS) for b in boxes]
        solid = [b for b, r in zip(boxes, real) if r and not _is_strip(b)]
        kept = [b for b, r in zip(boxes, real) if r or min(b.w, b.h) < BAY_MIN or _flanked(b, solid)]
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


def _has_glass(b: _Box) -> bool:
    """A hatch or a solid lies in the leaf: it is a sash with its glass drawn, whatever its panes."""
    return b.glass or any(_has_glass(k) for k in b.kids)


def _slats(cells: list[_Box]) -> bool:
    """Cells stacked at the pitch of a louvre, wider than tall: the slats of a shutter, not the panes of a sash."""
    return len(cells) >= SLAT_MIN and all(k.h <= SLAT_PITCH + NODE_TOL and k.w >= SLAT_ASPECT * k.h for k in cells)


def _panes(b: _Box) -> int:
    """In how many panes a leaf is divided: a shutter holds one inset, a sash is cut by its glazing bars."""
    while len(b.kids) == 1:
        b = b.kids[0]
    return 1 if _slats(b.kids) else max(1, len(b.kids))


def _plain(g: _Group) -> bool:
    """Nothing divides it in panes: a shutter, a panel, a piece of sash. A window with its bars is not plain."""
    return all(_panes(m) < GLAZED_MIN for m in g.members)


def _within(p: _Group, q: _Group) -> bool:
    return (p.x0 >= q.x0 - ALIGN_TOL and p.x1 <= q.x1 + ALIGN_TOL
            and p.y0 >= q.y0 - ALIGN_TOL and p.y1 <= q.y1 + ALIGN_TOL)


def _shares(a: _Group, b: _Group, side_by_side: bool, nodes: Nodes) -> bool:
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
    end, inward = (lo, 1) if at_hi else (hi, -1)  # the end where the shorter piece is cut, and where it lies from it
    if side_by_side:  # the corner away from the other piece: the one beside it is a junction with it, whatever it is
        left = a.x0 + a.x1 < b.x0 + b.x1
        return _junction(a.x0 if left else a.x1, end, 1 if left else -1, inward, nodes)
    bottom = a.y0 + a.y1 < b.y0 + b.y1
    return _junction(end, a.y0 if bottom else a.y1, inward, 1 if bottom else -1, nodes)


def _joins(a: _Group, b: _Group, nodes: Nodes) -> bool:
    """Two pieces of one symbol: they touch (or one lies within a symbol already made of several pieces) and line up."""
    w, h = max(a.x1, b.x1) - min(a.x0, b.x0), max(a.y1, b.y1) - min(a.y0, b.y0)
    if w > SYMBOL_MAX[0] or h > SYMBOL_MAX[1]:
        return False
    if sum(m.w * m.h for m in a.members + b.members) < TILING_MIN * w * h:
        return False  # leaves fill the symbol they make: a loose cluster of boxes (a stair, a section) is not one
    if (len(b.members) > 1 and _within(a, b)) or (len(a.members) > 1 and _within(b, a)):
        return True  # a pane cut in two by a line in front of it
    gx = max(a.x0, b.x0) - min(a.x1, b.x1)
    gy = max(a.y0, b.y0) - min(a.y1, b.y1)
    if -0.03 <= gx <= MERGE_GAP and _shares(a, b, True, nodes):
        return True
    return -0.03 <= gy <= MERGE_GAP and _shares(a, b, False, nodes)


def _merge(frames: list[_Box], nodes: Nodes) -> list[_Group]:
    """Frames that touch and line up (the leaves of a door, the panels of an entrance) are one symbol. Strips are left
    alone: a step or a ledge under a window is not a leaf of it (``_plinths`` takes the one at the foot of a door)."""
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


def _plinths(groups: list[_Group], nodes: Nodes) -> list[_Group]:
    """A line across the facade at the foot of a door (the plinth) cuts off the strip between its jambs: the door goes
    down to the bottom of that strip. A step is a strip too, but no line goes on sideways past its top corners."""
    strips = [g for g in groups if len(g.members) == 1 and _is_strip(g.members[0]) and not g.members[0].kids]
    taken: set[int] = set()
    for g in groups:
        if len(g.members) == 1 and _is_strip(g.members[0]):
            continue
        for s in strips:
            if id(s) not in taken and abs(s.y1 - g.y0) <= TOUCH and abs(s.x0 - g.x0) <= ALIGN_TOL \
                    and abs(s.x1 - g.x1) <= ALIGN_TOL and s.y1 - s.y0 <= PLINTH_MAX and g.y1 - s.y0 >= DOOR_HEIGHT \
                    and any(dx > OUTWARD for dx, _ in _ends_at(s.x1, s.y1, nodes)) \
                    and any(dx < -OUTWARD for dx, _ in _ends_at(s.x0, s.y1, nodes)):
                g.y0 = s.y0
                taken.add(id(s))
                break
    return [g for g in groups if id(g) not in taken]


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
    door) are plain leaves, one at each end of the row with at least one sash between them (or, when no leaf has glazing
    bars, alike and narrow beside the rest); a plain piece much narrower than what remains, next to them, is a jamb.
    What is left is the clear width."""
    g.core = (g.x0, g.x1)
    columns = _columns(g)

    def plain(c: list[_Box]) -> bool:
        return _slats(c) or sum(_panes(k) for k in c) < GLAZED_MIN

    def extent(cs: list[list[_Box]]) -> tuple[float, float]:
        return min(k.x0 for c in cs for k in c), max(k.x1 for c in cs for k in c)

    def folded() -> bool:
        """Alike leaves at both ends of a short row, much narrower than the row between them, no strips (planks) and
        with no glass drawn in them: shutters folded beside plain sashes."""
        first, last, middle = extent(columns[:1]), extent(columns[-1:]), extent(columns[1:-1])
        return len(columns) <= SHUTTER_ROW and abs((first[1] - first[0]) - (last[1] - last[0])) <= ALIGN_TOL \
            and first[1] - first[0] <= SHUTTER_SHARE * (middle[1] - middle[0]) \
            and not any(_is_strip(k) or _has_glass(k) for c in (columns[0], columns[-1]) for k in c)

    if len(columns) >= 3 and plain(columns[0]) and plain(columns[-1]) \
            and (not all(plain(c) for c in columns[1:-1]) or folded()):
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
    groups = _plinths(_merge(frames, lw.nodes), lw.nodes)
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
    """Level marks written in the view: "+0,00", "P.P.F. +0.00", "%%p0,00", "+ 3.20", "- 0.40 (297.40)"."""
    out = []
    for t in texts:
        if abs(math.sin(math.radians(t.angle))) > 0.1:
            continue
        n = len(t.lines)
        for i, line in enumerate(t.lines):
            m = MARK_RE.search(PLUS_MINUS_RE.sub("\u00b1", line))
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
    """(y, x0, x1) of the horizontal pieces of the lines, collinear pieces joined (those within a few millimetres of
    one another in height are on one row, at the mean of their heights)."""
    rows: dict[int, list[list[float]]] = {}
    for ls in lines:
        c = np.asarray(ls.coords)
        for k in np.flatnonzero(np.abs(np.diff(c[:, 1])) < HORIZONTAL_TOL):
            y = (c[k, 1] + c[k + 1, 1]) / 2
            rows.setdefault(round(y / ROW_TOL), []).append([min(c[k, 0], c[k + 1, 0]), max(c[k, 0], c[k + 1, 0]), y])
    out = []
    for spans in rows.values():
        spans.sort()
        x0, x1, ys = spans[0][0], spans[0][1], [spans[0][2]]
        for a, b, y in spans[1:] + [[math.inf, math.inf, 0.0]]:
            if a <= x1 + JOIN_GAP:
                x1 = max(x1, b)
                ys.append(y)
                continue
            if x1 - x0 >= min_len:
                out.append((float(np.mean(ys)), float(x0), float(x1)))
            x0, x1, ys = a, b, [y]
    return sorted(out)


def _verticals(lines: list[LineString]) -> np.ndarray:
    """(x, bottom, top) of the vertical pieces of the lines."""
    rows = []
    for ls in lines:
        c = np.asarray(ls.coords)
        for k in np.flatnonzero(np.abs(np.diff(c[:, 0])) < HORIZONTAL_TOL):
            rows.append(((c[k, 0] + c[k + 1, 0]) / 2, min(c[k, 1], c[k + 1, 1]), max(c[k, 1], c[k + 1, 1])))
    return np.array(rows).reshape(-1, 3)


def _is_railing(g: _Group, verticals: np.ndarray, hsegs: list[tuple[float, float, float]]) -> bool:
    """A balcony or a stair rail: as many evenly spaced balusters, close together, as no window has glazing bars, in a
    group no taller than a balustrade with a handrail along its top."""
    h = g.y1 - g.y0
    if h > RAIL_MAX or not any(abs(y - g.y1) <= ALIGN_TOL and min(b, g.x1) - max(a, g.x0) >= RAIL_TOP * (g.x1 - g.x0)
                               for y, a, b in hsegs):
        return False
    inside = verticals[(verticals[:, 0] > g.x0 + ALIGN_TOL) & (verticals[:, 0] < g.x1 - ALIGN_TOL)
                       & (verticals[:, 2] - verticals[:, 1] >= RAIL_HEIGHT * h) & (verticals[:, 1] >= g.y0 - ALIGN_TOL)
                       & (verticals[:, 2] <= g.y1 + ALIGN_TOL), 0]
    xs = np.sort(inside)
    bars = xs[np.concatenate(([True], np.diff(xs) > BAR_MERGE))] if len(xs) else xs  # the first line of each bar
    if len(bars) < RAIL_BARS:
        return False
    pitch = np.diff(bars)
    run = best = 1
    for a, b in zip(pitch, pitch[1:]):
        run = run + 1 if abs(a - b) <= RAIL_REGULAR * max(a, b) and max(a, b) <= RAIL_PITCH else 1
        best = max(best, run)
    return best + 1 >= RAIL_BARS


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


def _ground_line(hsegs: list[tuple[float, float, float]], span: float, verticals: np.ndarray) -> float | None:
    """The top of the ground line: the lowest horizontal line as long as the building that something tall rises from
    (a dimension string under the drawing is no ground), or a double one's upper line. If none carries anything, the
    lowest long one."""
    longest = sorted((y, a, b) for y, a, b in hsegs if b - a >= GROUND_SHARE * span)
    if not longest:
        return None
    longest = [t for t in longest if _carries(t[0], verticals)] or longest
    y = longest[0][0]
    above = [yy for yy, _, _ in longest if y < yy <= y + GROUND_DOUBLE]
    return max(above) if above else y


def _mode(values: list[float], tol: float) -> float:
    """The value most of the others are within ``tol`` of (the lowest on a tie), averaged with them."""
    best = max(values, key=lambda v: (sum(abs(u - v) <= tol for u in values), -v))
    return float(np.mean([u for u in values if abs(u - best) <= tol]))


def _figure_feet(doc: Drawing, scale: float) -> list[tuple[float, float]]:
    """(x, y) in metres of the feet of the person figures drawn in the sheet: block references, as tall as a person,
    whose insertion point is at the bottom. ``scale``: metres per drawing unit."""
    extents: dict[str, tuple[float, float, float, float] | None] = {}
    feet = []
    for e in doc.modelspace().query("INSERT"):
        if abs(float(e.dxf.get("rotation", 0))) > 1:
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
        width, height = (ext[2] - ext[0]) * sx * scale, (ext[3] - ext[1]) * abs(sy) * scale
        foot = ext[1] * abs(sy) * scale
        if sy > 0 and FIGURE_HEIGHT[0] <= height <= FIGURE_HEIGHT[1] and width <= FIGURE_WIDTH \
                and abs(foot) <= FIGURE_FOOT:
            feet.append((e.dxf.insert.x * scale, e.dxf.insert.y * scale))
    return feet


@dataclass
class _Sheet:
    """The modelspace of a drawing read once for all its elevations (reading it takes seconds, a sheet has ten
    views): items in metres with an index over their pieces, the texts and the feet of the figures."""

    cfg: Config
    unit: str
    scale: float  # metres per drawing unit
    size: int  # entities of the modelspace when it was read: more or fewer, and the drawing was edited
    items: list[Item]
    tree: STRtree | None
    owner: np.ndarray  # the index in ``items`` of each piece the tree holds
    texts: list[RawText]
    feet: list[tuple[float, float]]

    def items_in(self, area: tuple[float, float, float, float]) -> list[Item]:
        """The items with a piece that touches the area (metres), in drawing order."""
        if self.tree is None:
            return []
        hit = self.tree.query(box(*area), predicate="intersects")
        return [self.items[i] for i in np.unique(self.owner[hit])]

    def texts_in(self, area: tuple[float, float, float, float]) -> list[RawText]:
        """The texts within a metre of the area: a label may sit just outside the drawing it names."""
        x0, y0, x1, y1 = area
        return [t for t in self.texts if x0 - TEXT_MARGIN <= t.x <= x1 + TEXT_MARGIN
                and y0 - TEXT_MARGIN <= t.y <= y1 + TEXT_MARGIN]

    def feet_in(self, area: tuple[float, float, float, float]) -> list[float]:
        return [y for x, y in self.feet if area[0] <= x <= area[2] and area[1] <= y <= area[3]]


_SHEETS: "weakref.WeakKeyDictionary[Drawing, _Sheet]" = weakref.WeakKeyDictionary()


def _sheet(doc: Drawing, cfg: Config, unit: str, unit_scale: float) -> _Sheet:
    """The sheet of this drawing, read the first time and kept for as long as the drawing is (same settings, same unit
    and the same number of entities)."""
    size = len(doc.modelspace())
    known = _SHEETS.get(doc)
    if known is not None and (known.unit, known.scale, known.size) == (unit, unit_scale, size) and known.cfg == cfg:
        return known
    items = read_items(doc, cfg, area=None, ignore_veto=True, keep_other=True, unit=unit, keep_fills=True).items
    pieces = [(i, p.geom) for i, it in enumerate(items) for p in it.prims]
    tree = STRtree([g for _, g in pieces]) if pieces else None
    owner = np.array([i for i, _ in pieces], dtype=int)
    sheet = _Sheet(copy.deepcopy(cfg), unit, unit_scale, size, items, tree, owner, read_texts(doc, cfg, unit_scale),
                   _figure_feet(doc, unit_scale))
    _SHEETS[doc] = sheet
    return sheet


# --- the symbols ----------------------------------------------------------------------------------------------

@dataclass
class _Named:
    """A symbol the layer names say is a door or a window."""

    sym: FoundSymbol
    mixed: bool  # on a layer that holds every opening ("Infissi"): door or window is told by where it stands


def _is_tiling(g: _Group) -> bool:
    """A grid of small leaves all of one size, at least TILE_GRID of them across and down: the tiles of a wall. (The
    slats of a louvred window are two or three across.)"""
    cells = g.members
    if len(cells) < TILE_GRID ** 2 or not all(max(c.w, c.h) <= TILE_MAX and abs(c.w - cells[0].w) <= ALIGN_TOL
                                              and abs(c.h - cells[0].h) <= ALIGN_TOL for c in cells):
        return False
    columns = _columns(g)
    return len(columns) >= TILE_GRID and max(len(c) for c in columns) >= TILE_GRID


def _is_opening(g: _Group) -> bool:
    """The proportions and the drawing of a door or window: not a strip, not a post, not a bay of the wall, not a grid
    of tiles."""
    w, h = g.core[1] - g.core[0], g.y1 - g.y0
    if _is_tiling(g):
        return False
    if g.x1 - g.x0 < SYMBOL_MIN[0] or h < SYMBOL_MIN[1] or w / h > MAX_ASPECT or h / w > MAX_TALL:
        return False
    if w < SLIT_WIDTH and h < SLIT_ASPECT * w:
        return False  # a narrow opening is a slit, taller than wide: a small square is a post, a vent
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
    """The door stands on the steps of an entrance: strips one under the other, as wide as the door, down to the
    floor."""
    y, gap = g.y0, STEP_THRESHOLD
    while y > floor + FLOOR_TOL:
        below = [s for s in strips
                 if 0 <= y - s.y1 <= gap and s.x0 <= g.core[0] + ALIGN_TOL and s.x1 >= g.core[1] - ALIGN_TOL]
        if not below:
            return False
        y, gap = min(s.y0 for s in below), STEP_GAP
    return True


def _carries(ground: float, verticals: np.ndarray) -> bool:
    """Something tall rises from the ground line: the end of a wall, a jamb, a column. A long line with nothing on it
    (the underline of a title, the edge of a sheet) is no ground."""
    bottom = verticals[:, 1]
    return bool(np.any((verticals[:, 2] - bottom >= DOOR_HEIGHT) & (bottom >= ground - GROUND_DOUBLE - FLOOR_TOL)
                       & (bottom <= ground + FLOOR_TOL)))


def _find_floor(door_feet: list[float], levels_found: list[tuple[float, float]], ground: float | None,
                feet: list[float], grounded: bool = False) -> tuple[float | None, str]:
    """The floor of the ground storey and where it comes from, in order of trust. ``grounded``: walls stand on the ground
    line, so doors a storey above it are those of an upper storey (a view with a single ground door among the french
    windows of the floor above, or none at all) and the floor is the ground line."""
    if levels_found:
        return _mode([y - v for y, v in levels_found], FLOOR_VOTE), "quota +0,00"
    if door_feet:
        level = _level_of_doors(door_feet)
        if grounded and ground is not None and level >= ground + STOREY_MIN:
            return ground, "linea di terra"
        return level, "porta"
    if ground is not None:
        return ground, "linea di terra"
    if feet:
        return float(np.median(feet)), "blocco figura"
    return None, ""


def _slabs(base: float, bands: list[tuple[float, float, float, float]],
           hsegs: list[tuple[float, float, float]]) -> list[float]:
    """y of the floors of the storeys above ``base`` that the openings show without any mark: a line (the slab) between
    a row of openings and the row above it, as long as half the width the openings span (the longest such line, the
    highest on a tie), with no opening across it. ``bands``: (x0, x1, bottom, top) of each opening."""
    found: list[float] = []
    if not bands:
        return found
    width = max(t[1] for t in bands) - min(t[0] for t in bands)
    while True:
        lines = sorted(((b - a, y) for y, a, b in hsegs if base + STOREY_MIN <= y <= base + STOREY_MAX
                        and b - a >= SLAB_SPAN * width), reverse=True)
        for _, y in lines:
            below = any(base - SLAB_SLACK <= t[2] and t[3] <= y + SLAB_SLACK for t in bands)
            above = any(t[2] >= y - SLAB_SLACK for t in bands)
            across = any(t[2] < y - SLAB_SLACK and t[3] > y + SLAB_SLACK for t in bands)
            if below and above and not across:
                found.append(y)
                base = y
                break
        else:
            return found


def _shown(y: float, bands: list[tuple[float, float, float, float]], hsegs: list[tuple[float, float, float]],
           verticals: np.ndarray, span: float) -> bool:
    """A storey is built from this level up: a row of openings stands on it or above it, or a slab line runs along it
    and a wall goes on above (the eaves and the ridge are marked too, but a wall ends at the eaves)."""
    if any(t[2] >= y - SLAB_SLACK for t in bands):
        return True
    slab = any(abs(yy - y) <= FLOOR_SNAP and b - a >= SLAB_SHARE * span for yy, a, b in hsegs)
    return slab and bool(np.any((verticals[:, 1] <= y + FLOOR_TOL) & (verticals[:, 2] >= y + DOOR_HEIGHT)))


def _storeys(floor: float, levels_found: list[tuple[float, float]], upper_feet: list[float],
             hsegs: list[tuple[float, float, float]], verticals: np.ndarray, span: float,
             bands: list[tuple[float, float, float, float]]) -> list[float]:
    """y of the floors of all the storeys the view shows, from the marks of the other floors ("+3,20") where openings or
    a slab show a storey, from the long line (a slab, a balcony) a door of an upper storey stands on, or else from the
    slab between two rows of openings; empty if the view shows one storey. Each floor lies STOREY_MIN to STOREY_MAX above
    the one under it: a wrong floor (the eaves, the ridge) would set the height of the walls of the whole building."""
    ys = [y for y, v in levels_found if abs(y - v - floor) <= FLOOR_TOL
          and _shown(y, bands, hsegs, verticals, span)]
    for foot in upper_feet:
        slab = [y for y, a, b in hsegs if foot - SLAB_DEPTH <= y <= foot + 2 * ROW_TOL and b - a >= SLAB_SHARE * span]
        if slab:
            ys.append(max(slab))

    def spaced(found: list[float]) -> list[float]:
        levels = [floor]
        for y in sorted(found):
            if y - levels[-1] > STOREY_MAX:
                break
            if y - levels[-1] >= STOREY_MIN:
                levels.append(y)
        return levels

    levels = spaced(ys)
    if len(levels) == 1:  # no mark or door shows a storey: the slab between two rows of openings may
        levels = spaced(_slabs(floor, bands, hsegs))
    return levels if len(levels) > 1 else []


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


def _area(a: Symbol) -> float:
    return (a.x1 - a.x0) * (a.y1 - a.y0)


def _overlap(a: Symbol, b: Symbol) -> float:
    """The area the two symbols have in common."""
    return max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0)) * max(0.0, min(a.y1, b.y1) - max(a.y0, b.y0))


def _covers(named: Symbol, shape: FoundSymbol) -> bool:
    """The layer's symbol is (a part of) the opening the shape draws: it lies within it, or they cover each other and
    are about the same size."""
    whole = Symbol(shape.kind, *(shape.full_x or (shape.x0, shape.x1)), shape.y0, shape.y1)
    inter, area_n, area_s = _overlap(named, whole), _area(named), _area(whole)
    if inter >= INSIDE * area_n:
        return True
    return inter >= SAME_OVERLAP * min(area_n, area_s) and SAME_SYMBOL[0] <= area_n / area_s <= SAME_SYMBOL[1]


def _part_of(shape: FoundSymbol, named: Symbol) -> bool:
    """The shape is a piece of an opening the layers name (a leaf cut out by a railing in front of it), far smaller."""
    return _overlap(shape, named) >= INSIDE * _area(shape) and _area(named) >= PART_RATIO * _area(shape)


def _head_of(shape: FoundSymbol, named: Symbol) -> bool:
    """The shape is the lintel box (the shutter box, the sill) of a symbol the layers name: as wide as it, no taller
    than HEAD_MAX, and touching it from above or from below."""
    touching = abs(shape.y0 - named.y1) <= TOUCH or abs(shape.y1 - named.y0) <= TOUCH
    return shape.y1 - shape.y0 <= HEAD_MAX and abs(shape.x0 - named.x0) <= CASING \
        and abs(shape.x1 - named.x1) <= CASING and touching


def _is_low(y0: float, h: float, floor: float | None) -> bool:
    """A window whose bottom is on the floor (or lower) and no taller than a metre or so is a planter, a step or a
    vent."""
    return floor is not None and y0 <= floor + LOW_ON_FLOOR and h <= LOW_HEIGHT


def _framed(n0: float, n1: float, s0: float, s1: float) -> tuple[float, float]:
    """The extent of a named symbol with the frame its shape draws around it: a side of the shape up to CASING outside
    the named one is the frame of its glass; further out (a stone surround, a step) or inside it, the named side stays."""
    return s0 if n0 - CASING <= s0 < n0 else n0, s1 if n1 < s1 <= n1 + CASING else n1


def _combine(named: list[_Named], shapes: list[FoundSymbol], floor: float | None, levels: list[float]) -> list[Symbol]:
    """The layers are the primary answer: their kind and their size stay. The shape of the same opening gives them the
    frame around the glass (up to CASING), the apex of an arch, the glass (``pane``) and the shutters (``full_x``); it
    gives the clear width instead where the layer names the window with its shutters folded beside it. Shapes that match
    no named symbol are the openings the names do not give."""
    out: list[Symbol] = []
    taken: dict[int, list[_Named]] = {}
    alone: list[_Named] = []
    shapes = [s for s in shapes if not any(_part_of(s, n.sym) or _head_of(s, n.sym) for n in named)]
    for n in named:
        twins = [i for i, s in enumerate(shapes) if _covers(n.sym, s)]
        if twins:
            taken.setdefault(min(twins, key=lambda i: _area(shapes[i])), []).append(n)
        else:
            alone.append(n)
    for i, s in enumerate(shapes):
        if i not in taken:
            out.append(s)
            continue
        says = [n.sym.kind for n in taken[i] if not n.mixed]
        kind = ("door" if "door" in says else "window") if says else s.kind
        n0, n1 = min(n.sym.x0 for n in taken[i]), max(n.sym.x1 for n in taken[i])
        y0, y1 = _framed(min(n.sym.y0 for n in taken[i]), max(n.sym.y1 for n in taken[i]), s.y0, s.y1)
        if s.arched:
            y1 = max(y1, s.y1)  # the apex of a head the layer does not draw
        full = s.full_x
        if n0 - s.x0 > FRAME_BAND or s.x1 - n1 > FRAME_BAND:  # the shape adds a leaf the layer does not call the window
            x0, x1, full = n0, n1, (s.x0, s.x1) if full is None else full
        elif full is not None and abs(full[0] - n0) <= CASING and abs(full[1] - n1) <= CASING:
            x0, x1 = s.x0, s.x1  # the layer names the window with its shutters folded beside it: the shape has the clear width
        else:
            x0, x1 = _framed(n0, n1, s.x0, s.x1)
        out.append(FoundSymbol(kind, x0, x1, y0, y1, "layer+forma", s.arched, s.pane, full))
    for n in alone:
        if n.mixed:
            n.sym.kind = _kind(n.sym.y0, n.sym.y1 - n.sym.y0, floor, levels)
            if n.sym.kind == "window" and _is_low(n.sym.y0, n.sym.y1 - n.sym.y0, floor):
                continue
        out.append(n.sym)
    return sorted(out, key=lambda s: (s.x0, s.y0))


def _without_outline(groups: list[_Group], lw: _Linework) -> list[_Group]:
    """The groups but the outline of the facade: a lone frame with nothing in it, as wide as all the drawing above its
    middle, is the wall of a small building with no opening drawn in it, not a door as wide as the building."""
    if not lw.lines:
        return groups
    bounds = shapely.bounds(np.array(lw.lines, dtype=object))

    def outline(g: _Group) -> bool:
        if len(g.members) > 1 or g.members[0].kids or g.x1 - g.x0 < OUTLINE_MIN:
            return False
        above = bounds[bounds[:, 3] >= (g.y0 + g.y1) / 2]
        return len(above) > 0 and g.x1 - g.x0 >= OUTLINE_SHARE * (above[:, 2].max() - above[:, 0].min())

    return [g for g in groups if not outline(g)]


def _roofed(groups: list[_Group], lw: _Linework) -> list[_Group]:
    """The symbols with a roof, an eave or the top of the wall above them: a line as long as a roof, not the cap of a
    chimney, or the edge of a hatch or a solid as wide (a roof drawn as a fill has no line). What stands in the open
    sky above the roof (a chimney pot, a stack) is no opening."""
    long_lines = [g for g in lw.lines if g.bounds[2] - g.bounds[0] >= ROOF_SPAN]
    long_lines += [LineString(g.exterior.coords) for g in lw.glass if g.bounds[2] - g.bounds[0] >= ROOF_SPAN]
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
        notes.append(f"Il fondo della porta piu' bassa (y {min(door_feet):.2f} m) non coincide con la quota del "
                     "pavimento.")
    return notes


def detect_view_symbols(items: list[Item], texts: list[RawText], feet: list[float],
                        area: tuple[float, float, float, float], cfg: Config) -> ViewSymbols:
    """Doors, windows and floor level of one elevation, from its items (metres, as ``read_items`` gives them with
    ``keep_other`` and ``keep_fills``), its texts, the y of the feet of its person figures and its area in metres."""
    areas = sum(bool(AREA_MARK.search(t.text)) for t in texts)
    if areas >= PLAN_AREAS:
        return ViewSymbols(notes=[f"La vista ha {areas} superfici scritte (mq): e' una pianta, non un prospetto."])
    lines = _lines(items, area)
    segments = int(shapely.get_num_coordinates(lines).sum()) - len(lines)
    if segments > SEGMENTS_MAX:
        note = f"La vista ha {segments} tratti di linea: troppi per un prospetto (e' una pianta o una planimetria)."
        return ViewSymbols(notes=[note + " Non la leggo."])
    lw = _linework(items, lines)
    everything = _groups(lw)
    groups = [g for g in everything if g.x1 - g.x0 >= SYMBOL_MIN[0] and g.y1 - g.y0 >= SYMBOL_MIN[1]]
    strips = [g for g in everything if _is_strip(g.members[0]) and g.x1 - g.x0 > g.y1 - g.y0]
    verticals = _verticals(lw.lines)
    hsegs = _horizontals(lw.lines, MARK_LINE_MIN)
    candidates = _without_outline([g for g in groups if _is_opening(g) and not _is_railing(g, verticals, hsegs)], lw)
    shapes = _roofed(candidates, lw)
    roofless = bool(candidates) and not shapes
    if roofless:  # no roof, eave or wall top over any of them: an elevation drawn without one, not a view of details
        shapes = candidates
    span = area[2] - area[0]
    named = _named(items, cfg)
    marks = _mark_levels(_marks(texts), hsegs, _triangles(lw))

    door_feet = [n.sym.y0 for n in named if n.sym.kind == "door" and not n.mixed]
    door_feet += [g.y0 for g in shapes if g.y1 - g.y0 >= DOOR_HEIGHT_UNKNOWN]
    ground = _ground_line(hsegs, span, verticals)
    lowest = [n.sym.y0 for n in named] + [g.y0 for g in shapes]
    if ground is not None and lowest and ground > min(lowest) + FLOOR_TOL:
        ground = None  # a line above the bottom of an opening is the eaves or the roof: the ground is out of the view
    floor, source = _find_floor(door_feet, marks, ground, feet, ground is not None and _carries(ground, verticals))
    if floor is not None and source == "quota +0,00":
        near = [y for y, a, b in hsegs if b - a >= GROUND_SHARE * span and abs(y - floor) <= FLOOR_SNAP]
        floor = min(near, key=lambda y: abs(y - floor)) if near else floor
    levels: list[float] = []
    if floor is not None:
        upper = [g.y0 for g in shapes if g.y1 - g.y0 >= DOOR_HEIGHT_UNKNOWN and g.y0 >= floor + STOREY_MIN]
        bands = [(g.core[0], g.core[1], g.y0, g.y1) for g in shapes] \
            + [(n.sym.x0, n.sym.x1, n.sym.y0, n.sym.y1) for n in named]
        levels = _storeys(floor, marks, upper, hsegs, verticals, span,
                          [t for t in bands if not _is_low(t[2], t[3] - t[2], floor)])

    below = -math.inf if floor is None else floor - STOREY_MAX  # what stands further down is another drawing
    found = []
    for g in shapes:
        if g.y0 < below:
            continue
        kind = _kind(g.y0, g.y1 - g.y0, floor, levels)
        if kind == "window" and _is_low(g.y0, g.y1 - g.y0, floor) and not g.arched:  # an arch is an opening, whatever
            continue  # the ground hides of it
        if kind == "window" and floor is not None and g.y1 - g.y0 >= DOOR_HEIGHT and g.y0 - floor <= STEPS_MAX \
                and _on_steps(g, strips, floor):
            kind = "door"
        found.append(FoundSymbol(kind, g.core[0], g.core[1], g.y0, g.y1, "forma", g.arched, g.pane,
                                 (g.x0, g.x1) if g.core != (g.x0, g.x1) else None))
    notes = _floor_notes(floor, source, door_feet)
    if roofless:
        notes.append("Nessun tetto, gronda o sommita' del muro sopra le aperture: le forme sono prese senza la prova "
                     "del tetto (comignoli e dettagli non si distinguono), meno sicure.")
    if len(groups) > len(found):
        notes.append(f"{len(groups) - len(found)} figure scartate: fasce, specchiature di parete, vasi, gradini, "
                     "comignoli: non sono porte o finestre.")
    symbols = _combine([n for n in named if n.sym.y0 >= below], found, floor, levels)
    return ViewSymbols(symbols, floor, source, levels, [(y, a, b) for y, a, b in hsegs if b - a >= MIN_LINE], notes)


def read_view_symbols(doc: Drawing, cfg: Config, bbox: tuple[float, float, float, float], unit: str,
                      unit_scale: float) -> ViewSymbols:
    """Doors, windows and floor level of the elevation inside ``bbox`` (drawing units), whatever its layers are
    called. ``unit``/``unit_scale``: the drawing unit already decided by the main read, and its size in metres."""
    x0, y0, x1, y1 = (v * unit_scale for v in bbox[:4])
    area = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or area[2] <= area[0] or area[3] <= area[1]:
        return ViewSymbols(notes=["La vista non ha un'area (vuota o non valida): nessuna porta o finestra letta."])
    sheet = _sheet(doc, cfg, unit, unit_scale)
    return detect_view_symbols(sheet.items_in(area), sheet.texts_in(area), sheet.feet_in(area), area, cfg)
