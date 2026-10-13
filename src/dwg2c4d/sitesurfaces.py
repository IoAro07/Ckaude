"""The surfaces of the site: the ground round the building, read from the site plan the sheet draws it in.

A sheet often draws the project twice: the plan of the building (the one that is converted) and a site plan that holds a
copy of it among the lot, the paths, the roads. The site plan is drawn with lines, not with fills: a lawn, a path, a
road are regions that open and closed lines bound, and only the hatches (a paving of tiles, the steps) are fills.
The surfaces come from two sources:

1. Fills. Every hatch of the site plan that lies outside the building is a surface; what it is made of comes from the
   layer name, the pattern, the colour, as in the garden (``garden.kind_from_colour``), and from a word written on it.
2. Lines. The long lines of the site plan (not the strokes of a hatch, not furniture, not the small outlines of trees and
   rocks) are laid on the ground as a net: wherever they close a region (gaps narrower than ``2 * CLOSE`` are closed) the
   region is a face. A face of the net that holds a fill is that fill's; the others are surfaces of their own. What the
   face is made of: a word written in it (PRATO, STRADA...), the layer of the lines that bound it, else paving.

The site plan is laid on the plan that was converted with the lines the two have in common (``sitefit``): the place of
the copy of the plan in it. Nothing is guessed when that fails. Every surface is an object of its own (``Sito_Prato_01``)
so that each can be recoloured or moved in Cinema 4D, and every guess is told in the report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import shapely
from ezdxf import path as ezpath
from shapely.geometry import LineString, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from .config import Config
from .garden import EDGE_WIDTH, _fill_kind, _Fill, _ordered, _rgb_of
from .geom import fix, nest_polygons, polygons_of, union
from .mesh import SITE_PREFIX, Mesh, Slab
from .reader import _entity_prims, _Reader, _rough_bounds
from .sitefit import Placement, register
from .sitebands import BandStair, add_band_stairs, find_band_stairs, notes as bands_notes
from .sitewalls import SiteWall, add_site_walls, find_walls, notes as walls_notes
from .terrainmesh import add_draped
from .texts import read_texts

LINE_ENTITIES = frozenset({"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE"})  # what the lines of a site plan are drawn with

# --- the net of lines ---
MIN_PIECE = 0.25  # m: a line shorter than this is a dash or a tick of something else
MIN_CHAIN = 6.0  # m: lines that touch one another and are not this long together, or do not span MIN_SPAN, are an object (a tree, a
MIN_SPAN = 3.0  # m: rock, a chair, a stroke of a hatch), not the edge of a surface
CLOSE = 0.30  # m: lines closer than twice this (an end to another line, two lines) are joined: gaps this narrow are closed
REGION_MARGIN = 1.0  # m: the site plan's frame is grown by this: a region that touches it is the outside, not a face

DASHED_SHARE = 0.6  # a face with this share of its edge drawn dashed is ground, not paving

# --- what is kept ---
MIN_FACE = 4.0  # m2: a smaller face is no surface (a gap between two things)
MIN_FILL = 2.0  # m2: a smaller hatch is a speck (the brick of a pillar, a stroke of a texture)
MIN_WIDTH = EDGE_WIDTH + 0.25  # m: a face thinner than this (2 * area / perimeter) is a kerb or a path edge, not a surface
INSIDE = 0.5  # a face with more than this share inside the building (or in the garden) is a room, or the garden's
MAX_FACES = 80  # more faces than this is a drawing the net does not read: the biggest are kept, the rest said
BUILDING_MARGIN = 0.10  # m: what lies this close to the building is its wall

# --- the ground under it all, when the sheet has no terrain ---
BASE_CLOSE = 4.0  # m: gaps between the surfaces up to twice this are ground too
BASE_TOP = -0.20  # m: the top of the ground the surfaces lie on, below the lowest of them
BASE_NAME = SITE_PREFIX + "Terreno_00"

# --- levels, metres above the platform (the level of the paving round the house) ---
TOP = {"strada": -0.02, "pavimentazione": 0.0, "prato": -0.05, "acqua": -0.15, "terreno": -0.05}
KIND_ORDER = ("strada", "pavimentazione", "prato", "acqua", "terreno")
KIND_NAMES = {"strada": "Strada", "pavimentazione": "Pavimentazione", "prato": "Prato", "acqua": "Acqua", "terreno": "Terreno"}
KIND_FROM_GARDEN = {"lawn": "prato", "water": "acqua", "paving": "pavimentazione", "soil": "terreno"}
ROAD_LAYER = ("strad", "asfalt", "carrabil", "carregg", "viabil")  # a layer called so holds a road
WORD_KINDS = (  # the first word that matches says what the surface it is written on is made of
    ("acqua", r"\b(?:piscin\w*|vasca|acqua|laghett\w*|stagn\w*)\b"),
    ("prato", r"\b(?:prato|prati|giardin\w*|verde|erba|aiuol\w*)\b"),
    ("strada", r"\b(?:strad\w*|via|viale|carrabil\w*|carregg\w*|asfalt\w*|parcheggi\w*|rampa)\b"),
    ("terreno", r"\b(?:terreno|bosco|campo)\b"),
    ("pavimentazione", r"\b(?:portico|porticat\w*|terrazz\w*|marciapied\w*|pavimentazion\w*|piazzal\w*|cortile|patio|vialett\w*)\b"),
)


@dataclass
class SiteFrame:
    """Where the converted plan lies in a site plan of the sheet: the view, and the move that carries the plan onto it."""

    view: object  # the autodetect.View of the site plan
    placement: Placement


@dataclass
class SiteDrawing:
    """What the site plan draws, in metres in the frame of the converted plan."""

    region: Polygon  # the frame of the site plan, grown by REGION_MARGIN
    lines: list[tuple[str, LineString, bool]] = field(default_factory=list)  # (layer, line, dashed): the lines of the big drawings
    fills: list[_Fill] = field(default_factory=list)  # hatches, in the order they are drawn
    words: list[tuple[str, float, float]] = field(default_factory=list)  # (text, x, y)


@dataclass
class Surface:
    """One piece of ground of the site, an object of its own."""

    id: str  # Sito_Prato_01
    kind: str  # strada | pavimentazione | prato | acqua | terreno
    geom: BaseGeometry  # metres, the frame of the plan
    source: str  # campitura | linee
    layer: str
    label: str = ""  # the word written on it, or the pattern of its hatch
    top: float = 0.0  # m above the platform
    keep: bool = True
    why: str = ""  # how the kind was told, for the report


@dataclass
class SiteSurfaces:
    surfaces: list[Surface] = field(default_factory=list)
    walls: list[SiteWall] = field(default_factory=list)  # the curbs and the walls of the site (sitewalls)
    stairs: list[BandStair] = field(default_factory=list)  # the stairs drawn as rows of hatched bands (sitebands)
    view: int = 0  # the number of the site plan the surfaces were read in
    shift: tuple[float, float] = (0.0, 0.0)
    common: float = 0.0  # m of line the plan and the site plan share (the registration)
    left_out: int = 0  # faces left out because there were too many
    base: BaseGeometry = field(default_factory=Polygon)  # the ground under the surfaces, gaps between them closed (flat models only)

    @property
    def empty(self) -> bool:
        return not self.surfaces and not self.walls and not self.stairs

    @property
    def reach(self) -> BaseGeometry:
        """Where the surfaces are: the terrain must cover it."""
        return union([*(s.geom for s in self.surfaces if s.keep), *(w.geom for w in self.walls if w.keep),
                      *(s.geom for s in self.stairs if s.keep)])


# --- finding the site plan ---------------------------------------------------------------------

def locate_site(analysis, scale: float, plan) -> SiteFrame | None:
    """The site plan the converted plan is drawn in, with the move that lays the plan on it: of the views of the kind ``site``
    (the planimetria generale) the one that draws a copy of the plan, found by the lines the two share (``sitefit``), the
    best supported first; a site plan the plan reaches only through another view (a survey that shares the lot's lines with
    it) comes after the ones that draw it. None when the sheet has none, or when the plan cannot be laid on any."""
    if analysis is None or plan is None:
        return None
    found: list[tuple[tuple[int, float], SiteFrame]] = []
    for host in analysis.views:
        if host is plan or host.kind != "site":
            continue
        placement = register(analysis, scale, plan, host, set())
        if placement is not None:
            found.append(((len(placement.views) == 2, placement.common), SiteFrame(host, placement)))
    return max(found, key=lambda f: f[0])[1] if found else None


# --- reading the site plan -----------------------------------------------------------------------

def _to_plan(geom: BaseGeometry, frame: SiteFrame, scale: float) -> BaseGeometry:
    """A shape of the site plan (drawing units) in metres in the frame of the plan."""
    return shapely.transform(geom, lambda xy: frame.placement.to_plan(xy * scale))


def _fill_of(doc, e, order: int, layer: str, rules, dist: float) -> _Fill | None:
    loops = []
    for sub in ezpath.from_hatch(e):
        pts = [(v.x, v.y) for v in sub.flattening(dist)]
        if len(pts) >= 3:
            loops.append(Polygon(pts))
    shape = nest_polygons(loops)
    if shape.is_empty:
        return None
    return _Fill(order, layer, rules.garden_kind(layer), e.dxf.get("pattern_name", "") or "", _rgb_of(doc, e), shape)


def _chains(pieces: list[tuple[str, LineString, bool]]) -> list[tuple[str, LineString, bool]]:
    """The lines that belong to a drawing of something big: pieces that touch one another (within ``CLOSE``) make a chain, and a
    chain that is long enough (``MIN_CHAIN``) and spans enough (``MIN_SPAN``) stays. A road edge drawn in many pieces stays whole,
    a tree, a rock, a chair do not."""
    if not pieces:
        return []
    geoms = [g for _, g, _ in pieces]
    parent = list(range(len(geoms)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first, second = STRtree(geoms).query(geoms, predicate="dwithin", distance=CLOSE)
    for i, j in zip(first.tolist(), second.tolist()):
        parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(geoms)):
        groups.setdefault(find(i), []).append(i)
    out = []
    for members in groups.values():
        boxes = np.array([geoms[i].bounds for i in members])
        span = max(boxes[:, 2].max() - boxes[:, 0].min(), boxes[:, 3].max() - boxes[:, 1].min())
        if sum(geoms[i].length for i in members) >= MIN_CHAIN and span >= MIN_SPAN:
            out.extend(pieces[i] for i in members)
    return out


def _bounds(e) -> list[tuple[float, float]] | None:
    """Points that bound an entity, in drawing units: None for a kind of entity that has none."""
    if e.dxftype() == "POLYLINE":
        return [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices] or None
    return _rough_bounds(e)


def _has_height(e) -> bool:
    """Is the entity drawn at a height (a contour line: a polyline at its elevation)? The terrain reads those, not the surfaces."""
    t = e.dxftype()
    if t == "LWPOLYLINE":
        return bool(e.dxf.get("elevation", 0.0))
    if t == "POLYLINE":
        return any(v.dxf.location.z for v in e.vertices) or bool(e.dxf.get("elevation", (0.0, 0.0, 0.0))[2])
    if t == "LINE":
        return bool(e.dxf.start.z or e.dxf.end.z)
    return False


def _straight(e) -> bool:
    """Is the entity one straight segment (a LINE, a polyline of two points with no bulge)?"""
    t = e.dxftype()
    if t == "LINE":
        return True
    if t == "LWPOLYLINE":
        return len(e) == 2 and not any(p[4] for p in e.get_points("xyseb"))
    if t == "POLYLINE":
        return len(e.vertices) == 2 and not any(v.dxf.get("bulge", 0.0) for v in e.vertices)
    return False


def read_site(doc, cfg: Config, frame: SiteFrame, scale: float) -> SiteDrawing:
    """The lines, the hatches and the words of the site plan, in metres in the frame of the plan. Only what is drawn in the
    modelspace itself counts: the blocks (trees, cars, furniture) are objects, not the edges of a surface. A line on a layer
    of elevations, furniture or texts, a straight dashed line, a dash shorter than ``MIN_PIECE`` is left out."""
    x0, y0, x1, y1 = frame.view.bbox
    rules = cfg.layers
    dist = cfg.arc_tolerance / scale
    reader = _Reader(doc, cfg, dist)
    region = _to_plan(box(x0, y0, x1, y1), frame, scale).buffer(REGION_MARGIN, join_style="mitre")
    drawing = SiteDrawing(region)
    pieces: list[tuple[str, LineString, bool]] = []
    min_units = MIN_PIECE / scale
    for order, e in enumerate(_ordered(doc.modelspace())):
        t = e.dxftype()
        layer = e.dxf.layer
        try:
            if t == "HATCH":
                if not reader.visible(layer):
                    continue
                fill = _fill_of(doc, e, order, layer, rules, dist)
                if fill is not None and box(x0, y0, x1, y1).intersects(fill.geom):
                    fill.geom = _to_plan(fill.geom, frame, scale)
                    drawing.fills.append(fill)
                continue
            if t not in LINE_ENTITIES or not reader.visible(layer):
                continue
            if t == "LINE" and ((e.dxf.start.x - e.dxf.end.x) ** 2 + (e.dxf.start.y - e.dxf.end.y) ** 2) ** 0.5 < min_units:
                continue
            bounds = _bounds(e)
            if bounds is None:
                continue
            xs, ys = zip(*bounds)
            if max(xs) < x0 or min(xs) > x1 or max(ys) < y0 or min(ys) > y1 or _has_height(e):
                continue  # (a line with a height is a contour line: the terrain's business)
            if rules.vetoed(layer) and rules.garden_kind(layer) is None:
                continue
            dashed = reader._is_broken(e, layer)
            if dashed and _straight(e):
                continue  # a dashed straight line is an axis or a hidden edge; a dashed curve or a dashed outline draws a boundary
            for prim in _entity_prims(e, dist):
                geom = prim.geom.boundary if prim.kind == "ring" else prim.geom if prim.kind == "line" else None
                if geom is None or geom.length < min_units:
                    continue
                for line in getattr(geom, "geoms", [geom]):
                    pieces.append((layer, _to_plan(line, frame, scale), dashed))
        except Exception:  # a damaged entity is not worth stopping for
            continue
    drawing.lines = _chains(pieces)
    for text in read_texts(doc, cfg, scale, frame.view.bbox):
        (x, y), = frame.placement.to_plan(np.array([[text.x, text.y]]))
        drawing.words.append((text.text, float(x), float(y)))
    return drawing


# --- the faces of the net ------------------------------------------------------------------------

def faces_of(lines: list[LineString], region: Polygon) -> list[Polygon]:
    """The regions of ``region`` that ``lines`` close: the lines are grown by ``CLOSE`` (so gaps narrower than twice that are
    closed), the free space that is left is cut in regions, the one that touches the edge of ``region`` is the outside and
    goes, the others are grown back by ``CLOSE`` to the line itself."""
    if not lines:
        return []
    net = shapely.union_all(shapely.buffer(np.array(lines, dtype=object), CLOSE, cap_style="square"))
    free = region.difference(net)
    edge = region.exterior.buffer(1e-6)
    out = []
    for poly in polygons_of(free):
        if poly.intersects(edge):
            continue
        out.extend(polygons_of(fix(poly.buffer(CLOSE, join_style="mitre", mitre_limit=2.0))))
    return out


def _thickness(poly: BaseGeometry) -> float:
    """2 * area / perimeter: the width of a strip, whatever its length."""
    return 2.0 * poly.area / poly.length if poly.length else 0.0


def _kind_of_word(text: str) -> str | None:
    low = text.lower()
    for kind, pattern in WORD_KINDS:
        if re.search(pattern, low):
            return kind
    return None


def _word_in(poly: BaseGeometry, words: list[tuple[str, float, float]]) -> tuple[str, str] | None:
    """(kind, the text) of the first word written inside ``poly`` that says what it is made of."""
    for text, x, y in words:
        if poly.contains(shapely.Point(x, y)):
            kind = _kind_of_word(text)
            if kind is not None:
                return kind, text.strip()
    return None


def _kind_of_layer(layer: str, rules) -> str | None:
    if any(t.startswith(ROAD_LAYER) for t in rules.tokens(layer)):
        return "strada"
    return KIND_FROM_GARDEN.get(rules.garden_kind(layer) or "")


def _face_kind(face: Polygon, lines: list[tuple[str, LineString, bool]], words: list, rules) -> tuple[str, str, str]:
    """(kind, layer, why) of a face of the net: a word written in it; else the layer name of the lines that bound it (the
    one with most length along the edge); else ground when dashed lines bound it for the most part (``DASHED_SHARE``: the limit
    of a field, a slope, a contour), else paving, the kind of anything the drawing does not say."""
    word = _word_in(face, words)
    edge = face.boundary.buffer(CLOSE * 1.5)
    by_layer: dict[str, float] = {}
    dashed = total = 0.0
    for layer, ln, broken in lines:
        along = ln.intersection(edge).length
        by_layer[layer] = by_layer.get(layer, 0.0) + along
        total += along
        dashed += along if broken else 0.0
    layer = max(by_layer, key=by_layer.get) if by_layer else ""
    if word is not None:
        return word[0], layer, f"scritta '{word[1]}'"
    for name in sorted(by_layer, key=lambda n: -by_layer[n]):
        kind = _kind_of_layer(name, rules)
        if kind is not None and by_layer[name] > 0:
            return kind, name, f"layer {name}"
    if total > 0 and dashed >= DASHED_SHARE * total:
        return "terreno", layer, "bordata da linee tratteggiate"
    return "pavimentazione", layer, "predefinita"


def _fill_surface_kind(f: _Fill, words: list, rules) -> tuple[str, str] | None:
    """(kind, why) of a hatch, None for a white mask. A word written on it comes first, then the layer, the pattern, the colour."""
    word = _word_in(f.geom, words)
    if word is not None:
        return word[0], f"scritta '{word[1]}'"
    kind = _kind_of_layer(f.layer, rules)
    if kind is not None:
        return kind, f"layer {f.layer}"
    garden = _fill_kind(f)
    if garden is None:
        return None
    how = f"campitura {f.pattern}" if f.pattern and f.pattern.upper() != "SOLID" else "colore della campitura"
    return KIND_FROM_GARDEN[garden], how


def make_surfaces(drawing: SiteDrawing, building: BaseGeometry, taken: BaseGeometry, rules) -> tuple[list[Surface], int]:
    """(the surfaces, how many faces were left out for being too many). ``building``: the footprint of the building (the walls,
    the floors): the ground is laid round it, and what lies in it is a room. ``taken``: what the garden or the fence of the lot
    already cover."""
    zone = union([building.buffer(BUILDING_MARGIN, join_style="mitre"), taken])
    pieces: list[Surface] = []
    fills = sorted(drawing.fills, key=lambda f: f.order)
    covered: BaseGeometry = Polygon()
    for f in fills:  # a hatch drawn later covers what is under it
        kind = _fill_surface_kind(f, drawing.words, rules)
        if kind is None:
            continue
        for piece in polygons_of(f.geom.difference(zone)):
            if piece.area < MIN_FILL:
                continue
            label = f.pattern if f.pattern and f.pattern.upper() != "SOLID" else ""
            pieces.append(Surface("", kind[0], piece, "campitura", f.layer, label, why=kind[1]))
            covered = covered.union(piece)
    for face in faces_of([ln for _, ln, _ in drawing.lines], drawing.region):
        rest = face.difference(zone)
        if rest.area < (1.0 - INSIDE) * face.area:
            continue  # a room of the building, or the garden's own ground
        rest = rest.difference(covered) if not covered.is_empty else rest
        for piece in polygons_of(rest):
            if piece.area < MIN_FACE or _thickness(piece) < MIN_WIDTH:
                continue
            kind, layer, why = _face_kind(piece, drawing.lines, drawing.words, rules)
            pieces.append(Surface("", kind, piece, "linee", layer, "", why=why))
    pieces.sort(key=lambda s: -s.geom.area)
    left = max(len(pieces) - MAX_FACES, 0)
    pieces = pieces[:MAX_FACES]
    count: dict[str, int] = {}
    for s in pieces:
        count[s.kind] = count.get(s.kind, 0) + 1
        s.id = f"{SITE_PREFIX}{KIND_NAMES[s.kind]}_{count[s.kind]:02d}"
        s.top = TOP[s.kind]
    pieces.sort(key=lambda s: (KIND_ORDER.index(s.kind), s.id))
    return pieces, left


def _base(reach: BaseGeometry) -> BaseGeometry:
    """The ground under the surfaces: where they are, the gaps between them (up to ``2 * BASE_CLOSE``) and the holes in them filled."""
    closed = reach.buffer(BASE_CLOSE, join_style="mitre").buffer(-BASE_CLOSE, join_style="mitre")
    return union(Polygon(p.exterior) for p in polygons_of(closed))


def build_site_surfaces(doc, cfg: Config, analysis, plan, scale: float, building: BaseGeometry, taken: BaseGeometry,
                        warnings: list[str]) -> SiteSurfaces | None:
    """The surfaces of the site plan that holds a copy of the converted plan, or None. ``building``: the footprint of the
    building in metres; ``taken``: the ground the garden and the lot fence already cover."""
    frame = locate_site(analysis, scale, plan)
    if frame is None:
        return None
    drawing = read_site(doc, cfg, frame, scale)
    stairs, drawing.fills = find_band_stairs(drawing.fills, building)  # the hatches of steps are no lawn and no paving
    surfaces, left = make_surfaces(drawing, building, taken, cfg.layers)
    walls = find_walls([(layer, ln) for layer, ln, broken in drawing.lines if not broken], building, taken, union(f.geom for f in drawing.fills), drawing.words, cfg)
    if not surfaces and not walls and not stairs:
        return None
    site = SiteSurfaces(surfaces, walls, stairs, frame.view.id, tuple(float(v) for v in frame.placement.shift),
                        frame.placement.common, left)
    site.base = _base(site.reach)
    if surfaces:
        notes(site, warnings)
    walls_notes(walls, warnings)
    bands_notes(stairs, warnings)
    return site


# --- the mesh ------------------------------------------------------------------------------------

def add_site_surfaces(mesh: Mesh, site: SiteSurfaces, cfg: Config, ground=None) -> None:
    """Every surface as a slab of its own: draped on the terrain when there is one, else flat, its top ``top`` above the
    paving round the house and ``cfg.garden_thickness`` thick."""
    for s in site.surfaces:
        if not s.keep or s.geom.is_empty:
            continue
        if ground is None:
            mesh.add_extrusion(s.id, [Slab(s.top - cfg.garden_thickness, s.top, s.geom)])
        else:
            add_draped(mesh, ground, s.id, s.geom, s.top)
    add_site_walls(mesh, site.walls, ground)
    add_band_stairs(mesh, site.stairs, ground)
    if ground is None and not site.base.is_empty:  # a terrain is the ground itself; a flat model has none under the surfaces
        mesh.add_extrusion(BASE_NAME, [Slab(BASE_TOP - cfg.terrain_thickness, BASE_TOP, site.base)], bottom=True)


# --- the report ----------------------------------------------------------------------------------

def summary(site: SiteSurfaces) -> str:
    """One line for the report: how many surfaces of each kind and how much ground they cover."""
    by_kind: dict[str, list[Surface]] = {}
    for s in site.surfaces:
        if s.keep:
            by_kind.setdefault(s.kind, []).append(s)
    parts = [f"{len(v)} {KIND_NAMES[k].lower()} ({sum(s.geom.area for s in v):.0f} m2)" for k, v in sorted(
        by_kind.items(), key=lambda kv: KIND_ORDER.index(kv[0]))]
    return f"{sum(len(v) for v in by_kind.values())} superfici dal disegno del sito (vista {site.view}): " + ", ".join(parts)


def notes(site: SiteSurfaces, warnings: list[str]) -> None:
    """What was guessed, to be checked in the 3D preview."""
    guessed = sum(s.why == "predefinita" for s in site.surfaces)
    warnings.append(
        f"Superfici del sito: dalla vista {site.view} del foglio (la planimetria generale che disegna una copia della pianta) "
        f"ho messo la pianta sulla planimetria facendo combaciare {site.common:.0f} m di linee e ho costruito "
        f"{len(site.surfaces)} superfici dalle campiture e dalle regioni chiuse dalle linee lunghe (spostamento di "
        f"{site.shift[0]:.1f}, {site.shift[1]:.1f} m): ognuna e' un oggetto a se' (Sito_...) che in Cinema 4D puoi ricolorare o "
        "togliere. Controlla nell'anteprima 3D che siano dove devono stare.")
    if guessed:
        warnings.append(f"Superfici del sito: di {guessed} di esse il disegno non dice di cosa sono fatte (nessuna scritta, "
                        "nessuna campitura, nessun layer che lo dica): le ho trattate come pavimentazione. Per cambiarle usa "
                        "il materiale in Cinema 4D o --no-superfici-sito per non averle.")
    if site.left_out:
        warnings.append(f"Superfici del sito: le regioni chiuse dalle linee sono troppe: ho tenuto le {MAX_FACES} piu' grandi "
                        f"e lasciato fuori {site.left_out}.")
