"""Understand a drawing without trusting its layers.

A person who opens an unfamiliar drawing does not read the layer names first: they see a sheet with several views
(a plan, elevations, a section, a site plan), titles under them, rooms with names and areas, thick wall outlines,
door arcs, dimension lines. This module looks for the same things in the geometry and the texts:

* ``scan``            one fast pass over the modelspace into numpy arrays (segments, arcs, hatches, texts, blocks...);
* ``infer_unit``      the unit the numbers are really in (dimension values, door arcs...), whatever the header says;
* ``find_views``      the views of the sheet: groups of drawing separated by empty space (a long line, a frame, a stray
                      mark does not join two of them), the title of each (the little frame it is written in is not
                      part of any drawing), the plans drawn inside a bigger view (a house on its lot);
* the type of each view: plan, roof plan, elevation, section, site plan, detail: from the title, else from what the
  view holds (door arcs and room areas, level marks, room names, contour lines, roof tiles);
* the copies: a plan drawn twice (one of them turned, or drawn in the site) is found from the words written in it and
  confirmed by its lines; two floors of a house with the same footprint are not copies.

Nothing here builds a 3D model: it says what is where, and with what confidence; the pipeline decides what to do.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field, replace

import numpy as np

from .config import INSUNITS_TO_NAME, UNIT_TO_METERS

STRAIGHT = 1e-9  # a polyline piece with a smaller bulge than this is a straight segment
CELL = 0.5  # m: the size of the cells the sheet is divided in to find the views
LINK = 1.5  # m: pieces of drawing closer than this are one view; empty space wider than this separates two views
LONG_LINE = 12.0  # m: a straight line this long (ground line, section mark, border of the sheet, a table) is a ruling
BARE_RUN = 4.0  # m: ... where it runs through empty space for this long (more than a gap in a drawing) it is cut out
MIN_VIEW_CELLS = 40  # a piece of fewer cells than this (10 m2 of drawing) is a crumb, not a view
MIN_VIEW_SIDE = 2.5  # m: a piece thinner than this is a strip of text or a line, not a view
FLOAT_PAD = 0.001  # m: a view holds what lies this close to its box too (the box is the box of the ink: a hinge sits on its edge)
MAX_CELLS = 1e8  # a coordinate more cells than this from the origin is damage (NaN, 1e300), not drawing: it is left out
MAX_VIEWS = 200  # a sheet is not read as having more views than this (the biggest are kept): the views are compared in pairs
MAX_TITLES = 300  # ... nor as having more titles than this (the tallest letters are kept): each is looked for among all the lines
OUTLINE_LINES = 3  # a drawing made of long lines only (the boundary of a lot) is a view if it has this many of them
CRUMB_REACH = 3.0  # m: a crumb this close to a view is a part of it (a note, a north arrow, a stair)
CRUMB_SHARE = 0.15  # ... if it holds at most this share of the cells of that view
INSIDE_SHARE = 0.8  # a view with this share of its box in the box of another one lies in it
EDGE_SHARE = 0.25  # long lines with this share of their box on a drawing are the edge of it (its lot), not a view
STRAY_SHARE = 0.02  # a piece with less than this share of the cells of a view near it is a stray piece of it...
STRAY_REACH = 10.0  # m: ... "near" is this close to its box (a few contour lines cut off by gaps, a lone arrow)
FRAME_SEED = 1.0  # m: the frame of a title is this close to the title, at most
FRAME_TOUCH = 0.05  # m: two lines of a frame touch when their ends are this close
FRAME_SEGMENTS = 16  # a frame has at most this many lines (it is often drawn twice)
FRAME_SIZE = (20.0, 3.0)  # m: a frame is at most this long and this thick
TITLE_REACH = 8.0  # m: a title belongs to a view it is this close to (a gap of 2 cm on a sheet at 1:200 is 4 m)
TITLE_TIE = 1.0  # m: views this much farther than the nearest are about as near: the habit of the sheet decides
SITE_RATIO_OTHER = 6.0  # a plan this many times bigger than another one is the site even with another title
MIN_PLAN_CELLS = 40  # ... if that other one is at least this big (10 m2 of drawing): not a detail
SITE_RATIO = 2.5  # a plan this many times bigger than another view with the same title, or lying in it, is the site
COPY_SITE_RATIO = 1.5  # ... this many times if the plan in it is the copy of a plan of the sheet: the box is then exact
PLAN_DOORS = 3  # a view with this many swing arcs of door size is a plan
PLAN_AREAS = 3  # ... or with this many areas written in it
FACADE_ASPECT = 2.5  # a drawing this much wider than high, with no door arcs, is a facade
SECTION_ROOMS = 3  # a view with this many different room names written in it, and no doors or areas, is a section
MAX_TITLE = 60  # characters: a longer text is a note, unless it starts like a caption ("SEZIONE - A Superficie...")
LEVEL_MARKS = 2  # a view with this many level marks (+3,20) is an elevation or a section, not a plan
SITE_ORTHO = 0.4  # a view where fewer lines than this share run along two directions is made of contours or boundaries
SITE_SIZE = 30.0  # m: ... and it is a site if it is this wide
NEST_DOORS = 5  # a building drawn in a bigger view has at least this many swing doors, all in one place
NEST_LINK = 10.0  # m: swing doors this close to one another are in the same building
NEST_MARGIN = 3.0  # m: a building reaches this far beyond its outermost swing doors
NEST_ORTHO = 0.9  # a view of which more of the lines than this run along two directions is a plan, not a lot with a house on it
NEST_AREA = 0.3  # a plan in a view takes up less than this share of the box of the view...
NEST_DENSITY = 1.3  # ... and holds this many times more drawing per square metre than the rest of it
COPY_TEXTS = 3  # the texts propose a copy: this many different words of a drawing land on the same words, all moved alike
COPY_TOLERANCE = 0.1  # m: a text of the copy lies this close to where the move of the copy puts it
COPY_REPEATS = 12  # a text written more often than this (a dimension like 100) says nothing about where a copy lies
COPY_PAIRS = 200_000  # no more pairs of equal texts than this are tried...
COPY_MOVES = 60  # ... and no more moves than this (the best supported) are looked at
COPY_INK = 0.6  # the lines confirm it: this share of the lines of the smaller drawing lands on lines of the same length
#   (a copy reaches 0.8 and more; two floors of a house, with the same walls and other partitions, 0.3)
COPY_INK_TOLERANCE = 0.05  # m: ... within this distance
COPY_SIZE = 0.05  # two views are twins only if their sides differ by less than this share
COPY_VOTERS = 150  # the longest lines of a drawing vote for the shift that takes it onto its twin...
COPY_PARTNERS = 50  # ... each for the shifts to the lines of nearly its length, this many looked at...
COPY_VOTES = 6  # ... and a shift needs this many votes
COPY_FIT = 0.5  # a view is the copy when its box overlaps the box of the moved drawing by this share of the two together
COPY_ROUGH = 0.3  # a plan found by its doors is the copy when this share of its box (and its middle) lies in the copy's box
COPY_INSIDE = 0.85  # a copy lies in a bigger view when this share of its box does
ORTHO_MIN_LINES = 50  # the direction of the lines tells something with at least this many
ORTHO_TOLERANCE = 2  # degrees: lines this close to the main direction (or across it) run along it
ROOF_TILES = 3  # a view with this many hatches of roof tiles, no door arcs and no level marks is a roof plan
TILE_PATTERNS = ("COPPI", "ROMAN", "TEGOL", "TILE", "RROOF", "RSHKE", "SHINGLE", "EMBRIC")  # in the names of hatch patterns
LEVEL_MARK = re.compile(r"^(?:[A-Za-z.]{0,8}\s*)?[+\-\u00b1\u2212]\s*\d{1,4}[.,]\d{1,2}\b")  # +3,20  - 0.40  +/-0.00
CAPTION = re.compile(r"^[A-Za-z' ]+?\s*(?:[-:\u2013]\s*(?:[A-Z]|\d{1,2})(?:-(?:[A-Z]|\d{1,2}))?|\s(?:[A-Z]-[A-Z]|\d{1,2}-\d{1,2}))\s")  # SEZIONE - A Sup...
AREA_MARK = re.compile(r"(?<![A-Za-z0-9])(?:mq|m2|m\u00b2)(?![A-Za-z0-9])", re.I)  # 12,5 mq

UNIT_CHOICES = ("m", "cm", "mm")
OLD_UNITS = ("in", "ft")  # a header in these is voted on as one more unit, and overruled only by a clear majority
UNIT_SURE = 2.0  # with no unit in the header (or one in inches or feet) the analysis decides when its best unit leads by this many votes
MIN_PLAN_SIDE = 3.0  # m: a plan thinner than this is a fragment (the title block of a sheet read in the wrong unit), not a plan
MAX_GRID_CELLS = 4e5  # a sheet spread over more cells than this (a unit 100 times too small) is looked at through bigger cells
DOOR_RADIUS = (0.55, 1.40)  # m: the radius of a swing arc
DIMENSION_RANGE = (0.5, 15.0)  # m: where the median of the dimension values of a plan should lie
DIMENSION_FAR = (15.0, 60.0)  # m: ... and where it lies in a big hall that is dimensioned overall only: unusual, but possible
FAR_LIKELIHOOD = 0.4  # ... so a unit that puts the median there gets this share of the vote of a unit that puts it in the range
TEXT_HEIGHT = (0.05, 0.60)  # m: where the median height of the texts of a drawing should lie
CORE_RANGE = (1.5, 1500.0)  # m: the size of the dense core of the sheet, at least, at most (several drawings fit in it)
GARDEN_REACH = 25.0  # m: a garden does not lie farther than this from the plan (or from the lot it is drawn in)
OVERRIDE_MARGIN = 1.0  # the header is overruled only by a unit that leads by this many votes

# the words that say what a view is, strongest first (the text is upper-cased and stripped of punctuation)
VIEW_WORDS = (
    ("roof", ("PLANIMETRIA COPERTURA", "PIANTA COPERTURA", "PIANTA TETTO", "COPERTURA", "COPERTURE", "TETTO", "ROOF PLAN", "ROOF")),
    ("elevation", ("PROSPETTO", "PROSPETTI", "PROSP", "FRONTE", "FRONTI", "ALZATO", "FACCIATA", "ELEVATION", "ELEVAZIONE")),
    ("section", ("SEZIONE", "SEZIONI", "SEZ", "SECTION")),
    ("site", ("PLANIMETRIA GENERALE", "INQUADRAMENTO", "SITE PLAN", "PLANIMETRIA DI INSERIMENTO", "ESTRATTO",
              "PLANIMETRIA CATASTALE", "CATASTALE", "PLANIMETRIA DI ZONA", "PLANIMETRIA LOTTO", "ORTOFOTO")),
    ("detail", ("DETTAGLIO", "PARTICOLARE", "DETAIL")),
    ("plan", ("PIANTA", "PIANTE", "PLANIMETRIA", "PLANIMETRIE", "PIANO TERRA", "PIANO PRIMO", "PIANO SECONDO", "PIANO INTERRATO",
              "FLOOR PLAN", "GROUND FLOOR", "FIRST FLOOR", "PLAN")),
)
# what a caption may say before the word that tells the type: the state of the drawing ("STATO DI FATTO - PROSPETTO SUD")
STATES = ("STATO DI FATTO", "STATO DI PROGETTO", "STATO ATTUALE", "STATO DI COMPARAZIONE", "STATO SOVRAPPOSTO", "STATO MODIFICATO",
          "PROGETTO", "RILIEVO", "ESISTENTE", "VARIANTE")
NUMBER = re.compile(r"^(?:(?:TAV|TAVOLA|ALL|ALLEGATO|FIG|DIS|N|NR)\s+)?(?:[0-9]{1,2}|[A-Z])\s+(?=[A-Z]{3})")  # 1 - PIANTA  A) SEZIONE  TAV 3 PROSPETTO


@dataclass
class Soup:
    """Everything the drawing holds, as arrays, in drawing units. Built by ``scan``."""

    declared_unit: str | None
    layers: list[str]
    seg: np.ndarray  # (N, 4) x0, y0, x1, y1: LINEs and the straight pieces of polylines
    seg_layer: np.ndarray  # (N,) index into layers
    arcs: np.ndarray  # (M, 5) cx, cy, r, start deg, sweep deg: ARCs and the curved pieces of polylines
    arc_layer: np.ndarray
    circles: np.ndarray  # (K, 3) cx, cy, r
    curves: np.ndarray  # (S, 4) bounding boxes of SPLINEs and ELLIPSEs (clutter: furniture, contour lines)
    hatches: list[dict] = field(default_factory=list)  # layer, pattern, solid, bbox
    texts: list[dict] = field(default_factory=list)  # layer, text, x, y, height
    dims: list[dict] = field(default_factory=list)  # layer, x, y, value
    inserts: list[dict] = field(default_factory=list)  # layer, name, x, y
    entities: int = 0

    @property
    def counts(self) -> dict[str, int]:
        return {"segmenti": len(self.seg), "archi": len(self.arcs), "cerchi": len(self.circles),
                "curve": len(self.curves), "campiture": len(self.hatches), "testi": len(self.texts),
                "quote": len(self.dims), "blocchi": len(self.inserts)}


# --- the scan ---------------------------------------------------------------------------------

def _clean(text: str) -> str:
    """The text without the formatting codes of MTEXT ({\\H0.75x;...}, \\P, \\A1;...), on one line."""
    text = re.sub(r"\\[ACFHQTWfhpqtw][^;]*;", "", text)  # \H0.75x;  \C1;  \fArial|b0;  \A1;  \pxsm0.577;
    text = text.replace("\\P", " ").replace("\\~", " ").replace("{", "").replace("}", "")
    return " ".join(text.split())


def _bulge_arc(p0, p1, bulge: float):
    """(cx, cy, r, start deg, sweep deg) of the arc a polyline piece with a bulge draws."""
    from ezdxf.math import bulge_to_arc

    try:
        centre, start, end, radius = bulge_to_arc(p0, p1, bulge)
    except Exception:
        return None
    a0 = math.degrees(start)
    return centre.x, centre.y, radius, a0, (math.degrees(end) - a0) % 360.0 or 360.0


def scan(doc, layer_used=None) -> Soup:
    """One pass over the modelspace. ``layer_used(layer) -> bool`` leaves out the layers that are off or frozen."""
    layers: dict[str, int] = {}
    seg: list[tuple] = []
    seg_layer: list[int] = []
    arcs: list[tuple] = []
    arc_layer: list[int] = []
    circles: list[tuple] = []
    curves: list[tuple] = []
    hatches: list[dict] = []
    texts: list[dict] = []
    dims: list[dict] = []
    inserts: list[dict] = []
    n = 0

    def lid(name: str) -> int:
        return layers.setdefault(name, len(layers))

    def flipped(e) -> bool:
        """Drawn in an OCS whose x runs the other way (extrusion 0,0,-1): the x of its points is mirrored."""
        ext = e.dxf.get("extrusion", None)
        return ext is not None and ext[2] < 0

    def polyline(pts: list[tuple], closed: bool, layer: str, mirror: bool) -> None:
        if mirror:
            pts = [(-x, y, -b) for x, y, b in pts]
        if closed and len(pts) > 2:
            pts = pts + [pts[0]]
        for a, b in zip(pts, pts[1:]):
            if abs(a[2]) < STRAIGHT:
                seg.append((a[0], a[1], b[0], b[1]))
                seg_layer.append(lid(layer))
            else:
                arc = _bulge_arc((a[0], a[1]), (b[0], b[1]), a[2])
                if arc:
                    arcs.append(arc)
                    arc_layer.append(lid(layer))

    for e in doc.modelspace():
        t = e.dxftype()
        layer = e.dxf.layer
        if layer_used is not None and not layer_used(layer):
            continue
        n += 1
        try:
            if t == "LINE":
                s, f = e.dxf.start, e.dxf.end
                seg.append((s.x, s.y, f.x, f.y))
                seg_layer.append(lid(layer))
            elif t == "LWPOLYLINE":
                polyline(list(e.get_points("xyb")), bool(e.closed), layer, flipped(e))
            elif t == "POLYLINE":
                if e.is_2d_polyline:
                    pts = [(v.dxf.location.x, v.dxf.location.y, v.dxf.get("bulge", 0.0)) for v in e.vertices]
                    polyline(pts, bool(e.is_closed), layer, flipped(e))
            elif t == "SOLID":
                v = [e.dxf.get(k) for k in ("vtx0", "vtx1", "vtx3", "vtx2")]  # the corners in drawing order
                ring = [(p.x, p.y, 0.0) for p in v if p is not None]
                polyline([(-x, y, 0.0) for x, y, _ in ring] if flipped(e) else ring, True, layer, False)
            elif t == "ARC":
                c = e.dxf.center
                a0, a1 = e.dxf.start_angle, e.dxf.end_angle
                if flipped(e):
                    c, a0, a1 = (-c.x, c.y), 180.0 - a1, 180.0 - a0  # a mirror turns the arc round
                    arcs.append((c[0], c[1], e.dxf.radius, a0, (a1 - a0) % 360.0 or 360.0))
                else:
                    arcs.append((c.x, c.y, e.dxf.radius, a0, (a1 - a0) % 360.0 or 360.0))
                arc_layer.append(lid(layer))
            elif t == "CIRCLE":
                c = e.dxf.center
                circles.append((-c.x if flipped(e) else c.x, c.y, e.dxf.radius))
            elif t == "ELLIPSE":
                c, m = e.dxf.center, e.dxf.major_axis
                r = math.hypot(m.x, m.y)
                curves.append((c.x - r, c.y - r, c.x + r, c.y + r))
            elif t == "SPLINE":
                cp = [(p.x, p.y) for p in e.control_points]
                if cp:
                    xs, ys = zip(*cp)
                    curves.append((min(xs), min(ys), max(xs), max(ys)))
            elif t == "HATCH":
                xs, ys = [], []
                for path in e.paths:
                    if hasattr(path, "vertices"):
                        for v in path.vertices:
                            xs.append(v[0])
                            ys.append(v[1])
                    else:
                        for edge in path.edges:
                            for name in ("start", "end", "center"):
                                p = getattr(edge, name, None)
                                if p is not None:
                                    xs.append(p[0])
                                    ys.append(p[1])
                if xs:
                    lid(layer)
                    hatches.append({"layer": layer, "pattern": e.dxf.get("pattern_name", "") or "",
                                    "solid": bool(e.dxf.get("solid_fill", 0)),
                                    "bbox": (min(xs), min(ys), max(xs), max(ys))})
            elif t == "TEXT":
                txt = _clean(e.dxf.text)
                if txt:
                    texts.append({"layer": layer, "text": txt, "x": -e.dxf.insert.x if flipped(e) else e.dxf.insert.x,
                                  "y": e.dxf.insert.y, "height": e.dxf.height})
            elif t == "MTEXT":
                txt = _clean(e.text)
                if txt:
                    texts.append({"layer": layer, "text": txt, "x": e.dxf.insert.x, "y": e.dxf.insert.y,
                                  "height": e.dxf.char_height})
            elif t == "DIMENSION":
                try:
                    v = float(e.get_measurement())
                except Exception:
                    continue
                if v > 0:
                    dims.append({"layer": layer, "x": e.dxf.defpoint.x, "y": e.dxf.defpoint.y, "value": v})
            elif t == "INSERT":
                inserts.append({"layer": layer, "name": e.dxf.name,
                                "x": -e.dxf.insert.x if flipped(e) else e.dxf.insert.x, "y": e.dxf.insert.y})
        except Exception:
            continue  # one damaged entity never stops the analysis
    declared = None
    try:
        declared = INSUNITS_TO_NAME.get(int(doc.header.get("$INSUNITS", 0) or 0))
    except Exception:
        pass
    return Soup(
        declared_unit=declared,
        layers=sorted(layers, key=layers.get),
        seg=np.asarray(seg, float).reshape(-1, 4),
        seg_layer=np.asarray(seg_layer, int),
        arcs=np.asarray(arcs, float).reshape(-1, 5),
        arc_layer=np.asarray(arc_layer, int),
        circles=np.asarray(circles, float).reshape(-1, 3),
        curves=np.asarray(curves, float).reshape(-1, 4),
        hatches=hatches, texts=texts, dims=dims, inserts=inserts, entities=n,
    )


# --- the unit ---------------------------------------------------------------------------------

@dataclass
class UnitGuess:
    unit: str
    declared: str | None
    votes: dict[str, float]
    evidence: list[str]

    @property
    def scale(self) -> float:
        return UNIT_TO_METERS[self.unit]

    @property
    def differs(self) -> bool:
        return self.declared is not None and self.unit != self.declared and self.margin >= self.needed

    @property
    def needed(self) -> float:
        """How far ahead the best unit must be to overrule the header: a header in feet or inches is a unit that few of
        the drawings we meet are really in, but also one that nobody checks, so it takes a clear majority."""
        return UNIT_SURE if self.declared in OLD_UNITS else OVERRIDE_MARGIN

    @property
    def margin(self) -> float:
        """How far the best unit is ahead of the next one, in votes (0 when the unit was given)."""
        ranked = sorted(self.votes.values(), reverse=True)
        return ranked[0] - ranked[1] if len(ranked) > 1 else 0.0


def _core_size(soup: Soup) -> tuple[float, float]:
    """The width and height of the dense core of the drawing, in drawing units: the 2nd..98th percentile of the segments."""
    if len(soup.seg) == 0:
        return 0.0, 0.0
    xs = np.concatenate([soup.seg[:, 0], soup.seg[:, 2]])
    ys = np.concatenate([soup.seg[:, 1], soup.seg[:, 3]])
    return float(np.percentile(xs, 98) - np.percentile(xs, 2)), float(np.percentile(ys, 98) - np.percentile(ys, 2))


def _core_extent(soup: Soup) -> float:
    """The size of the dense core of the drawing, in drawing units (the bigger of its width and height)."""
    return max(_core_size(soup))


def infer_unit(soup: Soup) -> UnitGuess:
    """Vote on the unit of the numbers in the drawing: dimension values, door arcs, the height of the texts, the size of
    the drawing, and the header (the weakest, because it is the one that is wrong most often). A header in inches or feet
    stands in the vote as one more unit, overruled by a clear majority only: a centimetre sheet that says feet (the
    default of some converters) must not be read at the scale of feet. Whenever the header is not overruled the sheet is
    read in the unit it declares, as the conversion will."""
    declared = soup.declared_unit
    if declared is not None and declared not in UNIT_CHOICES and declared not in OLD_UNITS:
        return UnitGuess(declared, declared, {}, [])  # a unit we cannot vote on
    choices = UNIT_CHOICES + ((declared,) if declared in OLD_UNITS else ())
    votes = {u: 0.0 for u in choices}
    evidence: list[str] = []

    def vote(median: float, low: float, high: float, weight: float, what: str, far: tuple[float, float] | None = None) -> None:
        """Every unit that puts the median in the range shares the vote; one that puts it in the ``far`` range, where it
        is possible but unlikely, shares it with a smaller weight."""
        likely = {u: 1.0 for u in choices if low <= median * UNIT_TO_METERS[u] <= high}
        if far is not None:
            likely.update({u: FAR_LIKELIHOOD for u in choices if u not in likely and far[0] < median * UNIT_TO_METERS[u] <= far[1]})
        for u, p in likely.items():
            votes[u] += weight * p / sum(likely.values())
        if likely:
            evidence.append(f"{what} e' {median:g}: {'/'.join(likely)}")

    values = [d["value"] for d in soup.dims]
    if len(values) >= 3:
        vote(statistics.median(values), *DIMENSION_RANGE, 3.0, f"la mediana delle {len(values)} quote", DIMENSION_FAR)
    heights = [t["height"] for t in soup.texts if t["height"] > 0]
    if len(heights) >= 5:  # the letters of a drawing are 5 cm to 60 cm high, whatever the size of the sheet
        vote(statistics.median(heights), *TEXT_HEIGHT, 2.0, f"l'altezza mediana dei {len(heights)} testi")
    if len(soup.arcs):
        quarter = soup.arcs[(soup.arcs[:, 4] > 70) & (soup.arcs[:, 4] < 110)]
        if len(quarter) >= 3:
            for u in choices:
                s = UNIT_TO_METERS[u]
                share = float(np.mean((quarter[:, 2] * s >= DOOR_RADIUS[0]) & (quarter[:, 2] * s <= DOOR_RADIUS[1])))
                votes[u] += 2.0 * share
                if share >= 0.4:
                    evidence.append(f"{share:.0%} dei {len(quarter)} archi di 90 gradi misura quanto una porta in {u}")
    core = _core_extent(soup)
    if core > 0:  # a sheet can hold several drawings side by side: only the absurd is ruled out
        for u in choices:
            if CORE_RANGE[0] <= core * UNIT_TO_METERS[u] <= CORE_RANGE[1]:
                votes[u] += 0.5
            else:
                votes[u] -= 3.0  # smaller than a room, or bigger than a town: not in this unit
    if declared in votes:
        votes[declared] += 1.0
    # a tie goes to the header, else to centimetres (the unit of most sheets nothing else speaks for), never to metres
    best = max(choices, key=lambda u: (votes[u], u == declared, u == "cm"))
    guess = UnitGuess(best, declared, votes, evidence)
    if declared in votes and best != declared and not guess.differs:
        guess.unit = declared
    return guess


# --- the views --------------------------------------------------------------------------------

@dataclass
class View:
    id: int
    bbox: tuple[float, float, float, float]  # drawing units
    cells: int  # how much drawing it holds (0.5 m cells)
    titles: list[str] = field(default_factory=list)
    title_items: list[dict] = field(default_factory=list)
    kind: str = "?"  # plan | roof | elevation | section | site | detail | ?
    kind_from: str = ""  # titolo | contenuto
    copy_of: int | None = None  # the id of the view this one repeats
    parent: int | None = None  # the id of the bigger view this one lies in (a plan on its lot, a detail)
    segments: int = 0
    arcs: int = 0
    curves: int = 0
    hatches: int = 0
    doors: int = 0  # swing arcs
    texts: int = 0
    inserts: int = 0
    named: int = 0  # segments, hatches and blocks on layers the names call walls, doors or windows
    matched: str = ""  # an elevation: which facade of the plan it was matched to (written by elevmatch.match_views)
    levels: int = 0  # level marks written in it (+3,20  -0,40): what an elevation or a section has
    areas: int = 0  # areas written in it (MQ 12,5): what the rooms of a plan have
    rooms: int = 0  # different room names written in it
    tiles: int = 0  # hatches that look like roof tiles
    ortho: float = 0.0  # share of the lines that run along the main direction of the drawing, or across it
    outline: bool = False  # nothing but long lines (the boundary of a lot)

    def size_m(self, scale: float) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        return (x1 - x0) * scale, (y1 - y0) * scale

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        x0, y0, x1, y1 = self.bbox
        return x0 - margin <= x <= x1 + margin and y0 - margin <= y <= y1 + margin


_PACK = 1 << 31  # a cell (ix, iy) of the grid is kept as one integer: (ix + _SHIFT) * _PACK + iy + _SHIFT
_SHIFT = 1 << 30


def _pack(ix: np.ndarray, iy: np.ndarray) -> np.ndarray:
    return (ix + _SHIFT) * _PACK + (iy + _SHIFT)


def _unpack(key: np.ndarray) -> np.ndarray:
    return np.stack([key // _PACK - _SHIFT, key % _PACK - _SHIFT], axis=1)


def _along(seg: np.ndarray, step: float, cap: int) -> tuple[np.ndarray, np.ndarray]:
    """Points about every ``step`` along each segment (``cap`` pieces at most) and the segment each one is on."""
    if len(seg) == 0:
        return np.empty((0, 2)), np.empty(0, dtype=int)
    length = np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1])
    n = np.minimum(np.maximum((length / step).astype(int), 1), cap)
    rep = np.repeat(np.arange(len(seg)), n + 1)
    k = (np.arange(len(rep)) - (np.cumsum(n + 1) - (n + 1))[rep]) / n[rep]
    s = seg[rep]
    return np.stack([s[:, 0] + (s[:, 2] - s[:, 0]) * k, s[:, 1] + (s[:, 3] - s[:, 1]) * k], axis=1), rep


def _sample_points(soup: Soup, step: float) -> np.ndarray:
    """Points along everything but the segments, about every ``step`` (drawing units), for the grid of the sheet."""
    rings = np.concatenate([soup.arcs, np.column_stack([soup.circles, np.zeros(len(soup.circles)),
                                                          np.full(len(soup.circles), 360.0)])])
    out = []
    if len(rings):  # along the curve itself: the centre of a flat arc (a contour line) can be far from it
        r, a0, sweep = rings[:, 2], np.radians(rings[:, 3]), np.radians(rings[:, 4])
        n = np.minimum(np.maximum((r * sweep / step).astype(int), 1), 400)
        rep = np.repeat(np.arange(len(rings)), n + 1)
        ang = a0[rep] + sweep[rep] * (np.arange(len(rep)) - (np.cumsum(n + 1) - (n + 1))[rep]) / n[rep]
        out.append(np.stack([rings[rep, 0] + r[rep] * np.cos(ang), rings[rep, 1] + r[rep] * np.sin(ang)], axis=1))
    if len(soup.curves):
        out += [(soup.curves[:, :2] + soup.curves[:, 2:]) / 2.0, soup.curves[:, :2], soup.curves[:, 2:]]
    for h in soup.hatches:
        x0, y0, x1, y1 = h["bbox"]
        out.append(np.array([[x0, y0], [x1, y1], [(x0 + x1) / 2, (y0 + y1) / 2], [x0, y1], [x1, y0]]))
    if soup.inserts:
        out.append(np.array([[i["x"], i["y"]] for i in soup.inserts]))
    pts = np.concatenate([p.reshape(-1, 2) for p in out if len(p)]) if any(len(p) for p in out) else np.empty((0, 2))
    return pts[np.isfinite(pts).all(axis=1)]


def _title_kind(text: str) -> str | None:
    """What a title says the view is: the text must start with one of the view words (whole words: TETTOIA is no TETTO),
    after the number of the drawing and the state it shows ("1 - STATO DI FATTO - PROSPETTO SUD")."""
    up = " ".join(re.split(r"[^A-Z0-9']+", text.upper())).strip()
    while True:
        rest = next((up[len(w) + 1:] for w in STATES if up.startswith(w + " ")), None)
        if rest is None:
            rest = NUMBER.sub("", up, count=1)
        if rest == up:
            break
        up = rest
    for kind, words in VIEW_WORDS:
        for w in words:
            if up == w or up.startswith(w + " "):
                return kind
    return None


def _title_texts(soup: Soup) -> list[dict]:
    """The texts that name a view: short (or a caption that starts with the number of the drawing), and starting with a
    view word (a text drawn twice on itself counts once)."""
    out, seen = [], set()
    for t in soup.texts:
        key = (t["text"], round(t["x"], 3), round(t["y"], 3))
        if key not in seen and (len(t["text"]) <= MAX_TITLE or CAPTION.match(t["text"])):
            kind = _title_kind(t["text"])
            if kind:
                seen.add(key)
                out.append({**t, "kind": kind})
    return out


def _distance_to_segments(px: float, py: float, s: np.ndarray) -> np.ndarray:
    """How far a point is from each of the segments ``s``."""
    dx, dy = s[:, 2] - s[:, 0], s[:, 3] - s[:, 1]
    t = np.clip(((px - s[:, 0]) * dx + (py - s[:, 1]) * dy) / np.maximum(dx * dx + dy * dy, 1e-18), 0.0, 1.0)
    return np.hypot(px - (s[:, 0] + t * dx), py - (s[:, 1] + t * dy))


def _label_frames(soup: Soup, titles: list[dict], scale: float) -> list[dict]:
    """The little frame (a box, a tab, an underline) a title is written in: a few lines that touch one another and
    nothing else. It labels a drawing, it is not a drawing: when it sits a metre from a plan it must not become a part
    of it. A title with no such frame has none."""
    seg = soup.seg
    frames: list[dict] = []
    reach, touch, far = FRAME_SEED / scale, FRAME_TOUCH / scale, FRAME_SIZE[0] / scale
    low, high = np.minimum(seg[:, [0, 1]], seg[:, [2, 3]]), np.maximum(seg[:, [0, 1]], seg[:, [2, 3]])

    def around(x: float, y: float, r: float) -> np.ndarray:
        return np.flatnonzero((low[:, 0] <= x + r) & (high[:, 0] >= x - r) & (low[:, 1] <= y + r) & (high[:, 1] >= y - r))

    for t in titles if len(seg) else []:
        near = around(t["x"], t["y"], reach)
        if len(near) == 0:
            continue
        d = _distance_to_segments(t["x"], t["y"], seg[near])
        if d.min() > reach:
            continue
        local = around(t["x"], t["y"], far)  # a frame is no bigger than this: the lines to look at
        mine = [int(near[int(np.argmin(d))])]  # the line of the frame nearest to the title, and the lines touching it
        while len(mine) <= FRAME_SEGMENTS:
            ends = np.concatenate([seg[mine][:, :2], seg[mine][:, 2:]])
            cand = local[~np.isin(local, mine)]
            hit = np.zeros(len(cand), bool)
            for corner in (seg[cand][:, :2], seg[cand][:, 2:]):
                hit |= (np.hypot(corner[:, None, 0] - ends[None, :, 0], corner[:, None, 1] - ends[None, :, 1]) <= touch).any(axis=1)
            if not hit.any():
                break
            mine += [int(i) for i in cand[hit]]
        if len(mine) > FRAME_SEGMENTS:
            continue
        part = seg[mine]
        x0, x1 = min(part[:, 0].min(), part[:, 2].min()), max(part[:, 0].max(), part[:, 2].max())
        y0, y1 = min(part[:, 1].min(), part[:, 3].min()), max(part[:, 1].max(), part[:, 3].max())
        if max(x1 - x0, y1 - y0) * scale <= FRAME_SIZE[0] and min(x1 - x0, y1 - y0) * scale <= FRAME_SIZE[1]:
            frames.append({"title": t, "box": (float(x0), float(y0), float(x1), float(y1)), "segs": np.array(mine)})
    return frames


def _components(tree, reach: float) -> np.ndarray:
    """The piece every point of the tree belongs to: points closer than ``reach`` are one piece."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    pairs = tree.query_pairs(reach, output_type="ndarray")
    if len(pairs) == 0:
        return np.arange(tree.n)
    g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(tree.n, tree.n))
    return connected_components(g, directed=False)[1]


