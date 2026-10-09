"""Understand a drawing without trusting its layers.

A person who opens an unfamiliar drawing does not read the layer names first: they see a sheet with several views
(a plan, elevations, a section, a site plan), titles under them, rooms with names and areas, thick wall outlines,
door arcs, dimension lines. This module looks for the same things in the geometry and the texts:

* ``scan``            one fast pass over the modelspace into numpy arrays (segments, arcs, hatches, texts, blocks...);
* ``infer_unit``      the unit the numbers are really in (dimension values, door arcs...), whatever the header says;
* ``find_views``      the views of the sheet: groups of drawing separated by empty space, with their titles;
* the type of each view: plan, roof plan, elevation, section, site plan, detail: from the title, else from the shape.

Nothing here builds a 3D model: it says what is where, and with what confidence; the pipeline decides what to do.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field

import numpy as np

from .config import INSUNITS_TO_NAME, UNIT_TO_METERS

STRAIGHT = 1e-9  # a polyline piece with a smaller bulge than this is a straight segment
CELL = 0.5  # m: the size of the cells the sheet is divided in to find the views
LINK = 1.5  # m: pieces of drawing closer than this are one view; empty space wider than this separates two views
SATELLITE_LINK = 4.0  # m: a small piece with no title this close to a view is part of it (a note, a north arrow)
SATELLITE_SHARE = 0.15  # "small": at most this share of the cells of the view it joins
TITLE_REACH = 4.0  # m: a title belongs to the view it is this close to
MIN_VIEW_CELLS = 12  # a group of fewer cells than this (3 m2 of drawing) is a stray mark, not a view
LABEL_CELLS = 60  # a group with a title and no more drawing than this (15 m2) is the frame of the title, not a view
LABEL_REACH = 15.0  # m: the title in such a frame names the view it is this close to
SITE_RATIO_OTHER = 6.0  # ... and a plan this many times bigger than another one is the site even with another title
MIN_PLAN_CELLS = 40  # ... if that other one is at least this big (10 m2 of drawing): not a detail
SITE_RATIO = 2.5  # a plan this many times bigger than another view with the same title is the site around it

UNIT_CHOICES = ("m", "cm", "mm")
UNIT_SURE = 2.0  # with no unit in the header, the analysis decides when its best unit leads by this many votes
DOOR_RADIUS = (0.55, 1.40)  # m: the radius of a swing arc
DIMENSION_RANGE = (0.5, 15.0)  # m: where the median of the dimension values of a plan should lie
TEXT_HEIGHT = (0.05, 0.60)  # m: where the median height of the texts of a drawing should lie
CORE_RANGE = (1.5, 1500.0)  # m: the size of the dense core of the sheet, at least, at most (several drawings fit in it)
OVERRIDE_MARGIN = 1.0  # the header is overruled only by a unit that leads by this many votes

# the words that say what a view is, strongest first (the text is upper-cased and stripped of punctuation)
VIEW_WORDS = (
    ("roof", ("PLANIMETRIA COPERTURA", "PIANTA COPERTURA", "PIANTA TETTO", "COPERTURA", "COPERTURE", "TETTO", "ROOF PLAN", "ROOF")),
    ("elevation", ("PROSPETTO", "PROSPETTI", "ALZATO", "FACCIATA", "ELEVATION", "ELEVAZIONE")),
    ("section", ("SEZIONE", "SEZIONI", "SECTION")),
    ("site", ("PLANIMETRIA GENERALE", "INQUADRAMENTO", "SITE PLAN", "PLANIMETRIA DI INSERIMENTO", "ESTRATTO",
              "PLANIMETRIA CATASTALE", "CATASTALE", "PLANIMETRIA DI ZONA", "PLANIMETRIA LOTTO", "ORTOFOTO")),
    ("detail", ("DETTAGLIO", "PARTICOLARE", "DETAIL")),
    ("plan", ("PIANTA", "PLANIMETRIA", "PIANO TERRA", "PIANO PRIMO", "PIANO SECONDO", "PIANO INTERRATO", "FLOOR PLAN",
              "GROUND FLOOR", "FIRST FLOOR", "STATO DI FATTO", "STATO DI PROGETTO", "PROGETTO", "PLAN")),
)


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
        return self.declared is not None and self.unit != self.declared and self.margin >= OVERRIDE_MARGIN

    @property
    def margin(self) -> float:
        """How far the best unit is ahead of the next one, in votes (0 when the unit was given)."""
        ranked = sorted(self.votes.values(), reverse=True)
        return ranked[0] - ranked[1] if len(ranked) > 1 else 0.0


def _core_extent(soup: Soup) -> float:
    """The size of the dense core of the drawing, in drawing units: the 2nd..98th percentile of the segments."""
    if len(soup.seg) == 0:
        return 0.0
    xs = np.concatenate([soup.seg[:, 0], soup.seg[:, 2]])
    ys = np.concatenate([soup.seg[:, 1], soup.seg[:, 3]])
    return float(max(np.percentile(xs, 98) - np.percentile(xs, 2), np.percentile(ys, 98) - np.percentile(ys, 2)))


def infer_unit(soup: Soup) -> UnitGuess:
    """Vote on the unit of the numbers in the drawing: dimension values, door arcs, the height of the texts, the size of
    the drawing, and the header (the weakest, because it is the one that is wrong most often)."""
    if soup.declared_unit not in UNIT_CHOICES and soup.declared_unit is not None:
        return UnitGuess(soup.declared_unit, soup.declared_unit, {}, [])  # feet and inches: not for us to second-guess
    votes = {u: 0.0 for u in UNIT_CHOICES}
    evidence: list[str] = []

    def vote(median: float, low: float, high: float, weight: float, what: str) -> None:
        fits = [u for u in UNIT_CHOICES if low <= median * UNIT_TO_METERS[u] <= high]
        for u in fits:
            votes[u] += weight / len(fits)
        if fits:
            evidence.append(f"{what} e' {median:g}: {'/'.join(fits)}")

    values = [d["value"] for d in soup.dims]
    if len(values) >= 3:
        vote(statistics.median(values), *DIMENSION_RANGE, 3.0, f"la mediana delle {len(values)} quote")
    heights = [t["height"] for t in soup.texts if t["height"] > 0]
    if len(heights) >= 5:  # the letters of a drawing are 5 cm to 60 cm high, whatever the size of the sheet
        vote(statistics.median(heights), *TEXT_HEIGHT, 2.0, f"l'altezza mediana dei {len(heights)} testi")
    if len(soup.arcs):
        quarter = soup.arcs[(soup.arcs[:, 4] > 70) & (soup.arcs[:, 4] < 110)]
        if len(quarter) >= 3:
            for u in UNIT_CHOICES:
                s = UNIT_TO_METERS[u]
                share = float(np.mean((quarter[:, 2] * s >= DOOR_RADIUS[0]) & (quarter[:, 2] * s <= DOOR_RADIUS[1])))
                votes[u] += 2.0 * share
                if share >= 0.4:
                    evidence.append(f"{share:.0%} dei {len(quarter)} archi di 90 gradi misura quanto una porta in {u}")
    core = _core_extent(soup)
    if core > 0:  # a sheet can hold several drawings side by side: only the absurd is ruled out
        for u in UNIT_CHOICES:
            if CORE_RANGE[0] <= core * UNIT_TO_METERS[u] <= CORE_RANGE[1]:
                votes[u] += 0.5
            else:
                votes[u] -= 3.0  # smaller than a room, or bigger than a town: not in this unit
    if soup.declared_unit in votes:
        votes[soup.declared_unit] += 1.0
    best = max(UNIT_CHOICES, key=lambda u: (votes[u], u == soup.declared_unit))
    return UnitGuess(best, soup.declared_unit, votes, evidence)


# --- the views --------------------------------------------------------------------------------

@dataclass
class View:
    id: int
    bbox: tuple[float, float, float, float]  # drawing units
    cells: int  # how much drawing it holds (0.5 m cells)
    titles: list[str] = field(default_factory=list)
    title_items: list[dict] = field(default_factory=list)
    kind: str = "?"  # plan | roof | elevation | section | site | detail | ?
    kind_from: str = ""  # titolo | forma | ...
    copy_of: int | None = None  # the id of the view this one repeats
    segments: int = 0
    arcs: int = 0
    curves: int = 0
    hatches: int = 0
    doors: int = 0  # swing arcs
    texts: int = 0
    inserts: int = 0
    named: int = 0  # segments, hatches and blocks on layers the names call walls, doors or windows
    matched: str = ""  # an elevation: which facade of the plan it was matched to (written by elevmatch.match_views)

    def size_m(self, scale: float) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        return (x1 - x0) * scale, (y1 - y0) * scale

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        x0, y0, x1, y1 = self.bbox
        return x0 - margin <= x <= x1 + margin and y0 - margin <= y <= y1 + margin


def _sample_points(soup: Soup, step: float) -> np.ndarray:
    """Points along everything that is drawn, about every ``step`` (drawing units), for the grid of the sheet."""
    out = [soup.arcs[:, :2], soup.circles[:, :2]]
    if len(soup.curves):
        out += [(soup.curves[:, :2] + soup.curves[:, 2:]) / 2.0, soup.curves[:, :2], soup.curves[:, 2:]]
    for h in soup.hatches:
        x0, y0, x1, y1 = h["bbox"]
        out.append(np.array([[x0, y0], [x1, y1], [(x0 + x1) / 2, (y0 + y1) / 2], [x0, y1], [x1, y0]]))
    if soup.inserts:
        out.append(np.array([[i["x"], i["y"]] for i in soup.inserts]))
    if len(soup.seg):
        length = np.hypot(soup.seg[:, 2] - soup.seg[:, 0], soup.seg[:, 3] - soup.seg[:, 1])
        n = np.minimum(np.maximum((length / step).astype(int), 1), 400)
        rep = np.repeat(np.arange(len(soup.seg)), n + 1)
        k = np.concatenate([np.arange(c + 1) for c in n]) / np.repeat(n, n + 1)
        s = soup.seg[rep]
        out.append(np.stack([s[:, 0] + (s[:, 2] - s[:, 0]) * k, s[:, 1] + (s[:, 3] - s[:, 1]) * k], axis=1))
    pts = np.concatenate([p.reshape(-1, 2) for p in out if len(p)]) if any(len(p) for p in out) else np.empty((0, 2))
    return pts[np.isfinite(pts).all(axis=1)]


def _title_kind(text: str) -> str | None:
    """What a title says the view is: the text must start with one of the view words (whole words: TETTOIA is no TETTO)."""
    up = " ".join(re.split(r"[^A-Z0-9']+", text.upper())).strip()
    for kind, words in VIEW_WORDS:
        for w in words:
            if up == w or up.startswith(w + " "):
                return kind
    return None


def _title_texts(soup: Soup) -> list[dict]:
    """The texts that name a view: short, and starting with a view word."""
    out = []
    for t in soup.texts:
        if len(t["text"]) <= 60:
            kind = _title_kind(t["text"])
            if kind:
                out.append({**t, "kind": kind})
    return out


def find_views(soup: Soup, scale: float) -> list[View]:
    """The views of the sheet: pieces of drawing separated by empty space, with the title near each and a type."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    cell = CELL / scale
    pts = _sample_points(soup, cell * 0.8)
    if len(pts) == 0:
        return []
    centres = (np.unique(np.floor(pts / cell).astype(np.int64), axis=0) + 0.5) * cell
    tree = cKDTree(centres)

    def label(gap_m: float) -> np.ndarray:
        pairs = tree.query_pairs(gap_m / scale, output_type="ndarray")
        n = len(centres)
        if len(pairs) == 0:
            return np.arange(n)
        g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
        return connected_components(g, directed=False)[1]

    lab = label(LINK)
    titles = _title_texts(soup)
    order_ = np.argsort(lab, kind="stable")
    cuts = np.flatnonzero(np.diff(lab[order_])) + 1
    members = {int(lab[chunk[0]]): chunk for chunk in np.split(order_, cuts)}

    def bbox_of(idx: np.ndarray):
        c = centres[idx]
        (x0, y0), (x1, y1) = c.min(axis=0) - cell / 2, c.max(axis=0) + cell / 2
        return float(x0), float(y0), float(x1), float(y1)

    def titled(idx: np.ndarray) -> bool:
        x0, y0, x1, y1 = bbox_of(idx)
        m = TITLE_REACH / scale
        return any(x0 - m <= t["x"] <= x1 + m and y0 - m <= t["y"] <= y1 + m for t in titles)

    # small untitled pieces next to a bigger group (notes, north arrows, scale bars) belong to it
    coarse = label(SATELLITE_LINK)
    near: dict[int, set[int]] = {}  # coarse group -> the groups (at LINK) it holds
    for fine, wide in zip(lab.tolist(), coarse.tolist()):
        near.setdefault(wide, set()).add(fine)
    for l, idx in sorted(members.items(), key=lambda kv: len(kv[1])):
        if l not in members or len(idx) >= MIN_VIEW_CELLS * 4 or titled(idx):
            continue
        host = [m for m in near[int(coarse[idx[0]])] if m != l and m in members]
        host = [m for m in host if len(members[m]) * SATELLITE_SHARE >= len(idx)]
        if host:
            big = max(host, key=lambda m: len(members[m]))
            members[big] = np.concatenate([members[big], idx])
            del members[l]

    # the frame (or underline) of a title is a small group of its own: its title names the view nearby
    for l, idx in list(members.items()):
        if len(idx) < LABEL_CELLS:
            x0, y0, x1, y1 = bbox_of(idx)
            inside = [t for t in titles if x0 - 0.5 / scale <= t["x"] <= x1 + 0.5 / scale
                      and y0 - 0.5 / scale <= t["y"] <= y1 + 0.5 / scale]
            if inside:
                for t in inside:
                    t["far"] = True
                del members[l]
    views = [View(0, bbox_of(idx), int(len(idx))) for idx in members.values() if len(idx) >= MIN_VIEW_CELLS]
    order = 10.0 / scale  # views in the same row of the sheet (within 10 m) are read left to right
    views.sort(key=lambda v: (-round(v.bbox[3] / order), v.bbox[0]))
    for k, v in enumerate(views, 1):
        v.id = k
    _assign_titles(views, titles, scale)
    for v in views:
        _describe(v, soup, scale)
    _relate(views, scale)
    return views


