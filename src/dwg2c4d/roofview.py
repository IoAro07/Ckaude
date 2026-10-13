"""The roof drawn as a view of the sheet: cleaned, and laid over the plan.

Many sheets draw the roof plan (PLANIMETRIA COPERTURA) as a drawing of its own, away from the plan, sometimes turned
by a quarter, in the same scale, on layers ('0') that no name calls roof. The analysis knows which view it is
(``View.kind == "roof"``). What the roof builder (roof.py) needs from it is the outline and the ridge, hip and valley
lines in the frame of the plan; the view also holds the lines tiles are drawn with, panels, skylights, dimensions,
section marks, arrows, the gutters and the wall lines under the eaves. ``clean_roof`` takes those out and
``register_roof`` finds the move (quarter turns and a shift) that lays the outline over the building.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import shapely
from scipy.signal import fftconvolve
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union

from .config import LayerRules
from .geom import polygons_of, union
from .reader import Item, Prim
from .walls import _key, closure_segments, thinness

if TYPE_CHECKING:
    from .roof import Roof

# --- cleaning ---
MIN_ROOF_AREA = 4.0  # m2: a closed outline smaller than this is a skylight, a chimney or a panel, not a roof
FASCIA = 0.30  # m: a closed outline thinner than this is a gutter or a fascia board along the eaves
OUT_TOLERANCE = 0.30  # m: a line that goes further than this beyond the outline is a dimension or a section mark
CLOSE_GAP = 0.10  # m: roof lines that stop this short of each other are joined
GRID = 1e-6  # m: input snapping, as in roof.py
PATTERN_PARTS = 60  # a layer with this many pieces in the view, all short, is a hatch drawn with lines (tiles)
PATTERN_LENGTH = 1.0  # m: ... "short" is a median length under this
JUNK_LAYERS = ("sezion", "section", "quot", "dimens", "text", "testi", "tratteg", "hatch", "campit", "pattern",
               "pannell", "fotovolt", "photovolt", "solar", "panel")  # prefixes of the words a layer name is made of
EAVES_INSET = (0.2, 1.5)  # m: a loop this far inside the outline all round is the wall under the eaves
INSET_FIT = 0.05  # ... when it differs from the outline shrunk by that much by less than this share of its area

# --- registration ---
STEP = 0.25  # m: cell of the first, coarse search
FINE_STEP = 0.10  # m: where the exact search starts (it halves down to MIN_STEP)
MIN_STEP = 0.01  # m
DILATIONS = (0.0, 0.3, 0.6, 1.0)  # m: the building grown by the eaves, tried in the coarse search
MAX_OVERHANG = 1.5  # m: ... and the exact search never grows it more than this
MIN_IOU = 0.45  # the roof is on the plan if it overlaps the building (grown by the eaves) at least this well
MIN_COVER = 0.6  # ... and at least this share of the roof stands over the building (grown)
MIRROR_MARGIN = 0.1  # a mirrored pose is taken only if it overlaps this much better (iou) than any that is not
SAME_LINES = 0.05  # m: the lines of the roof in two poses are the same when they lie this close
TIE = 0.97  # a pose that scores at least this share of the best one fits as well: the plain one (no turn) is taken
CENTRE_WEIGHT = 0.02  # a shift that moves the centre of the roof away from the centre of the walls costs this much per diagonal
MAX_CELLS = 4_000_000  # no raster is made of more cells than this (the step grows instead)
MAX_VIEWS = 4  # no more roof plans than this are tried (the biggest)

# --- the written slope ---
PITCH_TEXT = re.compile(r"pend\w*\W+(?:\w+\W+){0,3}?(\d{1,3}(?:[.,]\d+)?)\s*(%|°|gradi)", re.I)  # Pendenza 25 %   pendenza falde 30%   Pend. 20 gradi
PITCH_PERCENT = (8.0, 100.0)  # a written slope in percent is a roof's if it lies in this range (a 2 % gutter is not)
PITCH_DEGREES = (5.0, 60.0)  # ... and in degrees in this one


@dataclass
class Registration:
    """How the roof plan is laid over the plan: turned by ``turns`` quarters (counter-clockwise, about the origin; after
    a mirror of x when ``mirror``), then moved by ``shift`` (m)."""

    turns: int
    mirror: bool
    shift: tuple[float, float]
    iou: float  # how well the outline covers the building grown by the eaves (0 to 1)
    overhang: float  # m: how far the building was grown for the best fit
    cover: float = 1.0  # the share of the roof that stands over the building (grown by the eaves)
    alternatives: list["Registration"] = field(default_factory=list)  # other poses that fit nearly as well (TIE)

    @property
    def tied(self) -> bool:
        return bool(self.alternatives)

    @property
    def sound(self) -> bool:
        """Does it fit well enough to be believed?"""
        return self.iou >= MIN_IOU and self.cover >= MIN_COVER


def pose_of(turns: int, mirror: bool) -> tuple[float, float, float, float]:
    """The matrix (a, b, d, e) of x' = a x + b y, y' = d x + e y: a mirror of x, then ``turns`` quarter turns."""
    c, s = (1, 0, -1, 0)[turns % 4], (0, 1, 0, -1)[turns % 4]
    sx = -1.0 if mirror else 1.0
    return c * sx, -s, s * sx, c


def place(geom: BaseGeometry, reg: Registration) -> BaseGeometry:
    """``geom`` of the roof plan moved onto the plan."""
    a, b, d, e = pose_of(reg.turns, reg.mirror)
    return affinity.affine_transform(geom, [a, b, d, e, reg.shift[0], reg.shift[1]])


# ------------------------------------------------------------------------------------------------
# cleaning

@dataclass
class _Part:
    layer: str
    line: LineString
    ring: Polygon | None = None  # the outline a closed polyline draws


@dataclass
class CleanRoof:
    """What is left of a roof view after cleaning: roof items (lines only) in the frame of the sheet, the outline of the
    roof (the union of its planes) and, when the view draws the wall under the eaves, how far the eaves reach."""

    items: list[Item]
    outline: BaseGeometry
    overhang: float | None


def _parts(items: list[Item]) -> list[_Part]:
    """The lines and closed outlines of the items; hatches are left out (their pattern is not geometry the roof needs)."""
    parts: list[_Part] = []
    for it in items:
        for p in it.prims:
            if p.kind == "line" and p.geom.length > 0:
                parts.append(_Part(it.layer, p.geom))
            elif p.kind == "ring":
                for poly in polygons_of(p.geom):
                    parts.append(_Part(it.layer, LineString(poly.exterior.coords), poly))
    return parts


def _loop(part: _Part) -> Polygon | None:
    """The shape a part closes: a closed polyline, or an open one whose end meets its start (drawn as lines)."""
    if part.ring is not None:
        return part.ring
    c = list(part.line.coords)
    if len(c) >= 4 and math.dist(c[0], c[-1]) <= CLOSE_GAP:
        poly = Polygon(c)
        return poly if poly.is_valid and poly.area > 0 else None
    return None


def _junk_layers(parts: list[_Part]) -> set[str]:
    """Layers that hold no roof structure: dimensions, texts, section marks, hatches and what is drawn with lines in
    their place (a layer of many short pieces: tiles), panels."""
    junk: set[str] = set()
    lengths: dict[str, list[float]] = {}
    for p in parts:
        lengths.setdefault(p.layer, []).append(p.line.length)
    rules = LayerRules()
    for layer, ls in lengths.items():
        if any(t.startswith(JUNK_LAYERS) for t in rules.tokens(layer)) or rules.is_surface(layer) \
                or (len(ls) >= PATTERN_PARTS and float(np.median(ls)) < PATTERN_LENGTH):
            junk.add(layer)
    return junk


def _pieces(geom: BaseGeometry) -> list[LineString]:
    """The lines of a geometry (a line, or a collection of them), none of them empty."""
    return [g for g in getattr(geom, "geoms", [geom]) if isinstance(g, LineString) and g.length > 0]


def _faces(lines: list[LineString]) -> list[Polygon]:
    """The closed planes the lines make (thin strips, gutters and slivers left out)."""
    segs = _pieces(unary_union(lines))
    network = unary_union(segs + closure_segments(segs, CLOSE_GAP))
    return [f for f in polygonize(network) if f.area >= MIN_ROOF_AREA and thinness(f) >= FASCIA]


def _outline(parts: list[_Part]) -> BaseGeometry:
    """The outline of the roof as far as the drawing says it: the closed outlines that are big enough and no strips, or
    else the planes the long lines make. Empty when there is none."""
    loops = [poly for p in parts if (poly := _loop(p)) is not None and poly.area >= MIN_ROOF_AREA and thinness(poly) >= FASCIA]
    if loops:
        return union(loops)
    lines = [p.line for p in parts if p.line.length >= math.sqrt(MIN_ROOF_AREA)]
    return union(_faces(lines)) if lines else Polygon()


def _eaves_inset(loop: Polygon, outline: BaseGeometry) -> float | None:
    """How far ``loop`` lies inside the outline all round (the wall under the eaves), or None when it does not."""
    if loop.area >= outline.area or outline.length == 0 or not outline.buffer(1e-6).covers(loop):
        return None
    d = float(np.median([outline.boundary.distance(Point(c)) for c in loop.exterior.coords]))
    if not EAVES_INSET[0] <= d <= EAVES_INSET[1]:
        return None
    shrunk = outline.buffer(-d, join_style="mitre")
    return d if loop.symmetric_difference(shrunk).area <= INSET_FIT * loop.area else None


def _prune(lines: list[LineString]) -> list[LineString]:
    """The lines without the dangling ones: a line with a free end lies in no plane (arrows, leaders, loose strokes)."""
    while True:
        degree = Counter(k for l in lines for k in (_key(l.coords[0]), _key(l.coords[-1])))
        kept = [l for l in lines if degree[_key(l.coords[0])] > 1 and degree[_key(l.coords[-1])] > 1]
        if len(kept) == len(lines):
            return kept
        lines = kept


def _components(lines: list[LineString]) -> list[list[LineString]]:
    """The lines grouped by the nodes they share."""
    parent: dict = {}

    def find(k):
        while parent.setdefault(k, k) != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for l in lines:
        parent[find(_key(l.coords[0]))] = find(_key(l.coords[-1]))
    groups: dict = {}
    for l in lines:
        groups.setdefault(find(_key(l.coords[0])), []).append(l)
    return list(groups.values())


def clean_roof(items: list[Item]) -> CleanRoof | None:
    """The roof structure of a roof view: its outline and its ridge, hip and valley lines. Out go the layers of
    dimensions, texts, section marks and tile patterns; small closed outlines (skylights, chimneys, panels) and strips
    (gutters); the wall line that runs inside the outline all round (that is the wall under the eaves: it tells the
    overhang, it is no roof line); everything that goes beyond the outline (dimensions, section lines); the lines that
    end in the air (arrows); and a closed shape lying inside a plane of another (a panel array)."""
    parts = _parts(items)
    if not parts:
        return None
    junk = _junk_layers(parts)
    parts = [p for p in parts if p.layer not in junk]
    outline = _outline(parts)
    if outline.is_empty:
        return None
    grown = outline.buffer(OUT_TOLERANCE)
    overhang: float | None = None
    kept: list[LineString] = []
    for p in parts:
        loop = _loop(p)
        if loop is not None:
            if loop.area < MIN_ROOF_AREA or thinness(loop) < FASCIA:
                continue
            inset = _eaves_inset(loop, outline)
            if inset is not None:
                overhang = inset if overhang is None else min(overhang, inset)
                continue
        if p.line.difference(grown).length > 1e-6:
            continue
        kept.append(shapely.set_precision(p.line, GRID))
    kept = [l for l in kept if not l.is_empty]
    if not kept:
        return None
    segs = _pieces(unary_union(kept))
    lines = _prune(_pieces(unary_union(segs + closure_segments(segs, CLOSE_GAP))))
    groups = []
    for comp in _components(lines):
        planes = _faces(comp)
        if planes:
            groups.append((comp, union(planes)))
    groups = [(c, r) for i, (c, r) in enumerate(groups)
              if not any(j != i and r.area < o.area and o.buffer(1e-6).covers(r) for j, (_, o) in enumerate(groups))]
    if not groups:
        return None
    lines = [l for comp, _ in groups for l in comp]
    return CleanRoof([Item("0", None, "roof", [Prim(l, "line") for l in lines])], union(r for _, r in groups), overhang)


# ------------------------------------------------------------------------------------------------
# registration

def _mask(shape: BaseGeometry, x0: float, y0: float, nx: int, ny: int, step: float) -> np.ndarray:
    """``shape`` on a grid of ``ny`` x ``nx`` cells of ``step`` metres whose lower left corner is (x0, y0)."""
    xs = x0 + (np.arange(nx) + 0.5) * step
    ys = y0 + (np.arange(ny) + 0.5) * step
    gx, gy = np.meshgrid(xs, ys)
    return shapely.contains_xy(shape, gx, gy).astype(np.float32)


def _iou(a: BaseGeometry, b: BaseGeometry) -> float:
    inter = a.intersection(b).area
    return inter / (a.area + b.area - inter) if inter > 0 else 0.0


class _Fit:
    """The exact score of a pose: how well the roof, moved, covers the building grown by ``d`` (cached per d)."""

    def __init__(self, roof: BaseGeometry, building: BaseGeometry) -> None:
        self.roof, self.building = roof, building
        self._grown: dict[float, BaseGeometry] = {}
        c, f = roof.centroid, building.centroid
        self.diagonal = math.hypot(*(b - a for a, b in zip(building.bounds[:2], building.bounds[2:])))
        self.base = (f.x - c.x, f.y - c.y)  # the shift that puts the centres together

    def grown(self, d: float) -> BaseGeometry:
        d = round(d, 3)
        if d not in self._grown:
            self._grown[d] = self.building.buffer(d, join_style="mitre") if d > 0 else self.building
        return self._grown[d]

    def score(self, tx: float, ty: float, d: float) -> tuple[float, float]:
        """(score, iou): the iou less a small cost for a roof off the centre of the walls (it only breaks ties)."""
        moved = affinity.translate(self.roof, tx, ty)
        iou = _iou(moved, self.grown(d))
        off = math.hypot(tx - self.base[0], ty - self.base[1])
        return iou - CENTRE_WEIGHT * off / max(self.diagonal, 1e-6), iou


def _refine(fit: _Fit, tx: float, ty: float, d: float) -> tuple[float, float, float, float, float]:
    """Pattern search around a shift and a growth: (tx, ty, d, score, iou)."""
    best = fit.score(tx, ty, d)
    step = FINE_STEP
    while step >= MIN_STEP:
        moved = False
        for dx, dy, dd in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (1, 1, 0), (-1, -1, 0), (1, -1, 0), (-1, 1, 0),
                           (0, 0, 1), (0, 0, -1)):
            nd = d + dd * step
            if not 0.0 <= nd <= MAX_OVERHANG:
                continue
            cand = fit.score(tx + dx * step, ty + dy * step, nd)
            if cand[0] > best[0] + 1e-9:
                best, tx, ty, d, moved = cand, tx + dx * step, ty + dy * step, nd, True
        if not moved:
            step /= 2.0
    return tx, ty, d, best[0], best[1]


def register_roof(roof: BaseGeometry, building: BaseGeometry) -> Registration | None:
    """The move that lays the outline of a roof plan over the building, or None when it fits nowhere (``fit_roof`` is the
    best move whatever it is worth)."""
    reg = fit_roof(roof, building)
    return reg if reg is not None and reg.sound else None


def fit_roof(roof: BaseGeometry, building: BaseGeometry) -> Registration | None:
    """The best move that lays the outline of a roof plan over the building (None only when nothing can be tried).

    Every pose (a quarter turn each, and the mirror images) is tried: the two shapes are drawn on a coarse grid and
    their overlap is worked out for every shift at once (a correlation), for a few growths of the building; the best
    shifts are then polished with the exact areas. A mirror image is taken only when it fits ``MIRROR_MARGIN`` better
    than any pose that is not mirrored. ``roof`` and ``building`` are in metres. It is ``sound`` when it overlaps the
    building grown by the eaves (``MIN_IOU``) and the roof stands over it (``MIN_COVER``)."""
    if roof.is_empty or building.is_empty:
        return None
    bx0, by0, bx1, by1 = building.bounds
    step = STEP
    while ((bx1 - bx0 + 2 * MAX_OVERHANG) / step) * ((by1 - by0 + 2 * MAX_OVERHANG) / step) > MAX_CELLS:
        step *= 2.0
    pad = MAX_OVERHANG + step
    fx0, fy0 = bx0 - pad, by0 - pad
    fnx, fny = int(math.ceil((bx1 - bx0 + 2 * pad) / step)), int(math.ceil((by1 - by0 + 2 * pad) / step))
    masks = {d: _mask(building.buffer(d, join_style="mitre") if d else building, fx0, fy0, fnx, fny, step) for d in DILATIONS}
    candidates: list[tuple[float, int, bool, float, float, float]] = []  # (score, turns, mirror, tx, ty, d), coarse
    for mirror in (False, True):
        for turns in range(4):
            posed = place(roof, Registration(turns, mirror, (0.0, 0.0), 0.0, 0.0))
            rx0, ry0, rx1, ry1 = posed.bounds
            rnx, rny = int(math.ceil((rx1 - rx0) / step)) + 1, int(math.ceil((ry1 - ry0) / step)) + 1
            if rnx * rny > MAX_CELLS:
                continue
            r = _mask(posed, rx0, ry0, rnx, rny, step)
            area_r = float(r.sum())
            if area_r == 0:
                continue
            best = None
            for d, f in masks.items():
                corr = fftconvolve(f, r[::-1, ::-1], mode="full")
                union_ = float(f.sum()) + area_r - corr
                iou = np.where(corr > 0.5, corr / np.maximum(union_, 1.0), 0.0)
                k = int(np.argmax(iou))
                i, j = divmod(k, iou.shape[1])
                if best is None or iou[i, j] > best[0]:
                    # roof cell p lies over the cell p + (i - (rny - 1)) of the building grid
                    best = (float(iou[i, j]), fx0 - rx0 + (j - (rnx - 1)) * step, fy0 - ry0 + (i - (rny - 1)) * step, d)
            if best is not None:
                candidates.append((best[0], turns, mirror, best[1], best[2], best[3]))
    if not candidates:
        return None
    results = []
    for _, turns, mirror, tx, ty, d in sorted(candidates, reverse=True):
        posed = place(roof, Registration(turns, mirror, (0.0, 0.0), 0.0, 0.0))
        tx, ty, d, score, iou = _refine(_Fit(posed, building), tx, ty, d)
        results.append((score, iou, turns, mirror, tx, ty, d, posed))
    results.sort(key=lambda r: -r[0])
    plain = [r for r in results if not r[3]]
    best = results[0]
    if best[3] and plain and best[0] < plain[0][0] + MIRROR_MARGIN:
        best = plain[0]  # a turned copy is far likelier than a mirrored one: the mirror must be clearly better
    tied = [r for r in results if r is not best and r[0] >= TIE * best[0] and (not r[3] or best[3])]
    if tied:  # a square roof fits the same building in several poses: the one drawn as it is wins
        plain = [r for r in [best, *tied] if r[2] == 0 and not r[3]]
        if plain:
            best = plain[0]
            tied = [r for r in results if r is not best and r[0] >= TIE * best[0] and (not r[3] or best[3])]
    return _registration(best, building, tied)


def _registration(result: tuple, building: BaseGeometry, tied: list[tuple] = ()) -> Registration:
    """The Registration of one result of the search: (score, iou, turns, mirror, tx, ty, d, the roof posed)."""
    _, iou, turns, mirror, tx, ty, d, posed = result
    cover = affinity.translate(posed, tx, ty).intersection(building.buffer(d, join_style="mitre")).area / posed.area
    return Registration(turns, mirror, (tx, ty), iou, d, cover, [_registration(r, building) for r in tied])


# ------------------------------------------------------------------------------------------------
# the roof plan of a sheet

@dataclass
class ViewRoof:
    """A roof plan found in a view of the sheet and laid over the plan: its roof items in the frame of the plan."""

    items: list[Item]
    view: int
    registration: Registration
    overhang: float | None  # m: from the wall line the view draws under the eaves, else from the registration
    size: tuple[float, float]  # m: the roof plan
    ambiguous: bool  # the roof fits the plan in another pose too, and the lines of the roof do not look the same in it


def pitch_from_texts(texts: list[str]) -> tuple[float, str] | None:
    """The slope written in the drawing as (degrees, the words), or None. A slope in percent (rise per 100 of run) or in
    degrees; the one written most often when the sheet gives several. Values no roof has (a 2 % gutter, a 0.5 %
    terrace) are not looked at."""
    found: list[tuple[float, str]] = []
    for text in texts:
        for m in PITCH_TEXT.finditer(text):
            value = float(m.group(1).replace(",", "."))
            if m.group(2) == "%":
                if PITCH_PERCENT[0] <= value <= PITCH_PERCENT[1]:
                    found.append((math.degrees(math.atan(value / 100.0)), f"{m.group(1)} %"))
            elif PITCH_DEGREES[0] <= value <= PITCH_DEGREES[1]:
                found.append((value, f"{m.group(1)} gradi"))
    if not found:
        return None
    count = Counter(round(deg, 1) for deg, _ in found)
    best = max(count, key=lambda deg: (count[deg], -deg))
    return next((deg, words) for deg, words in found if round(deg, 1) == best)


def written_pitch(analysis) -> tuple[float, str] | None:
    """The slope written on the views of the sheet that tell about the roof: the roof plan, the sections, the elevations
    and the plan itself."""
    kinds = ("roof", "section", "elevation")
    boxes = [v for v in analysis.views if v.kind in kinds or v is analysis.plan]
    texts = [t["text"] for t in analysis.soup.texts if any(v.contains(t["x"], t["y"]) for v in boxes)]
    return pitch_from_texts(texts)


def roof_views(analysis) -> list:
    """The views of the sheet that are roof plans, the biggest first."""
    return sorted((v for v in analysis.views if v.kind == "roof" and v is not analysis.plan), key=lambda v: -v.cells)[:MAX_VIEWS]


def roof_from_view(doc, cfg, analysis, building: BaseGeometry, unit: str, scale: float,
                   warnings: list[str]) -> ViewRoof | None:
    """The roof of the sheet's roof plan, laid over ``building`` (the footprint of the plan, metres): of the views that are
    roof plans the one whose outline fits the building best (``register_roof``). None when there is none or none fits
    (the warnings say so: a roof put in the wrong place is worse than no roof)."""
    from dataclasses import replace

    from .reader import read_items

    views = roof_views(analysis)
    if not views:
        return None
    area = (min(v.bbox[0] for v in views), min(v.bbox[1] for v in views), max(v.bbox[2] for v in views), max(v.bbox[3] for v in views))
    read = read_items(doc, replace(cfg, layers=LayerRules(), area=None), area=area, ignore_veto=True, keep_other=True, unit=unit)
    bounds = [shapely.total_bounds([p.geom for p in it.prims]) for it in read.items]
    best: ViewRoof | None = None
    worst: Registration | None = None
    empty = 0
    for v in views:
        x0, y0, x1, y1 = (c * scale for c in v.bbox)
        mine = [it for it, b in zip(read.items, bounds) if x0 <= (b[0] + b[2]) / 2 <= x1 and y0 <= (b[1] + b[3]) / 2 <= y1]
        clean = clean_roof(mine)
        if clean is None:
            empty += 1
            continue
        reg = fit_roof(clean.outline, building)
        if reg is None or not reg.sound:
            worst = reg if worst is None or (reg is not None and reg.iou > worst.iou) else worst
            continue
        placed = [Item(it.layer, None, "roof", [Prim(place(p.geom, reg), p.kind) for p in it.prims]) for it in clean.items]
        drawn = unary_union([p.geom for it in clean.items for p in it.prims])
        ambiguous = any(place(drawn, alt).difference(place(drawn, reg).buffer(SAME_LINES)).length > 0.02 * drawn.length
                        for alt in reg.alternatives)
        found = ViewRoof(placed, v.id, reg, clean.overhang if clean.overhang is not None else reg.overhang, v.size_m(scale),
                         ambiguous)
        if best is None or reg.iou > best.registration.iou:
            best = found
    if best is not None:
        return best
    if worst is not None:
        warnings.append(
            f"Tetto: la vista {views[0].id} e' la pianta della copertura ma il suo contorno non combacia con i muri della "
            f"pianta convertita (sovrapposizione migliore {worst.iou * 100:.0f} %, ce ne vuole almeno {MIN_IOU * 100:.0f} %): non "
            "l'ho messa, per non piazzare un tetto sbagliato. Se sai dov'e' indicala tu con --layer-tetto, --area-tetto e "
            "--sposta-tetto.")
    elif empty:
        warnings.append(
            f"Tetto: nella vista {views[0].id} (la pianta della copertura) non ho trovato nessun contorno chiuso da cui "
            "fare le falde: indica i layer del tetto con --layer-tetto.")
    return None


def view_roof_notes(found: ViewRoof, roof: "Roof", pitch: tuple[float, str] | None) -> list[str]:
    """What the report tells about a roof taken from a view: where it came from, how it was put on the plan, the slope.
    Every one of these is a reading of the drawing, not something the user said: it has to be checkable."""
    reg = found.registration
    w, h = found.size
    moved = f"ruotato di {reg.turns * 90} gradi" if reg.turns else "non ruotato"
    if reg.mirror:
        moved += " e rovesciato (era speculare alla pianta)"
    notes = [f"Tetto: costruito dalla vista della copertura (vista {found.view}, {w:.0f} x {h:.0f} m, disegnata altrove nel foglio): "
             f"messo sulla pianta {moved} e spostato di {reg.shift[0]:.2f}, {reg.shift[1]:.2f} m, facendo combaciare il suo contorno "
             f"con i muri (sovrapposizione {reg.iou * 100:.0f} %, sporto {found.overhang:.2f} m). Controlla nell'anteprima 3D "
             "che sia al suo posto."]
    if found.ambiguous:
        notes.append("Tetto: il contorno combacia con la pianta anche in altre posizioni (forma simmetrica): ho scelto quella "
                     "non ruotata; controlla che i colmi vadano nel verso giusto.")
    if roof.pitch_source == "scritta" and pitch:
        notes.append(f"Tetto: pendenza {roof.pitch_deg:.0f} gradi, quella scritta nel foglio ({pitch[1]}).")
    elif roof.pitch_source == "predefinita":
        notes.append(f"Tetto: nessuna pendenza scritta ne' ricavabile dai prospetti: uso {roof.pitch_deg:.0f} gradi (--pendenza per "
                     "cambiarla).")
    return notes