def _extent(xy: np.ndarray, label: np.ndarray, half: float) -> dict[int, tuple[float, float, float, float]]:
    """The box of the cells of every piece (``xy`` are the centres of the cells, ``half`` is half a cell)."""
    if len(xy) == 0:
        return {}
    order = np.argsort(label, kind="stable")
    sl = label[order]
    start = np.flatnonzero(np.r_[True, sl[1:] != sl[:-1]])
    x, y = xy[order, 0], xy[order, 1]
    box = np.stack([np.minimum.reduceat(x, start) - half, np.minimum.reduceat(y, start) - half,
                    np.maximum.reduceat(x, start) + half, np.maximum.reduceat(y, start) + half], axis=1)
    return {int(k): tuple(map(float, b)) for k, b in zip(sl[start], box)}


def _thick_enough(box: tuple[float, float, float, float], scale: float) -> bool:
    return min(box[2] - box[0], box[3] - box[1]) * scale >= MIN_VIEW_SIDE


def _bare_runs(near: np.ndarray, which: np.ndarray, per: float) -> np.ndarray:
    """The points of the long lines that lie in a bare run: a stretch of a line with no drawing near it that is too long
    to be a gap in a drawing (BARE_RUN). ``near``: the point has drawing near it; ``which``: the line it is on;
    ``per``: metres between two points."""
    bare = ~near
    if not bare.any():
        return bare
    same = np.r_[False, which[1:] == which[:-1]]  # the point before is on the same line
    run = np.cumsum(bare & ~(same & np.r_[False, bare[:-1]]))
    return bare & (np.bincount(run[bare], minlength=run.max() + 1)[run] * per >= BARE_RUN)