def _assign_titles(views: list[View], titles: list[dict], scale: float) -> None:
    """Each title goes to the nearest view (inside it, or within reach of its edge)."""
    for t in titles:
        best, best_d = None, (LABEL_REACH if t.get("far") else TITLE_REACH) / scale
        for v in views:
            x0, y0, x1, y1 = v.bbox
            d = math.hypot(max(x0 - t["x"], 0, t["x"] - x1), max(y0 - t["y"], 0, t["y"] - y1))
            if d < best_d or (d == best_d and best is not None and v.cells < best.cells):
                best, best_d = v, d
        if best is not None:
            best.title_items.append(t)


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


def _describe(v: View, soup: Soup, scale: float) -> None:
    x0, y0, x1, y1 = v.bbox
    v.titles = [t["text"] for t in v.title_items]
    seg = soup.seg
    if len(seg):
        mx, my = (seg[:, 0] + seg[:, 2]) / 2, (seg[:, 1] + seg[:, 3]) / 2
        v.segments = int(np.count_nonzero((mx >= x0) & (mx <= x1) & (my >= y0) & (my <= y1)))
    if len(soup.arcs):
        ax, ay, sweep = soup.arcs[:, 0], soup.arcs[:, 1], soup.arcs[:, 4]
        here = (ax >= x0) & (ax <= x1) & (ay >= y0) & (ay <= y1)
        v.arcs = int(here.sum())
        radius = soup.arcs[:, 2] * scale
        v.doors = int(np.count_nonzero(here & (sweep > 70) & (sweep < 110) & (radius >= DOOR_RADIUS[0])
                                       & (radius <= DOOR_RADIUS[1])))
    if len(soup.curves):
        cx, cy = (soup.curves[:, 0] + soup.curves[:, 2]) / 2, (soup.curves[:, 1] + soup.curves[:, 3]) / 2
        v.curves = int(np.count_nonzero((cx >= x0) & (cx <= x1) & (cy >= y0) & (cy <= y1)))
    v.hatches = sum(1 for h in soup.hatches if x0 <= (h["bbox"][0] + h["bbox"][2]) / 2 <= x1
                    and y0 <= (h["bbox"][1] + h["bbox"][3]) / 2 <= y1)
    v.texts = sum(1 for t in soup.texts if x0 <= t["x"] <= x1 and y0 <= t["y"] <= y1)
    v.inserts = sum(1 for i in soup.inserts if x0 <= i["x"] <= x1 and y0 <= i["y"] <= y1)
    if v.title_items:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2  # the title nearest to the view says what it is
        v.kind, v.kind_from = min(v.title_items, key=lambda t: math.hypot(t["x"] - cx, t["y"] - cy))["kind"], "titolo"
    else:
        v.kind, v.kind_from = _kind_from_shape(v, scale), "forma"


def _relate(views: list[View], scale: float) -> None:
    """Views that repeat one another: a plan much bigger than another view that has the same title is the site around
    it; two untitled views of the same size are copies of one drawing."""
    for a in views:
        for b in views:
            if a is b or a.kind != "plan" or b.kind != "plan":
                continue
            same = bool({t["text"].upper() for t in a.title_items} & {t["text"].upper() for t in b.title_items})
            if same and a.cells >= SITE_RATIO * b.cells:
                a.kind, a.kind_from = "site", f"contiene la vista {b.id}, che ha lo stesso titolo"
            elif a.cells >= SITE_RATIO_OTHER * b.cells and b.cells >= MIN_PLAN_CELLS:
                a.kind, a.kind_from = "site", f"molto piu' grande della vista {b.id}: il lotto intorno alla casa"
    for i, a in enumerate(views):
        for b in views[i + 1:]:
            wa, ha = a.size_m(scale)
            wb, hb = b.size_m(scale)
            similar = abs(wa - wb) <= 0.06 * max(wa, wb) and abs(ha - hb) <= 0.06 * max(ha, hb)
            if similar and not a.title_items and not b.title_items \
                    and a.kind in ("plan", "?") and b.kind in ("plan", "?"):
                b.copy_of = a.id
                if b.kind == "?":
                    b.kind, b.kind_from = a.kind, f"copia della vista {a.id}"