def _pieces(soup: Soup, scale: float, frames: list[dict]) -> tuple[list[tuple[tuple[float, float, float, float], int, bool]],
                                                                   np.ndarray]:
    """The pieces of drawing of the sheet, each as (box, cells, made of long lines only), and the centres of all the
    cells with drawing in them. The sheet is cut in cells of 0.5 m; cells with drawing in them closer than LINK are one
    piece. A long straight line (the ground of an elevation, a table, the walls of a plan) holds a drawing together,
    but where it runs through empty space it links nothing: a ground line under two elevations, a section mark or the
    border of the sheet must not make one view of two."""
    from scipy.spatial import cKDTree

    cell = CELL / scale
    seg = soup.seg
    drawn = np.ones(len(seg), bool)
    for f in frames:
        drawn[f["segs"]] = False
    ruling = drawn & (np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1]) * scale >= LONG_LINE)
    step = cell * 0.8
    ink = np.concatenate([_sample_points(soup, step), _along(seg[drawn & ~ruling], step, 400)[0]])
    line, which = _along(seg[ruling], step, 4000)
    which = np.flatnonzero(ruling)[which]
    keys = np.unique(_pack(*np.floor(ink / cell).astype(np.int64).T))
    near = np.zeros(len(line), bool)
    if len(keys) and len(line):
        near = np.isfinite(cKDTree((_unpack(keys) + 0.5) * cell).query(line, distance_upper_bound=LINK / scale)[0])
    cut = _bare_runs(near, which, step * scale)
    keys = np.unique(np.concatenate([keys, _pack(*np.floor(line[~cut] / cell).astype(np.int64).T)]))
    centre = (_unpack(keys) + 0.5) * cell
    piece = _components(cKDTree(centre), LINK / scale) if len(centre) else np.empty(0, dtype=int)
    box = _extent(centre, piece, cell / 2)
    count = np.bincount(piece) if len(piece) else np.zeros(0, dtype=int)
    real = {g for g, b in box.items() if count[g] >= MIN_VIEW_CELLS and _thick_enough(b, scale)}
    # crumbs (a note, a north arrow, a stair): a part of the view they are near, when they are small beside it
    into = np.full(len(count), -1)
    into[list(real)] = list(real)
    crumb = ~np.isin(piece, list(real))
    if real and crumb.any():
        d, i = cKDTree(centre[~crumb]).query(centre[crumb], distance_upper_bound=CRUMB_REACH / scale)
        at = np.isfinite(d)
        cg, host = piece[crumb][at], piece[~crumb][i[at]]
        order = np.lexsort((d[at], cg))
        cg, host = cg[order], host[order]
        first = np.r_[True, cg[1:] != cg[:-1]] if len(cg) else np.zeros(0, bool)  # the nearest piece of each crumb
        for c, h in zip(cg[first], host[first]):
            if count[c] <= CRUMB_SHARE * count[h]:
                into[c] = h
    keep = into[piece] >= 0
    count = np.bincount(into[piece[keep]], minlength=len(count)) if keep.any() else count
    # the box of a view is the box of its ink, not of its cells: it does not move when the sheet is shifted by a fraction
    # of a cell, and it is as tight as a person would draw it
    marks = np.concatenate([ink, line[~cut]])
    owner = into[piece[np.searchsorted(keys, _pack(*np.floor(marks / cell).astype(np.int64).T))]]
    box = _extent(marks[owner >= 0], owner[owner >= 0], 0.0)
    out = [(box[g], int(count[g]), False) for g in sorted(real)]
    # long lines with nothing near them: the boundary of a lot is a view when it is more than one line
    if cut.any():
        bkeys, inverse = np.unique(_pack(*np.floor(line[cut] / cell).astype(np.int64).T), return_inverse=True)
        bxy = (_unpack(bkeys) + 0.5) * cell
        lab = _components(cKDTree(bxy), LINK / scale)
        lines_in = np.bincount(np.unique(np.column_stack([lab[inverse.reshape(-1)], which[cut]]), axis=0)[:, 0],
                               minlength=lab.max() + 1)
        cells = np.bincount(lab)
        for g, b in _extent(line[cut], lab[inverse.reshape(-1)], 0.0).items():
            if lines_in[g] >= OUTLINE_LINES and cells[g] >= MIN_VIEW_CELLS and _thick_enough(b, scale):
                out.append((b, int(cells[g]), True))
    return out, centre


def _overlap(a: tuple, b: tuple) -> float:
    """The area two boxes have in common."""
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _area(a: tuple) -> float:
    return max(a[2] - a[0], 0.0) * max(a[3] - a[1], 0.0)


def _gap(a: tuple, b: tuple) -> float:
    """The distance between two boxes (0 when they touch or overlap)."""
    return math.hypot(max(a[0] - b[2], b[0] - a[2], 0.0), max(a[1] - b[3], b[1] - a[3], 0.0))


def _inside(v: View, w: View) -> bool:
    """Does ``v`` lie in the box of ``w``?"""
    return _overlap(v.bbox, w.bbox) >= INSIDE_SHARE * _area(v.bbox)


def _renumber(views: list[View], scale: float) -> None:
    """Views in the same row of the sheet (within 10 m) are read left to right, the rows from the top."""
    order = 10.0 / scale
    views.sort(key=lambda v: (-round(v.bbox[3] / order), v.bbox[0]))
    for k, v in enumerate(views, 1):
        v.id = k


def _sane(soup: Soup, scale: float) -> Soup:
    """The drawing without the pieces whose coordinates no grid of cells can hold (a damaged entity has a NaN, an
    infinity, 1e300): they are not drawing, and they must not stop the analysis."""
    limit = MAX_CELLS * CELL / scale

    def sound(a: np.ndarray, columns: int) -> np.ndarray:
        return a[np.isfinite(a).all(axis=1) & (np.abs(a[:, :columns]) < limit).all(axis=1)]

    def fine(*values: float) -> bool:
        return all(math.isfinite(v) and abs(v) < limit for v in values)

    return replace(soup, seg=sound(soup.seg, 4), arcs=sound(soup.arcs, 3), circles=sound(soup.circles, 3),
                   curves=sound(soup.curves, 4), hatches=[h for h in soup.hatches if fine(*h["bbox"])],
                   texts=[t for t in soup.texts if fine(t["x"], t["y"])], inserts=[i for i in soup.inserts if fine(i["x"], i["y"])])


def _grid_scale(soup: Soup, scale: float) -> float:
    """The scale the cells of the sheet are laid at: ``scale``, unless that spreads the core of the sheet over more than
    MAX_GRID_CELLS cells (a plan of 14 m read in a unit 100 times too big is 1400 m of sheet, seconds of work to cut in
    half metres and nothing to learn from): then the sheet is looked at through bigger cells, as if it were smaller."""
    w, h = _core_size(soup)
    cells = (w * scale / CELL) * (h * scale / CELL)
    return scale if cells <= MAX_GRID_CELLS else scale * math.sqrt(MAX_GRID_CELLS / cells)


def find_views(soup: Soup, scale: float) -> list[View]:
    """The views of the sheet: pieces of drawing separated by empty space, with the title near each and a type."""
    soup = _sane(soup, scale)
    titles = sorted(_title_texts(soup), key=lambda t: -t["height"])[:MAX_TITLES]
    frames = _label_frames(soup, titles, scale)
    pieces, centre = _pieces(soup, _grid_scale(soup, scale), frames)
    views = [View(0, box, cells, outline=outline) for box, cells, outline in sorted(pieces, key=lambda p: -p[1])[:MAX_VIEWS]]
    views = _without_strays(views, [f["box"] for f in frames] + [(t["x"], t["y"], t["x"], t["y"]) for t in titles], scale)
    _renumber(views, scale)
    _measure(views, soup, scale)
    _assign_titles(views, titles, frames, scale)
    for v in views:
        _name(v, scale)
    inner = _plans_inside(views, soup, scale, centre)
    lines = _Lines(soup, scale)
    pairs = _match_copies(views, inner, soup, scale, centre, lines)
    pairs += _twins(views, lines, {frozenset((id(c), id(o))) for c, o in pairs})
    _measure(inner, soup, scale)
    for v in inner:
        _name(v, scale)
    views += inner
    _renumber(views, scale)
    _link_copies(views, pairs, inner)
    _nest(views)
    _relate(views, scale)
    return views