def _kind_from_shape(v: View, scale: float) -> str:
    """The type of an untitled view, from what it holds."""
    w, h = v.size_m(scale)
    if v.doors >= 2:
        return "plan"
    aspect = max(w, h) / max(min(w, h), 1e-9)
    if aspect >= 2.2 and v.doors == 0:
        return "elevation"
    return "?"


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
            rows.append({"id": v.id, "tipo": v.kind, "da": v.kind_from, "larghezza_m": round(w, 1),
                         "altezza_m": round(h, 1), "titolo": " | ".join(v.titles), "porte": v.doors,
                         "copia_di": v.copy_of or "", "usata": v is self.plan})
        return rows


def choose_plan(views: list[View], wanted: int | None = None) -> tuple[View | None, str]:
    """The view to convert: the one asked for, else the biggest plan that is neither the site around a building nor
    a copy of another view, else the view with the most door arcs."""
    if wanted is not None:
        for v in views:
            if v.id == wanted:
                return v, f"la vista {v.id} indicata da te"
        return None, f"la vista {wanted} non esiste"
    plans = [v for v in views if v.kind == "plan" and v.copy_of is None]
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
        label = f"{v.id}: {_KIND_IT.get(v.kind, v.kind)}" + (f"  \"{v.titles[0]}\"" if v.titles else "")
        if v.copy_of:
            label += f" (copia di {v.copy_of})"
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
    if cfg.area is None and a.plan is not None and len(a.views) > 1:
        margin = 1.0 / a.unit.scale
        x0, y0, x1, y1 = a.plan.bbox
        change["area"] = (x0 - margin, y0 - margin, x1 + margin, y1 + margin)
        change["area_auto"] = True
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