def _match_copies(views: list[View], inner: list[View], soup: Soup, scale: float, centre: np.ndarray, lines: _Lines) -> list[tuple[View, View]]:
    """The pairs (copy, original) of views that repeat each other. A plan found in a bigger view that is the copy of a plan
    of the sheet gets the exact box of that plan (the swing doors only say where the house is, roughly): of several
    drawings it could be the copy of, the biggest (a plan, not a diagram of its body)."""
    pairs: list[tuple[View, View]] = []
    best: dict[int, tuple[float, View, tuple]] = {}
    for src, holder, box in _copies(views, soup, scale, lines):
        if box is None:
            pairs.append((holder, src))
            continue
        for k, w in enumerate(inner):
            middle = ((w.bbox[0] + w.bbox[2]) / 2, (w.bbox[1] + w.bbox[3]) / 2)
            if _overlap(w.bbox, box) >= COPY_ROUGH * _area(w.bbox) and box[0] <= middle[0] <= box[2] and box[1] <= middle[1] <= box[3] \
                    and _area(box) > best.get(k, (0.0,))[0]:
                best[k] = (_area(box), src, box)
    for k, (_, src, box) in best.items():
        inner[k].bbox = box
        inner[k].cells = int(((centre[:, 0] >= box[0]) & (centre[:, 0] <= box[2]) & (centre[:, 1] >= box[1]) & (centre[:, 1] <= box[3])).sum())
        pairs.append((inner[k], src))
    return pairs


def _link_copies(views: list[View], pairs: list[tuple[View, View]], inner: list[View]) -> None:
    """Views that repeat one another are one group: the original is the one that is not drawn inside another view, then
    the one with a title, then the first in reading order; the others are its copies. A copy with no type of its own has
    the type of the original."""
    group = {id(v): id(v) for v in views}

    def find(k: int) -> int:
        while group[k] != k:
            group[k] = group[group[k]]
            k = group[k]
        return k

    for c, o in pairs:
        group[find(id(c))] = find(id(o))
    members: dict[int, list[View]] = {}
    for v in views:
        members.setdefault(find(id(v)), []).append(v)
    inside = {id(v) for v in inner}
    for g in members.values():
        if len(g) < 2:
            continue
        first = min(g, key=lambda v: (id(v) in inside, not v.title_items, v.id))
        for v in g:
            if v is not first:
                v.copy_of = first.id
                if v.kind == "?":
                    v.kind, v.kind_from = first.kind, f"copia della vista {first.id}"


def _without_strays(views: list[View], marks: list[tuple], scale: float) -> list[View]:
    """Leave out the boundary lines that frame other views (the border of the sheet) or lie on one (the edge of its own
    lot), and the stray pieces near a much bigger drawing (a few contour lines cut off by gaps, a lone arrow), unless a
    title is near them."""
    keep = []
    for v in views:
        others = [w for w in views if w is not v and not w.outline]
        if v.outline:
            if any(_inside(w, v) or _overlap(v.bbox, w.bbox) >= EDGE_SHARE * _area(v.bbox) for w in others):
                continue
        elif any(v.cells < STRAY_SHARE * w.cells and _gap(v.bbox, w.bbox) <= STRAY_REACH / scale for w in others) \
                and not any(_gap(m, v.bbox) <= TITLE_REACH / scale for m in marks):
            continue
        keep.append(v)
    return keep


def _plans_inside(views: list[View], soup: Soup, scale: float, centre: np.ndarray) -> list[View]:
    """A plan drawn on its lot (or a copy of a plan in a site): when all the swing doors of a view lie together in one
    place that takes up less than a third of it and is much denser than the rest, that place is a plan of its own. Two
    places with doors (the wings of a long building, two houses) say nothing, and neither does a view that is all straight
    lines along two directions, like a plan: the lot around a house has contours, trees, boundaries running every way."""
    from scipy.spatial import cKDTree

    arcs = soup.arcs
    door = (arcs[:, 4] > 70) & (arcs[:, 4] < 110) & (arcs[:, 2] * scale >= DOOR_RADIUS[0]) & (arcs[:, 2] * scale <= DOOR_RADIUS[1])
    margin = NEST_MARGIN / scale
    plans = []
    for v in views:
        x0, y0, x1, y1 = v.bbox
        at = door & (arcs[:, 0] >= x0) & (arcs[:, 0] <= x1) & (arcs[:, 1] >= y0) & (arcs[:, 1] <= y1)
        if v.outline or v.kind in ("elevation", "section", "detail") or at.sum() < NEST_DOORS or v.ortho >= NEST_ORTHO:
            continue
        hinge, radius = arcs[at, :2], arcs[at, 2]
        group = _components(cKDTree(hinge), NEST_LINK / scale)
        sizes = np.bincount(group)
        if (sizes >= NEST_DOORS).sum() != 1:
            continue
        mine = group == int(np.argmax(sizes))
        box = (max(x0, float((hinge[mine, 0] - radius[mine]).min()) - margin), max(y0, float((hinge[mine, 1] - radius[mine]).min()) - margin),
               min(x1, float((hinge[mine, 0] + radius[mine]).max()) + margin), min(y1, float((hinge[mine, 1] + radius[mine]).max()) + margin))
        if _area(box) >= NEST_AREA * _area(v.bbox):
            continue
        inside = int(((centre[:, 0] >= box[0]) & (centre[:, 0] <= box[2]) & (centre[:, 1] >= box[1]) & (centre[:, 1] <= box[3])).sum())
        around = max(v.cells - inside, 1) / max(_area(v.bbox) - _area(box), 1e-9)  # drawing per unit of area
        if inside / _area(box) >= NEST_DENSITY * around:  # a house on its lot is far denser than the lot
            plans.append(View(0, box, inside))
    return plans


def _copy_moves(soup: Soup, scale: float) -> list[tuple[int, tuple[float, float], np.ndarray, np.ndarray]]:
    """The ways the written texts say a drawing was copied: (quarter turns, shift in metres, the texts it moves, the texts
    they land on: indices in ``soup.texts``), the best supported first and COPY_MOVES at most. A move counts when
    COPY_TEXTS different words land on the same words within COPY_TOLERANCE: a plan copied into the site (and turned
    there) keeps its words where the move puts them."""
    where: dict[str, list[int]] = {}
    seen = set()
    for k, t in enumerate(soup.texts):
        key = (t["text"], round(t["x"] * scale, 2), round(t["y"] * scale, 2))  # a text drawn twice on itself counts once
        if len(t["text"]) >= 2 and key not in seen:
            seen.add(key)
            where.setdefault(t["text"], []).append(k)
    src, dst, word = [], [], []
    for w, idx in enumerate(i for i in where.values() if 2 <= len(i) <= COPY_REPEATS):
        if len(src) > COPY_PAIRS:
            break
        for i in idx:
            for j in idx:
                if i != j:
                    src.append(i)
                    dst.append(j)
                    word.append(w)
    if not src:
        return []
    src, dst, word = np.array(src), np.array(dst), np.array(word)
    xy = np.array([[t["x"], t["y"]] for t in soup.texts]) * scale
    found: list[tuple[int, int, tuple[float, float], np.ndarray]] = []  # (different words, turns, shift, the pairs)
    for turns in range(4):
        c, s = (1, 0, -1, 0)[turns], (0, 1, 0, -1)[turns]  # cosine and sine of the turn
        p = xy[src]
        shift = xy[dst] - np.stack([c * p[:, 0] - s * p[:, 1], s * p[:, 0] + c * p[:, 1]], axis=1)
        for half in (0.0, 0.5):  # two grids: a group cut by the edge of a cell of one lies whole in a cell of the other
            cell = np.floor(shift / (2 * COPY_TOLERANCE) + half).astype(np.int64)
            key = (cell[:, 0] + _SHIFT) * _PACK + cell[:, 1] + _SHIFT
            order = np.argsort(key, kind="stable")
            first = np.flatnonzero(np.r_[True, key[order][1:] != key[order][:-1]])
            last = np.r_[first[1:], len(order)]
            for a, b in zip(first[(last - first) >= COPY_TEXTS], last[(last - first) >= COPY_TEXTS]):
                pairs = order[a:b]
                if len(set(word[pairs])) >= COPY_TEXTS:
                    mid = np.median(shift[pairs], axis=0)
                    found.append((len(set(word[pairs])), turns, (float(mid[0]), float(mid[1])), pairs))
    found.sort(key=lambda f: -f[0])
    moves: list[tuple[int, tuple[float, float], np.ndarray, np.ndarray]] = []
    for _, turns, mid, pairs in found[:4 * COPY_MOVES]:
        if len(moves) < COPY_MOVES and all(m[0] != turns or np.hypot(m[1][0] - mid[0], m[1][1] - mid[1]) > 2 * COPY_TOLERANCE for m in moves):
            moves.append((turns, mid, src[pairs], dst[pairs]))
    return moves


def _moved(box: tuple, turns: int, shift: tuple[float, float], scale: float) -> tuple[float, float, float, float]:
    """The box of a drawing after a move: a turn of ``turns`` quarters about the origin, then a shift (metres)."""
    c, s = (1, 0, -1, 0)[turns], (0, 1, 0, -1)[turns]
    corners = [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])]
    xs = [c * x - s * y + shift[0] / scale for x, y in corners]
    ys = [s * x + c * y + shift[1] / scale for x, y in corners]
    return min(xs), min(ys), max(xs), max(ys)


class _Lines:
    """The middle and the length of every straight line of the sheet. Two drawings are copies of one another when the
    lines of one lie, after a move (a turn by quarters and a shift), on lines of the other of the same length."""

    def __init__(self, soup: Soup, scale: float) -> None:
        from scipy.spatial import cKDTree

        seg = soup.seg
        self.x, self.y = (seg[:, 0] + seg[:, 2]) / 2, (seg[:, 1] + seg[:, 3]) / 2
        self.length = np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1])
        self.tree = cKDTree(np.stack([self.x, self.y], axis=1)) if len(seg) else None
        self.scale = scale

    def inside(self, box: tuple) -> np.ndarray:
        return np.flatnonzero((self.x >= box[0]) & (self.x <= box[2]) & (self.y >= box[1]) & (self.y <= box[3]))

    def share(self, box: tuple, turns: int, shift: tuple[float, float]) -> float:
        """The share of the lines in ``box`` that a move (``shift`` in metres) carries onto a line of the same length."""
        mine = self.inside(box)
        if len(mine) == 0 or self.tree is None:
            return 0.0
        c, s = (1, 0, -1, 0)[turns], (0, 1, 0, -1)[turns]
        x, y = self.x[mine], self.y[mine]
        moved = np.stack([c * x - s * y + shift[0] / self.scale, s * x + c * y + shift[1] / self.scale], axis=1)
        tol = COPY_INK_TOLERANCE / self.scale
        d, i = self.tree.query(moved, distance_upper_bound=tol)
        hit = np.isfinite(d)
        same = np.zeros(len(mine), bool)
        same[hit] = np.abs(self.length[i[hit]] - self.length[mine][hit]) <= tol
        return float(same.mean())

    def shift(self, box: tuple, other: tuple, turns: int) -> tuple[float, float] | None:
        """The shift (metres) that, after the turn, carries most of the longest lines of ``box`` onto lines of the same
        length in ``other``: every line votes for the shifts that would put it on a line of its length."""
        mine, there = self.inside(box), self.inside(other)
        if len(mine) == 0 or len(there) == 0:
            return None
        mine = mine[np.argsort(-self.length[mine])[:COPY_VOTERS]]
        there = there[np.argsort(self.length[there])]
        c, s = (1, 0, -1, 0)[turns], (0, 1, 0, -1)[turns]
        tol = COPY_INK_TOLERANCE / self.scale
        lengths = self.length[there]
        votes = []
        for i in mine:
            at = int(np.searchsorted(lengths, self.length[i]))  # the lines of nearly the same length are around here
            near = there[max(at - COPY_PARTNERS // 2, 0):at + COPY_PARTNERS // 2]
            j = near[np.abs(self.length[near] - self.length[i]) <= tol]
            votes.append(np.stack([self.x[j] - (c * self.x[i] - s * self.y[i]), self.y[j] - (s * self.x[i] + c * self.y[i])], axis=1))
        votes = np.concatenate(votes) if votes else np.empty((0, 2))
        if len(votes) == 0:
            return None
        _, inverse, count = np.unique(np.round(votes / (2 * tol)).astype(np.int64), axis=0, return_inverse=True, return_counts=True)
        best = int(count.argmax())
        if count[best] < COPY_VOTES:
            return None
        found = np.median(votes[inverse.reshape(-1) == best], axis=0) * self.scale
        return float(found[0]), float(found[1])


def _copies(views: list[View], soup: Soup, scale: float, lines: _Lines) -> list[tuple[View, View, tuple | None]]:
    """The copies of drawings in the sheet: (the view that was copied, the view that holds the copy, the box of the copy
    or None). The texts propose where a drawing was copied to, the lines confirm it. The holder is a view of the sheet
    that repeats the drawing (box None), or a much bigger view the copy is drawn in (a plan in the site, turned a quarter):
    then the box is exactly the box of the plan."""
    moves = _copy_moves(soup, scale) if views and soup.texts and lines.tree is not None else []
    if not moves:
        return []
    tx = np.array([t["x"] for t in soup.texts])
    ty = np.array([t["y"] for t in soup.texts])
    owner = np.full(len(tx), -1)  # the smallest view that holds each text
    for k in sorted(range(len(views)), key=lambda k: -_area(views[k].bbox)):
        x0, y0, x1, y1 = views[k].bbox
        owner[(tx >= x0) & (tx <= x1) & (ty >= y0) & (ty <= y1)] = k
    found: list[tuple[View, View, tuple | None]] = []
    done = set()
    for turns, shift, src, dst in moves:
        a, b = owner[src][owner[src] >= 0], owner[dst][owner[dst] >= 0]
        if len(a) == 0 or len(b) == 0:
            continue
        ka, kb = int(np.bincount(a).argmax()), int(np.bincount(b).argmax())
        va, vb = views[ka], views[kb]
        if ka == kb or (ka, kb) in done or (kb, ka) in done:
            continue
        box = _moved(va.bbox, turns, shift, scale)
        union = _area(va.bbox) + _area(vb.bbox) - _overlap(box, vb.bbox)
        if _overlap(box, vb.bbox) >= COPY_FIT * union:
            where = None  # two drawings of the sheet repeat each other
        elif _overlap(box, vb.bbox) >= COPY_INSIDE * _area(box) and vb.cells > va.cells:
            where = (max(box[0], vb.bbox[0]), max(box[1], vb.bbox[1]), min(box[2], vb.bbox[2]), min(box[3], vb.bbox[3]))
        else:
            continue
        agree = lines.share(va.bbox, turns, shift)
        if where is None:  # two views that repeat each other: the lines of the smaller drawing are in the other
            back = (4 - turns) % 4
            c, s = (1, 0, -1, 0)[back], (0, 1, 0, -1)[back]
            agree = max(agree, lines.share(vb.bbox, back, (-(c * shift[0] - s * shift[1]), -(s * shift[0] + c * shift[1]))))
        if agree >= COPY_INK:
            found.append((va, vb, where))
            done.add((ka, kb))
    return found


def _twins(views: list[View], lines: _Lines, known: set[frozenset]) -> list[tuple[View, View]]:
    """Views of about the same size whose lines lie on one another after a turn and a shift: a drawing and its copy, with
    no words in common. Two floors of a house have the same walls and not the same partitions: they are no twins."""
    pairs = []
    if lines.tree is None:
        return pairs
    for i, a in enumerate(views):
        for b in views[i + 1:]:
            wa, ha = sorted(a.size_m(lines.scale))
            wb, hb = sorted(b.size_m(lines.scale))  # a copy may be turned a quarter of a turn
            if a.outline or b.outline or frozenset((id(a), id(b))) in known or a.cells < MIN_PLAN_CELLS or b.cells < MIN_PLAN_CELLS \
                    or "site" in (a.kind, b.kind) or (a.kind != b.kind and "?" not in (a.kind, b.kind)) \
                    or abs(wa - wb) > COPY_SIZE * max(wa, wb) or abs(ha - hb) > COPY_SIZE * max(ha, hb):
                continue
            for turns in range(4):
                shift = lines.shift(a.bbox, b.bbox, turns)
                if shift is None:
                    continue
                back = (4 - turns) % 4
                c, s = (1, 0, -1, 0)[back], (0, 1, 0, -1)[back]
                if max(lines.share(a.bbox, turns, shift),
                       lines.share(b.bbox, back, (-(c * shift[0] - s * shift[1]), -(s * shift[0] + c * shift[1])))) >= COPY_INK:
                    pairs.append((b, a))
                    break
    return pairs


def _nest(views: list[View]) -> None:
    """A view in the box of a bigger one is a part of that drawing: its ``parent`` is the smallest of those."""
    for v in views:
        hosts = [w for w in views if w is not v and w.cells > v.cells and not w.outline and _inside(v, w)]
        v.parent = min(hosts, key=lambda w: _area(w.bbox)).id if hosts else None


def _orthogonality(angle: np.ndarray, length: np.ndarray) -> float:
    """The share of the lines (by length) that run along the main direction of the drawing, or across it, whatever that
    direction is: 1 for a building, about 0.2 for contour lines."""
    hist = np.bincount(np.minimum(angle.astype(int), 89), weights=length, minlength=90)
    smooth = sum(np.roll(hist, k) for k in range(-ORTHO_TOLERANCE, ORTHO_TOLERANCE + 1))
    return float(smooth.max() / max(hist.sum(), 1e-12))


def _measure(views: list[View], soup: Soup, scale: float) -> None:
    """What each view holds: lines, arcs, door arcs, hatches, texts, level marks, written areas, room names."""
    from .texts import ROOM_WORDS

    seg, arcs = soup.seg, soup.arcs
    room_re = re.compile(r"\b(?:" + "|".join(sorted(map(re.escape, ROOM_WORDS), key=len, reverse=True)) + r")\b")
    mx, my = (seg[:, 0] + seg[:, 2]) / 2, (seg[:, 1] + seg[:, 3]) / 2
    length = np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1])
    angle = np.degrees(np.arctan2(seg[:, 3] - seg[:, 1], seg[:, 2] - seg[:, 0])) % 90.0
    door = (arcs[:, 4] > 70) & (arcs[:, 4] < 110) & (arcs[:, 2] * scale >= DOOR_RADIUS[0]) & (arcs[:, 2] * scale <= DOOR_RADIUS[1])
    cx, cy = (soup.curves[:, 0] + soup.curves[:, 2]) / 2, (soup.curves[:, 1] + soup.curves[:, 3]) / 2
    hx = np.array([(h["bbox"][0] + h["bbox"][2]) / 2 for h in soup.hatches])
    hy = np.array([(h["bbox"][1] + h["bbox"][3]) / 2 for h in soup.hatches])
    tile = np.array([any(w in h["pattern"].upper() for w in TILE_PATTERNS) for h in soup.hatches], bool)
    tx, ty = np.array([t["x"] for t in soup.texts]), np.array([t["y"] for t in soup.texts])
    is_level = np.array([bool(LEVEL_MARK.match(t["text"])) for t in soup.texts], bool)
    is_area = np.array([bool(AREA_MARK.search(t["text"])) for t in soup.texts], bool)
    room = [m.group() if (m := room_re.search(t["text"].upper())) else "" for t in soup.texts]
    ix = np.array([i["x"] for i in soup.inserts])
    iy = np.array([i["y"] for i in soup.inserts])
    pad = FLOAT_PAD / scale
    for v in views:
        x0, y0, x1, y1 = v.bbox[0] - pad, v.bbox[1] - pad, v.bbox[2] + pad, v.bbox[3] + pad

        def within(x: np.ndarray, y: np.ndarray) -> np.ndarray:
            return (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)

        here = within(mx, my)
        v.segments = int(here.sum())
        v.ortho = _orthogonality(angle[here], length[here]) if v.segments else 0.0
        at = within(arcs[:, 0], arcs[:, 1])
        v.arcs, v.doors = int(at.sum()), int((at & door).sum())
        v.curves = int(within(cx, cy).sum())
        h = within(hx, hy)
        v.hatches, v.tiles = int(h.sum()), int((h & tile).sum())
        t = np.flatnonzero(within(tx, ty))
        v.texts = len(t)
        v.levels, v.areas = int(is_level[t].sum()), int(is_area[t].sum())
        v.rooms = len({room[i] for i in t} - {""})
        v.inserts = int(within(ix, iy).sum())


def _contradicts(kind: str, v: View) -> bool:
    """A title cannot name a view whose content says otherwise: a PROSPETTO is not the plan with its door arcs and the
    areas of its rooms, and a PIANTA is not a facade covered with level marks."""
    plan_like = v.doors >= PLAN_DOORS or v.areas >= PLAN_AREAS
    if kind in ("elevation", "section"):
        return plan_like and v.levels < LEVEL_MARKS
    if kind == "plan":
        return v.levels >= LEVEL_MARKS and not plan_like
    return False


def _side(box: tuple, bbox: tuple) -> str:
    """Where a title sits compared with a view: in it, above it, below it, left or right of it."""
    if _gap(box, bbox) == 0:
        return "in"
    if box[1] >= bbox[3]:
        return "above"
    if box[3] <= bbox[1]:
        return "below"
    return "left" if box[2] <= bbox[0] else "right"


def _assign_titles(views: list[View], titles: list[dict], frames: list[dict], scale: float) -> None:
    """Each title names the view nearest to it (the frame it is written in counts, not the text alone), within
    TITLE_REACH, but never a view whose content says it cannot be. Where two views are about as near, the one that has
    no title yet is named, and when both are alike the habit of the sheet decides: the titles that are clearly near one
    view (and outside it) say whether this sheet puts them above or below their drawings. A title written inside a view
    belongs to it. A frame within LINK of the view it names is a part of it."""
    from collections import Counter

    box_of = {id(f["title"]): f["box"] for f in frames}
    reach, tie = TITLE_REACH / scale, TITLE_TIE / scale
    options = []
    for t in titles:
        box = box_of.get(id(t), (t["x"], t["y"], t["x"], t["y"]))
        near = sorted(((_gap(box, v.bbox), v.cells, v) for v in views
                       if _gap(box, v.bbox) <= reach and not _contradicts(t["kind"], v)), key=lambda o: o[:2])
        options.append((t, box, near))
    sure = Counter(_side(box, near[0][2].bbox) for _, box, near in options
                   if near and near[0][0] > 0 and (len(near) == 1 or near[1][0] - near[0][0] > tie))
    habit = sure.most_common(1)[0][0] if sure else None
    chosen = [(t, box, [o for o in near if o[0] - near[0][0] <= (0 if near[0][0] == 0 else tie)])  # in a view: that view
              for t, box, near in options if near]
    for t, box, close in sorted(chosen, key=lambda c: len(c[2]) > 1):  # the titles that are clearly near one view first
        free = [o for o in close if not o[2].title_items] or close  # a title between two views names the one with no title
        d, _, v = next((o for o in free if _side(box, o[2].bbox) == habit), free[0])
        v.title_items.append(t)
        if d <= LINK / scale and id(t) in box_of:
            v.bbox = (min(v.bbox[0], box[0]), min(v.bbox[1], box[1]), max(v.bbox[2], box[2]), max(v.bbox[3], box[3]))


def _name(v: View, scale: float) -> None:
    """The type of a view: what its title says, else what it holds."""
    v.titles = [t["text"] for t in v.title_items]
    if v.title_items:
        cx, cy = (v.bbox[0] + v.bbox[2]) / 2, (v.bbox[1] + v.bbox[3]) / 2  # the biggest letters, then the nearest
        best = max(v.title_items, key=lambda t: (round(t["height"], 3), -math.hypot(t["x"] - cx, t["y"] - cy)))
        v.kind, v.kind_from = best["kind"], "titolo"
    else:
        v.kind, why = _kind_from_content(v, scale)
        v.kind_from = f"contenuto: {why}" if why else ""


def _relate(views: list[View], scale: float) -> None:
    """Views that hold one another: a plan much bigger than another view that has the same title, or that lies in it, is
    the site around it."""
    for a in views:
        for b in views:
            if a is b or a.kind != "plan" or b.kind not in ("plan", "roof"):
                continue
            same = bool({t["text"].upper() for t in a.title_items} & {t["text"].upper() for t in b.title_items})
            if same and b.kind == "plan" and a.cells >= SITE_RATIO * b.cells:
                a.kind, a.kind_from = "site", f"contiene la vista {b.id}, che ha lo stesso titolo"
            elif b.kind == "plan" and a.cells >= SITE_RATIO_OTHER * b.cells and b.cells >= MIN_PLAN_CELLS:
                a.kind, a.kind_from = "site", f"molto piu' grande della vista {b.id}: il lotto intorno alla casa"
            elif b.parent == a.id and a.cells >= (COPY_SITE_RATIO if b.copy_of else SITE_RATIO) * b.cells:
                a.kind, a.kind_from = "site", f"contiene la vista {b.id}: il lotto intorno alla casa"


def _kind_from_content(v: View, scale: float) -> tuple[str, str]:
    """The type of a view with no title, from what it holds: a plan has door arcs and the areas of its rooms, a facade
    is wide and low with level marks, a site has lines running every way (contours, boundaries) over many metres, a
    roof plan has tiles. When nothing says, '?'."""
    w, h = v.size_m(scale)
    aspect = max(w, h) / max(min(w, h), 1e-9)
    if v.segments >= ORTHO_MIN_LINES and v.ortho < SITE_ORTHO and max(w, h) >= SITE_SIZE:
        return "site", "linee in ogni direzione (curve di livello, confini) su molti metri"
    if (v.doors >= PLAN_DOORS or v.areas >= PLAN_AREAS or (v.doors >= 2 and aspect < FACADE_ASPECT)) \
            and v.levels < LEVEL_MARKS:
        return "plan", f"{v.doors} archi di porta, {v.areas} superfici scritte"
    if v.rooms >= SECTION_ROOMS and v.doors < PLAN_DOORS and v.areas < PLAN_AREAS:
        return "section", f"{v.rooms} nomi di locali scritti e nessuna porta"
    if v.tiles >= ROOF_TILES and v.tiles * 5 >= v.hatches and v.levels == 0 and aspect < FACADE_ASPECT * 1.5:
        return "roof", f"{v.tiles} campiture di coppi"
    if v.levels >= LEVEL_MARKS or (aspect >= FACADE_ASPECT and v.doors == 0):
        return "elevation", f"larga e bassa ({w:.0f} x {h:.0f} m)" if v.levels < LEVEL_MARKS else f"{v.levels} quote di livello"
    return "?", ""


def _count_named(views: list[View], soup: Soup, rules=None) -> None:
    """How much of each view is on layers the names call walls, doors or windows: a plan drawn with proper layers is
    the plan even when its doors are blocks (which the scan does not see as arcs)."""
    from .config import LayerRules

    rules = rules or LayerRules()
    names = {l for l in soup.layers if rules.classify_layer(l) in ("wall", "door", "window")}
    if not names:
        return
    ids = [i for i, l in enumerate(soup.layers) if l in names]
    seg = soup.seg
    mine = np.isin(soup.seg_layer, ids) if len(seg) else np.zeros(0, bool)
    for v in views:
        x0, y0, x1, y1 = v.bbox
        n = 0
        if len(seg):
            mx, my = (seg[:, 0] + seg[:, 2]) / 2, (seg[:, 1] + seg[:, 3]) / 2
            n += int(np.count_nonzero(mine & (mx >= x0) & (mx <= x1) & (my >= y0) & (my <= y1)))
        n += sum(1 for h in soup.hatches if h["layer"] in names and x0 <= (h["bbox"][0] + h["bbox"][2]) / 2 <= x1
                 and y0 <= (h["bbox"][1] + h["bbox"][3]) / 2 <= y1)
        n += sum(1 for i in soup.inserts if (i["layer"] in names or rules.classify_block(i["name"]) in ("door", "window"))
                 and x0 <= i["x"] <= x1 and y0 <= i["y"] <= y1)
        v.named = n


# --- what to do with the sheet ----------------------------------------------------------------

@dataclass
class Analysis:
    soup: Soup
    unit: UnitGuess
    views: list[View]
    plan: View | None  # the view that is converted
    notes: list[str] = field(default_factory=list)  # what was decided, for the report
    wall_layers: list[str] = field(default_factory=list)  # layers chosen as walls (empty: the layer names were enough)

    def view_table(self) -> list[dict]:
        scale = self.unit.scale
        rows = []
        for v in self.views:
            w, h = v.size_m(scale)
            rows.append({"id": v.id, "tipo": v.kind, "da": v.kind_from, "larghezza_m": round(float(w), 1),
                         "altezza_m": round(float(h), 1), "titolo": " | ".join(v.titles), "porte": v.doors,
                         "copia_di": v.copy_of or "", "dentro_la_vista": v.parent or "", "usata": v is self.plan})
        return rows


def choose_plan(views: list[View], wanted: int | None = None) -> tuple[View | None, str]:
    """The view to convert: the one asked for, else the biggest plan that is neither the site around a building nor
    a copy of another view (a plan found inside a bigger view, whose box is only approximate, comes after the others),
    else the view with the most door arcs."""
    if wanted is not None:
        for v in views:
            if v.id == wanted:
                return v, f"la vista {v.id} indicata da te"
        return None, f"la vista {wanted} non esiste"
    plans = [v for v in views if v.kind == "plan" and v.copy_of is None]
    plans = [v for v in plans if v.parent is None] or plans
    if plans:
        best = max(plans, key=lambda v: (v.named > 0 or v.doors > 0, v.cells))
        return best, "la pianta piu' grande"
    withdoors = [v for v in views if v.doors > 0 and v.kind not in ("elevation", "section", "roof", "site")]
    if withdoors:
        return max(withdoors, key=lambda v: v.doors), "la vista con piu' porte"
    return None, ""


def analyze(doc, layer_used=None, wanted_view: int | None = None, unit: str | None = None, rules=None) -> Analysis:
    """``unit``: the unit the user gave (then nothing is guessed). ``rules``: the layer rules (default ones), to tell
    which layers the names call walls, doors and windows."""
    soup = scan(doc, layer_used)
    if unit in UNIT_TO_METERS:
        unit = UnitGuess(unit, soup.declared_unit, {unit: 9.0}, ["indicata da te"])
    else:
        unit = infer_unit(soup)
    views = find_views(soup, unit.scale)
    _count_named(views, soup, rules)
    plan, why = choose_plan(views, wanted_view)
    notes: list[str] = []
    if wanted_view is not None and plan is None:
        notes.append(f"La vista {wanted_view} non esiste: il foglio ne ha {len(views)} (guarda NOME_viste.png).")
    if plan is not None and len(views) > 1:
        w, h = plan.size_m(unit.scale)
        others = ", ".join(f"{v.id} ({_KIND_IT.get(v.kind, v.kind)})" for v in views if v is not plan)
        notes.append(f"Il foglio ha {len(views)} viste: converto la vista {plan.id} ({_KIND_IT.get(plan.kind, plan.kind)}, "
                     f"{w:.0f} x {h:.0f} m), {why}. Le altre: {others}. Per un'altra usa --vista N.")
    return Analysis(soup, unit, views, plan, notes)


_KIND_IT = {"plan": "pianta", "roof": "copertura", "elevation": "prospetto", "section": "sezione", "site": "planimetria "
            "generale", "detail": "dettaglio", "?": "non riconosciuta"}


def write_views_image(analysis: Analysis, path) -> "Path":
    """The sheet as the program understood it: every view framed and numbered with its type (the converted one in
    red). Needs matplotlib."""
    from pathlib import Path

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    soup, scale = analysis.soup, analysis.unit.scale
    fig, ax = plt.subplots(figsize=(15, 10))
    if len(soup.seg):
        step = max(1, len(soup.seg) // 40000)
        s = soup.seg[::step] * scale
        from matplotlib.collections import LineCollection

        ax.add_collection(LineCollection([[(a, b), (c, d)] for a, b, c, d in s], colors="#bbbbbb", linewidths=0.25))
    for v in analysis.views:
        x0, y0, x1, y1 = (c * scale for c in v.bbox)
        colour = "#d62728" if v is analysis.plan else {"elevation": "#1f77b4", "section": "#9467bd", "roof": "#ff7f0e",
                                                        "site": "#2ca02c"}.get(v.kind, "#555555")
        ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, ec=colour, lw=1.8 if v is analysis.plan else 1.0))
        label = f"{v.id}: {_KIND_IT.get(v.kind, v.kind)}" + (f"  \"{v.titles[0][:40]}\"" if v.titles else "")
        if v.copy_of:
            label += f" (copia di {v.copy_of})"
        if v.parent:
            label += f" (nella vista {v.parent})"
        if v.matched:
            label += f"  -> {v.matched}"
        ax.text(x0, y1, label, fontsize=8, color=colour, va="bottom", ha="left",
                bbox={"fc": "white", "ec": "none", "alpha": 0.8, "pad": 1})
    ax.autoscale()
    ax.set_aspect("equal")
    ax.set_title(f"Il foglio letto dal programma: unita' {analysis.unit.unit}"
                 + (f" (il file dichiara {analysis.unit.declared})" if analysis.unit.differs else "")
                 + " - in rosso la vista che viene convertita", fontsize=9)
    path = Path(path)
    fig.savefig(path, dpi=80, bbox_inches="tight")
    plt.close(fig)
    return path


# --- the walls, when the layer names do not say -----------------------------------------------

THIN = 0.6  # m: wall bodies are thinner than this; what is thicker is a floor, a room, a hatch over an area
CLOSE = 0.8  # m: the gaps of doors and windows up to twice this are shut before the rooms are counted
MAX_ROOM = 2500.0  # m2: a hole of the walls up to this size is a room or the inside of a building (not the sheet around it)
PAIR_GAIN = 1.25  # the reading with parallel line pairs replaces the plain one if it scores this many times more
MIN_WALL_SCORE = 3.0  # a layer that scores less is not taken for the walls
ADD_FACTOR = 1.5  # a layer is added to the walls the names found only if it makes them this many times better
MIN_GAIN = 1.0  # ... and a further layer only if it raises the score by at least this much
MAX_ADDED = 3  # no more than this many layers are added to what the names found


def candidate_layers(a: Analysis, rules=None) -> list[str]:
    """The layers that could hold the walls of the chosen plan: long straight lines (not the thousands of short
    ones of furniture drawn with lines) or hatches, inside the view; and the layers the names call walls."""
    plan = a.plan
    if plan is None:
        return []
    soup, scale = a.soup, a.unit.scale
    x0, y0, x1, y1 = plan.bbox
    seg = soup.seg
    mx, my = (seg[:, 0] + seg[:, 2]) / 2, (seg[:, 1] + seg[:, 3]) / 2
    inside = (mx >= x0) & (mx <= x1) & (my >= y0) & (my <= y1)
    length = np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1]) * scale
    out = []
    for i, name in enumerate(soup.layers):
        m = inside & (soup.seg_layer == i)
        long_ = int(np.count_nonzero(length[m] >= 1.0))
        hatches = sum(1 for h in soup.hatches if h["layer"] == name
                      and x0 <= (h["bbox"][0] + h["bbox"][2]) / 2 <= x1 and y0 <= (h["bbox"][1] + h["bbox"][3]) / 2 <= y1)
        category = rules.classify_layer(name) if rules is not None else None
        if category not in (None, "wall"):
            continue  # the name says it is a door layer, a roof, floors...: not the walls
        if category is None and rules is not None and (rules.vetoed(name) or rules.garden_kind(name)):
            continue  # a layer the names do not call walls, and that is furniture, the garden, an annotation...
        if category == "wall" or (long_ >= 8 and m.sum() / max(long_, 1) <= 30) or hatches >= 5:
            out.append(name)
    return out


def wall_metrics(footprint, plan_area_m2: float) -> dict:
    """How much a footprint looks like the walls of a plan: the part of it thinner than THIN, the rooms that part
    closes, how much of the view it spans, how many crumbs it has."""
    from shapely.geometry import Polygon

    from .geom import polygons_of

    if footprint.is_empty:
        return {"score": 0.0, "area": 0.0, "rooms": 0, "thin": 0.0, "frag": 0, "cover": 0.0, "enclosed": 0.0}
    thin = footprint.difference(footprint.buffer(-THIN / 2, join_style="mitre").buffer(THIN / 2, join_style="mitre"))
    bodies = polygons_of(thin)
    area = thin.area
    # Rooms: the holes of the walls once the openings (doors, windows: gaps up to 2 * CLOSE wide) are shut.
    shut = thin.buffer(CLOSE, join_style="mitre", mitre_limit=2.0).buffer(-CLOSE, join_style="mitre", mitre_limit=2.0)
    holes = [Polygon(r).area for b in polygons_of(shut) for r in b.interiors]
    rooms = sum(1 for a_ in holes if 1.0 <= a_ <= MAX_ROOM)
    enclosed = sum(a_ for a_ in holes if 1.0 <= a_ <= MAX_ROOM)
    frag = sum(1 for b in bodies if b.area < 0.08)
    x0, y0, x1, y1 = thin.bounds if not thin.is_empty else (0, 0, 0, 0)
    cover = ((x1 - x0) * (y1 - y0)) / max(plan_area_m2, 1e-9)
    share = area / footprint.area if footprint.area else 0.0
    score = (3.0 * min(rooms, 10) + 0.05 * min(area, 120.0) + 2.0 * min(cover, 1.0) - 0.05 * min(frag, 20)) * share
    return {"score": score, "area": area, "rooms": rooms, "thin": share, "frag": frag, "cover": cover,
            "enclosed": enclosed}


def choose_wall_layers(doc, cfg, a: Analysis, rules) -> tuple[list[str], list[str], list[str]]:
    """(the layers added because of their shape, notes, the added layers read with line pairs). The layers the names
    call walls stay as they are (the name rules go on deciding them); this adds the other
    layers that look like walls inside the chosen plan: when the names found none, or little, every layer that looks
    like walls by itself; when the names found good walls, only a layer that makes them clearly better.
    ``rules``: the default ``LayerRules`` (what the names say, not what the user or the proposals put in the
    configuration)."""
    from dataclasses import replace

    from .config import LayerRules
    from .geom import union
    from .reader import read_items
    from .walls import build_wall_layers, clean_footprint

    plan = a.plan
    cands = candidate_layers(a, rules)
    if plan is None or not cands:
        return [], [], []
    named = [c for c in cands if rules.classify_layer(c) == "wall"]
    others = tuple(c for c in cands if c not in named)
    sub = replace(cfg, units=a.unit.unit, area=plan.bbox, layers=LayerRules({"wall": cands}), garden=False,
                  auto=False, pair_layers=(), shape_openings=False)
    items = read_items(doc, sub).items
    w, h = plan.size_m(a.unit.scale)
    area = max(w * h, 1e-9)
    footprints = {k: clean_footprint(f, sub.merge_tolerance) for k, f in build_wall_layers(items, sub, []).items()}
    # A layer that is not named is also tried with two parallel lines counting as a wall (open ends): that reading is
    # kept only where it is clearly better than the plain one (closed outlines, hatches), which is the safe one.
    paired = {k: clean_footprint(f, sub.merge_tolerance) for k, f in
              build_wall_layers(items, replace(sub, pair_layers=others), []).items() if k in others}
    use_pairs: list[str] = []
    for k, f in paired.items():
        if k in footprints and wall_metrics(f, area)["score"] > PAIR_GAIN * max(wall_metrics(footprints[k], area)["score"], 0.5):
            footprints[k] = f
            use_pairs.append(k)
    named = [c for c in named if c in footprints]
    chosen = list(named)
    current = wall_metrics(union([footprints[c] for c in chosen]), area)
    singles = {c: wall_metrics(footprints[c], area)["score"] for c in footprints if c not in named}
    pool = [c for c in sorted(singles, key=singles.get, reverse=True) if singles[c] > 0.0]
    added: list[tuple[str, dict]] = []
    weak = not named or current["score"] < MIN_WALL_SCORE
    if weak:  # the names say nothing (or little): each layer on its own merits
        for c in pool[:MAX_ADDED]:
            if singles[c] >= MIN_WALL_SCORE:
                chosen.append(c)
                added.append((c, {}))
        if added:
            current = wall_metrics(union([footprints[c] for c in chosen]), area)
    for _ in range(MAX_ADDED - len(added)):  # then one layer at a time: the one that helps most, if it helps enough
        best, best_m = None, None
        for c in pool:
            if c in chosen:
                continue
            m = wall_metrics(union([footprints[k] for k in chosen + [c]]), area)
            if best_m is None or m["score"] > best_m["score"]:
                best, best_m = c, m
        if best is None or best_m["score"] < MIN_WALL_SCORE or best_m["score"] < current["score"] + MIN_GAIN \
                or (not weak and not added and best_m["score"] < ADD_FACTOR * current["score"]):
            break
        chosen.append(best)
        added.append((best, best_m))
        current = best_m
    notes: list[str] = []
    if added:
        names = ", ".join(f"'{c}'" for c, _ in added)
        notes.append(f"Muri: {'oltre ai layer dei nomi (' + ', '.join(named) + ') ' if named else ''}nella pianta {names} "
                     f"{'ha' if len(added) == 1 else 'hanno'} la forma dei muri: insieme {current['area']:.0f} m2 di corpi "
                     f"sottili che chiudono {current['rooms']} locali.")
    added_names = [c for c, _ in added]
    return added_names, notes, [c for c in use_pairs if c in added_names]


def names_say_openings(soup: Soup, rules) -> bool:
    """Does the drawing have layers or blocks that the names call doors or windows? Then the doors and windows are
    read from them, and the shapes of arcs and gaps do not add any."""
    if any(rules.classify_layer(l) in ("door", "window") for l in soup.layers):
        return True
    if any(rules.classify_layer(i["layer"]) in ("door", "window") for i in soup.inserts):
        return True
    return any(rules.classify_block(i["name"]) in ("door", "window") for i in soup.inserts)


def apply_analysis(doc, cfg, a: Analysis, rules=None) -> tuple["Config", list[str]]:
    """What the analysis changes in the configuration: the unit, the area of the plan, the wall layers, whether doors
    and windows are told by their shape. Whatever the user gave (``--unita``, ``--area``, ``--muri``) is left alone."""
    import glob
    from dataclasses import replace

    from .config import LayerRules

    rules = rules or LayerRules()
    notes = list(a.notes)
    change: dict = {}
    u = a.unit
    if cfg.units is None and u.differs:
        change["units"] = u.unit
        notes.insert(0, f"Il file dichiara '{u.declared}' ma i numeri sono in '{u.unit}' "
                        f"({'; '.join(u.evidence) or 'misure e simboli lo indicano'}): uso '{u.unit}'.")
    elif cfg.units is None and u.declared is None and u.margin >= UNIT_SURE:
        change["units"] = u.unit
        notes.insert(0, f"Il file non dichiara le unita' ma i numeri sono in '{u.unit}' "
                        f"({'; '.join(u.evidence) or 'misure e simboli lo indicano'}): uso '{u.unit}'.")
    too_small = a.plan is not None and cfg.view is None and min(a.plan.size_m(a.unit.scale)) < MIN_PLAN_SIDE  # one asked for stays
    if too_small and cfg.area is None and len(a.views) > 1:
        w, h = a.plan.size_m(a.unit.scale)
        notes.append(f"La vista {a.plan.id} misura solo {w:.1f} x {h:.1f} m: non e' una pianta (l'unita' del file e' "
                     f"forse sbagliata): non ritaglio il foglio, converto tutto. Indica la pianta con --vista N o --area.")
    elif cfg.area is None and a.plan is not None and len(a.views) > 1:
        margin = 1.0 / a.unit.scale
        x0, y0, x1, y1 = a.plan.bbox
        change["area"] = (x0 - margin, y0 - margin, x1 + margin, y1 + margin)
        change["area_auto"] = True
        if cfg.garden_area is None and cfg.garden_reach is None:
            # a garden lies around its house, not on the other side of the sheet: the lot the plan is drawn in (when the
            # plan lies inside a bigger drawing) or a few tens of metres around it
            host = next((w for w in a.views if w.id == a.plan.parent), None)
            gx0, gy0, gx1, gy1 = host.bbox if host is not None else a.plan.bbox
            reach = GARDEN_REACH / a.unit.scale
            change["garden_reach"] = (gx0 - reach, gy0 - reach, gx1 + reach, gy1 + reach)
    elif a.plan is None and len(a.views) > 1 and cfg.view is None:
        notes.append("Il foglio ha piu' viste e non ho capito quale sia la pianta: indicala con --vista N "
                     "(guarda l'immagine *_viste.png).")
    new = replace(cfg, **change) if change else cfg
    proposed = "wall" in getattr(cfg, "proposed", ())
    if "wall" not in cfg.layers.overrides or proposed:
        added, more, by_shape = choose_wall_layers(doc, new, a, rules)
        if added:  # the name rules stop deciding once there is a list: it holds the layers the names call walls too
            named = [glob.escape(l) for l in a.soup.layers if rules.classify_layer(l) == "wall"]
            kept = list(cfg.layers.overrides.get("wall", [])) if proposed else []
            wall = list(dict.fromkeys(named + kept + [glob.escape(l) for l in added]))
            new = replace(new, layers=LayerRules({**cfg.layers.overrides, "wall": wall}), pair_layers=tuple(by_shape))
            a.wall_layers = added
        notes.extend(more)
    return replace(new, auto=False, shape_openings=not names_say_openings(a.soup, rules), analysis=a,
                   analysis_notes=notes), notes
