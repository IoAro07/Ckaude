"""The garden: the ground outside the building, a dug pool, and plants / outdoor furniture from blocks.

Ground. Every hatch outside the walls (and the closed outlines on a layer that says what they are: Prato,
Pavimentazione, Piscina...) is a piece of ground. What it is made of comes from, in this order: the layer name,
the hatch pattern, the hatch colour (green = lawn, blue = water, anything else = paving). The pieces are put in
drawing order (what is drawn later covers what is under it), so the result never overlaps; thin strips (kerbs,
low walls) are kept apart, the gaps between the pieces are filled with soil, and the water becomes a pool: a
closed shell dug under the paving, with the water in it.

Plants and furniture. Blocks outside the walls named like a hedge (Siepe), a tree (Albero), a shrub
(Cespuglio) or a piece of garden furniture (Sdraio, Tavolo...), or inserted on a layer of the garden, become simple
stand-ins sized by the block's outline (a box for a hedge, a trunk and a crown for a tree, an ellipsoid for a
shrub): each one is a group of its own with its axis at the centre of its base, to be replaced with a real model.
Their heights come from the elevation when it draws them, else from the defaults, and can be corrected in
``NAME_giardino.csv`` (see ``garden_table``).
"""

from __future__ import annotations

import colorsys
import math
import statistics
from dataclasses import dataclass, field

from ezdxf import colors as ezcolors
from ezdxf import path as ezpath
from ezdxf.math import Vec3
from shapely import affinity
from shapely.geometry import Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from .config import Config
from .geom import fix, nest_polygons, oriented_rect, polygons_of, union
from .mesh import Mesh, Slab
from .reader import LINE_TYPES, _entity_prims, _flatten, layer_used

GAP = 3.0  # m: a piece of ground this close to the garden found so far belongs to it
OBJECT_GAP = 10.0  # m: plants and furniture this far from it still do
MIN_SURFACE = 0.25  # m2: smaller pieces of ground are specks
EDGE_WIDTH = 0.35  # m: a strip of ground thinner than this is a kerb or a low wall, not a surface
CLOSE = 0.4  # m: gaps this narrow between pieces of ground are filled with soil
SIMPLIFY = 0.01  # m: the outlines of the ground are simplified to this tolerance
SLIVER = 0.03  # m: what is thinner than twice this, left by cutting one piece out of another, is dropped
POOL_MIN = 1.0  # m2: water smaller than this is not a pool
HOLE_MAX = 40.0  # m2: a bigger hole in the ground is a courtyard, not a table or a bush
COPING_MAX = 0.8  # m: the bare edge between the water and the paving is the coping of the pool up to this width

KINDS = ("paving", "edge", "lawn", "soil")  # the ground, bottom to top of the mesh (water is the pool)
GROUND_NAMES = {"paving": "Pavimentazione", "edge": "Bordi", "lawn": "Prato", "soil": "Terreno"}
KIND_NAMES = {"tree": "albero", "hedge": "siepe", "shrub": "cespuglio", "furniture": "arredo"}
NAME_KINDS = {"albero": "tree", "siepe": "hedge", "cespuglio": "shrub", "arredo": "furniture"}
PREFIX = {"tree": "A", "hedge": "S", "shrub": "C", "furniture": "E"}
PARTS = {"tree": ("Tronco", "Chioma"), "hedge": ("Siepe",), "shrub": ("Cespuglio",), "furniture": ("Arredo",)}
PART_NAMES = ("Tronco", "Chioma", "Siepe", "Cespuglio", "Arredo")  # first word of the mesh groups of an object

_BLOCK_KINDS = (
    ("hedge", ("siep", "hedge")),
    ("tree", ("alber", "tree", "pino", "cipress", "palma", "quercia", "ulivo", "olivo", "betulla", "faggio", "abete")),
    ("shrub", ("cespugl", "shrub", "bush", "arbust", "aiuol", "fiori", "flower", "vaso")),
    ("furniture", ("sdraio", "lettin", "lounger", "sunbed", "chaise", "ombrellon", "umbrella", "tavolo", "table",
                   "sedia", "chair", "panca", "panchin", "bench", "barbecue", "bbq", "dondolo", "amaca", "gazebo",
                   "pergol")),
)
_FURNITURE_HEIGHT = (("sdraio", 0.35), ("lettin", 0.35), ("lounger", 0.35), ("sunbed", 0.35), ("chaise", 0.40),
                     ("ombrellon", 2.30), ("umbrella", 2.30), ("tavolino", 0.45), ("tavolo", 0.75), ("table", 0.75),
                     ("sedia", 0.85), ("chair", 0.85), ("panca", 0.45), ("panchin", 0.45), ("bench", 0.45),
                     ("barbecue", 0.90), ("bbq", 0.90), ("gazebo", 2.60), ("pergol", 2.60))
_PATTERN_KINDS = (("grass", "lawn"), ("erba", "lawn"), ("prato", "lawn"), ("turf", "lawn"), ("water", "water"),
                  ("acqua", "water"), ("earth", "soil"), ("terra", "soil"))
_ELEVATION_WORDS = ("prospett", "sezion", "elevat")


# --- the model --------------------------------------------------------------------------------

@dataclass
class GardenObject:
    id: str
    kind: str  # tree | hedge | shrub | furniture
    block: str
    layer: str
    cx: float  # metres, CAD coordinates (before the model is moved to the origin)
    cy: float
    angle: float  # radians: the direction of the length axis (the block's own x)
    length: float
    width: float
    height: float
    height_src: str = "predefinita"  # predefinita | prospetto | tabella
    z0: float = 0.0  # the ground under it
    keep: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def axis(self) -> tuple[float, float]:
        return math.cos(self.angle), math.sin(self.angle)


@dataclass
class Garden:
    surfaces: dict[str, BaseGeometry] = field(default_factory=dict)  # KINDS -> ground (pools cut out)
    pools: list[BaseGeometry] = field(default_factory=list)  # the water outline of each pool
    shells: list[BaseGeometry] = field(default_factory=list)  # the outer outline of the shell of each pool (coping included)
    objects: list[GardenObject] = field(default_factory=list)
    window: tuple[float, float, float, float] | None = None  # the garden found, metres

    @property
    def empty(self) -> bool:
        return not self.objects and not self.pools and all(g.is_empty for g in self.surfaces.values())

    def area(self, kind: str) -> float:
        return self.surfaces[kind].area if kind in self.surfaces else 0.0


@dataclass
class _Fill:
    order: int
    layer: str
    layer_kind: str | None
    pattern: str
    rgb: tuple[int, int, int] | None
    geom: BaseGeometry  # drawing units, then metres


@dataclass
class _Block:
    order: int
    layer: str
    layer_kind: str | None
    name: str
    kind: str | None
    cx: float
    cy: float
    angle: float
    length: float
    width: float
    top: float = 0.0  # world y of the top and bottom of the block: its height when it is drawn in an elevation
    bottom: float = 0.0


# --- classification ---------------------------------------------------------------------------

def _rgb_of(doc, e) -> tuple[int, int, int] | None:
    """The colour a hatch is drawn with (its own, else its layer's); ACI 7 is the black/white of the screen."""
    try:
        true = e.dxf.get("true_color", None)
        if true is not None:
            return tuple(ezcolors.int2rgb(true))
        aci = e.dxf.get("color", 256)
        if aci in (0, 256):
            layer = doc.layers.get(e.dxf.layer)
            if layer.dxf.get("true_color", None) is not None:
                return tuple(ezcolors.int2rgb(layer.dxf.true_color))
            aci = abs(layer.dxf.get("color", 7))
        if aci == 7:
            return (0, 0, 0)
        return tuple(ezcolors.aci2rgb(aci))
    except Exception:
        return None


def kind_from_colour(rgb: tuple[int, int, int] | None) -> str | None:
    """lawn | water | paving, from a fill colour; None for white (a mask, not a material)."""
    if rgb is None:
        return "paving"
    h, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))
    hue = h * 360.0
    if v >= 0.98 and s <= 0.04:
        return None
    if s >= 0.20 and 65.0 <= hue <= 170.0:
        return "lawn"
    if s >= 0.22 and v >= 0.45 and 175.0 <= hue <= 255.0:
        return "water"
    return "paving"


def _fill_kind(f: _Fill) -> str | None:
    if f.layer_kind in ("lawn", "paving", "water"):
        return f.layer_kind
    pattern = f.pattern.lower()
    for word, kind in _PATTERN_KINDS:
        if word in pattern:
            return kind
    return kind_from_colour(f.rgb)


def block_kind(name: str, layer_kind: str | None, in_elevation: bool = False) -> str | None:
    """tree | hedge | shrub | furniture, from the name of a block, else from the layer it is on. A block that
    says it is for an elevation ("Albero Prospetto") is no plant of the plan; in an elevation it is one."""
    low = name.lower()
    if not in_elevation and any(w in low for w in _ELEVATION_WORDS):
        return None
    for kind, words in _BLOCK_KINDS:
        if any(w in low for w in words):
            return kind
    return {"plants": "shrub", "furniture": "furniture", "garden": "furniture"}.get(layer_kind or "")


def default_height(kind: str, name: str, cfg: Config) -> float:
    if kind == "tree":
        return cfg.tree_height
    if kind == "hedge":
        return cfg.hedge_height
    if kind == "shrub":
        return cfg.shrub_height
    low = name.lower()
    return next((h for w, h in _FURNITURE_HEIGHT if w in low), cfg.furniture_height)


# --- reading ----------------------------------------------------------------------------------

def _inside(boxes, x: float, y: float) -> bool:
    return any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in boxes)


def _skippable(cfg: Config, layer: str, layer_kind: str | None) -> bool:
    """Elevations, sections, texts, furniture indoors, lights...: nothing of the garden is on them."""
    if layer_kind is not None:
        return False
    rules = cfg.layers
    return rules.vetoed(layer) or any(t.startswith(rules._NOT_GARDEN) for t in rules.tokens(layer))


def _block_box(doc, name: str, dist: float, cache: dict):
    """The outline of a block in its own coordinates (x0, y0, x1, y1), the base point at the origin."""
    if name in cache:
        return cache[name]
    outline = None
    try:
        block = doc.blocks.get(name)
    except Exception:
        block = None
    if block is not None:
        bounds = []
        for e in _flatten(list(block), lambda: None):
            try:
                bounds.extend(p.geom.bounds for p in _entity_prims(e, dist) if not p.geom.is_empty)
            except Exception:
                continue
        if bounds:
            x0s, y0s, x1s, y1s = zip(*bounds)
            base = block.block.dxf.get("base_point", Vec3())
            bx, by = base.x, base.y
            outline = (min(x0s) - bx, min(y0s) - by, max(x1s) - bx, max(y1s) - by)
    cache[name] = outline
    return outline


def _place(e, outline) -> tuple[float, float, float, float, float, float, float]:
    """Where a block reference puts the outline of its block: (centre x, centre y, angle, length, width,
    top y, bottom y), the length being along the block's own x axis."""
    m = e.matrix44()
    x0, y0, x1, y1 = outline
    c = [m.transform(Vec3(x, y, 0.0)) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    ux, uy = c[1].x - c[0].x, c[1].y - c[0].y
    length, width = math.hypot(ux, uy), math.hypot(c[3].x - c[0].x, c[3].y - c[0].y)
    angle = math.atan2(uy, ux) if length > 1e-12 else 0.0
    if angle > math.pi / 2 + 1e-9:  # one orientation per box: the axis points to the right (or up)
        angle -= math.pi
    elif angle <= -math.pi / 2 + 1e-9:
        angle += math.pi
    ys = [p.y for p in c]
    return (sum(p.x for p in c) / 4.0, sum(p.y for p in c) / 4.0, angle, length, width, max(ys), min(ys))


def _scan(doc, cfg: Config, dist: float, zones, skip, user_window):
    """Hatches / outlines and blocks of the garden, in drawing units: (fills, blocks, fills drawn in an
    elevation, blocks drawn in an elevation)."""
    rules = cfg.layers
    fills: list[_Fill] = []
    blocks: list[_Block] = []
    e_fills: list[_Fill] = []
    e_blocks: list[_Block] = []
    rings: dict[str, tuple[int, list[Polygon], str]] = {}
    cache: dict = {}
    window = box(*user_window) if user_window else None
    for order, e in enumerate(doc.modelspace()):
        t = e.dxftype()
        layer = e.dxf.layer
        if not layer_used(doc, cfg, layer) or rules.classify_layer(layer) is not None:
            continue  # off, or a layer of the plan (walls, doors, floors...)
        lk = rules.garden_kind(layer)
        if _skippable(cfg, layer, lk):
            continue
        try:
            if t == "HATCH":
                loops = []
                for sub in ezpath.from_hatch(e):
                    pts = [(v.x, v.y) for v in sub.flattening(dist)]
                    if len(pts) >= 3:
                        loops.append(Polygon(pts))
                shape = nest_polygons(loops)
                if shape.is_empty:
                    continue
                fill = _Fill(order, layer, lk, e.dxf.get("pattern_name", "") or "", _rgb_of(doc, e), shape)
            elif t == "INSERT":
                in_elevation = _inside(zones, e.dxf.insert.x, e.dxf.insert.y)
                kind = block_kind(e.dxf.name, lk, in_elevation)
                if kind is None:
                    continue
                outline = _block_box(doc, e.dxf.name, dist, cache)
                if outline is None:
                    continue
                cx, cy, angle, length, width, top, bottom = _place(e, outline)
                if window is not None and not window.intersects(Point(cx, cy)):
                    continue
                blk = _Block(order, layer, lk, e.dxf.name, kind, cx, cy, angle, length, width, top, bottom)
                if in_elevation:
                    e_blocks.append(blk)
                elif not _inside(skip, cx, cy):
                    blocks.append(blk)
                continue
            elif t in LINE_TYPES and lk in ("lawn", "paving", "water"):
                polys = [p.geom for p in _entity_prims(e, dist) if p.kind == "ring"]
                if polys:
                    rings.setdefault(layer, (order, [], lk))[1].extend(polys)
                continue
            else:
                continue
        except Exception:
            continue
        x0, y0, x1, y1 = fill.geom.bounds
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        if window is not None and not window.intersects(box(*fill.geom.bounds)):
            continue
        if _inside(zones, cx, cy):
            e_fills.append(fill)
        elif not _inside(skip, cx, cy):
            fills.append(fill)
    for layer, (order, polys, lk) in rings.items():
        shape = nest_polygons(polys)
        if not shape.is_empty and (window is None or window.intersects(box(*shape.bounds))):
            fills.append(_Fill(order, layer, lk, "", None, shape))
    return fills, blocks, e_fills, e_blocks


def _scaled(geom: BaseGeometry, scale: float) -> BaseGeometry:
    return affinity.scale(geom, scale, scale, origin=(0, 0))


# --- ground -----------------------------------------------------------------------------------

def _thin(poly: Polygon) -> bool:
    """A strip: nothing of it is further than half of ``EDGE_WIDTH`` from its outline."""
    return poly.buffer(-EDGE_WIDTH / 2.0, join_style="mitre").is_empty


def _tidy(geom: BaseGeometry) -> BaseGeometry:
    """Drop slivers and specks: what the cutting of one piece out of another leaves behind."""
    if geom.is_empty:
        return geom
    # (2 mm short of the way back: two parts that only touched at a point stay apart, no pinched vertical edges)
    geom = fix(geom).buffer(-SLIVER, join_style="mitre").buffer(SLIVER - 0.002, join_style="mitre")
    # a centimetre of tolerance: the wiggles of a hatch outline (a leaf pattern) make necks that the mesh welds shut
    return union(p.simplify(SIMPLIFY, preserve_topology=True) for p in polygons_of(geom) if p.area >= MIN_SURFACE)


def _find_window(fills: list[_Fill], footprint: BaseGeometry, user_window) -> tuple[list[_Fill], BaseGeometry]:
    """The fills that belong to the garden: those that touch the building or the garden found so far (within
    GAP). With a window given by the user: those that touch it."""
    if user_window:
        return list(fills), box(*user_window)
    window = box(*footprint.bounds)
    pending, kept = list(fills), []
    grown = True
    while grown:
        grown = False
        for f in list(pending):
            if box(*f.geom.bounds).distance(window) <= GAP:
                x0, y0, x1, y1 = f.geom.bounds
                wx0, wy0, wx1, wy1 = window.bounds
                window = box(min(x0, wx0), min(y0, wy0), max(x1, wx1), max(y1, wy1))
                kept.append(f)
                pending.remove(f)
                grown = True
    return kept, window


def pool_shell(pool: BaseGeometry, cover: dict[str, BaseGeometry], cfg: Config) -> BaseGeometry:
    """The outline of the shell of a pool: the water grown by the wall, or, when the paving around it has a hole a
    little bigger than the water (the coping the drawing leaves bare), that hole."""
    shell = pool.buffer(cfg.pool_wall)
    ground = union(cover[k] for k in KINDS)
    holes = [Polygon(r) for poly in polygons_of(ground) for r in poly.interiors]
    reach = pool.buffer(COPING_MAX)
    near = [h for h in holes if h.buffer(0.02).contains(pool) and reach.contains(h)]
    return union([shell, min(near, key=lambda h: h.area)]) if near else shell


def build_ground(fills: list[_Fill], footprint: BaseGeometry, cfg: Config) \
        -> tuple[dict[str, BaseGeometry], list[BaseGeometry], list[BaseGeometry]]:
    """The ground as non-overlapping pieces by kind (``KINDS``), the water outlines of the pools and the outer
    outlines of their shells."""
    cover: dict[str, BaseGeometry] = {k: Polygon() for k in (*KINDS, "water")}
    for f in sorted(fills, key=lambda f: f.order):
        kind = _fill_kind(f)
        if kind is None:
            continue
        for poly in polygons_of(f.geom.difference(footprint)):
            k = "edge" if kind != "water" and _thin(poly) else kind
            for other in cover:
                if other != k and not cover[other].is_empty and cover[other].intersects(poly):
                    cover[other] = cover[other].difference(poly)
            cover[k] = cover[k].union(poly)
    pools = [p for p in polygons_of(_tidy(cover.pop("water"))) if p.area >= POOL_MIN]
    outlines = [pool_shell(p, cover, cfg) for p in pools]
    shells = union(outlines)
    ground = {k: _tidy(g.difference(shells)) if not shells.is_empty else _tidy(g) for k, g in cover.items()}
    _fill_holes(ground, shells, footprint)
    covered = union([*ground.values(), shells])
    if not covered.is_empty:  # soil in the gaps between the pieces (and nowhere else)
        gaps = covered.buffer(CLOSE, join_style="mitre").buffer(-CLOSE, join_style="mitre") \
            .difference(covered).difference(footprint)
        ground["soil"] = _tidy(union([ground["soil"], gaps]))
    return ground, pools, outlines


def _fill_holes(ground: dict[str, BaseGeometry], shells: BaseGeometry, footprint: BaseGeometry) -> None:
    """A hole in the ground that is not the building or a pool is ground the hatch leaves bare (where a bush, a
    table, a pergola stands): it gets what surrounds it, the kind it shares most of its outline with."""
    solid = union([*ground.values(), shells])
    for poly in polygons_of(solid):
        for ring in poly.interiors:
            hole = Polygon(ring).difference(footprint).difference(solid)  # what is left bare, not what is in it
            for piece in polygons_of(hole):
                if not 0.002 <= piece.area <= HOLE_MAX:
                    continue
                edge = piece.buffer(0.05)
                best = max(KINDS, key=lambda k: ground[k].boundary.intersection(edge).length)
                if ground[best].boundary.intersection(edge).length > 0:
                    ground[best] = _tidy(ground[best].union(piece))


# --- the objects ------------------------------------------------------------------------------

def _stack_heights(boxes: list[tuple[float, float, float, float]]) -> list[float]:
    """Heights of what an elevation draws in pieces one above the other (a hedge made of two rows of leaves):
    boxes that share their width and touch vertically are one thing, as tall as all of them together."""
    parent = list(range(len(boxes)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (ax0, ay0, ax1, ay1) in enumerate(boxes):
        for j in range(i + 1, len(boxes)):
            bx0, by0, bx1, by1 = boxes[j]
            across = min(ax1, bx1) - max(ax0, bx0)
            if across >= 0.6 * min(ax1 - ax0, bx1 - bx0) and ay0 <= by1 + 0.08 and by0 <= ay1 + 0.08:
                parent[find(i)] = find(j)
    groups: dict[int, list[float]] = {}
    for i, b in enumerate(boxes):
        groups.setdefault(find(i), []).extend((b[1], b[3]))
    return [max(ys) - min(ys) for ys in groups.values()]


def _height_from_elevation(objects: list[GardenObject], e_blocks: list[_Block], e_fills: list[_Fill]) -> None:
    """Heights the elevation draws: a plant or a piece of furniture drawn as a block, a hedge or a shrub also as
    a hatch as wide as the blocks of the plan (green, or on a layer of the garden). Pieces drawn one above the
    other (two rows of hedge) count together; the tallest thing found is taken, as what is hidden or cut can
    only make it look lower."""
    for kind in {o.kind for o in objects}:
        boxes = [(b.cx - b.length / 2.0, b.bottom, b.cx + b.length / 2.0, b.top) for b in e_blocks
                 if b.kind == kind and b.top - b.bottom > 0.05]
        if kind in ("hedge", "shrub"):
            size = statistics.median(o.length for o in objects if o.kind == kind)
            for f in e_fills:
                x0, y0, x1, y1 = f.geom.bounds
                if (f.layer_kind in ("plants", "garden", "lawn") or kind_from_colour(f.rgb) == "lawn") \
                        and abs((x1 - x0) - size) <= 0.08 * size and 0.1 <= y1 - y0 <= 6.0:
                    boxes.append((x0, y0, x1, y1))
        heights = [h for h in _stack_heights(boxes) if 0.1 <= h <= 30.0]
        if not heights:
            continue
        for o in objects:
            if o.kind == kind and o.height_src == "predefinita":
                o.height, o.height_src = max(heights), "prospetto"


def _objects(blocks: list[_Block], e_blocks, e_fills, footprint: BaseGeometry, window: BaseGeometry,
             ground: dict[str, BaseGeometry], cfg: Config) -> list[GardenObject]:
    reach = box(*window.bounds).buffer(OBJECT_GAP)
    inside = footprint.buffer(0.02)
    objs: list[GardenObject] = []
    for b in blocks:
        if b.kind is None or inside.contains(Point(b.cx, b.cy)) or not reach.contains(Point(b.cx, b.cy)):
            continue
        length, width = max(b.length, 0.05), max(b.width, 0.05)
        objs.append(GardenObject("", b.kind, b.name, b.layer, b.cx, b.cy, b.angle, length, width,
                                 default_height(b.kind, b.name, cfg)))
    objs.sort(key=lambda o: (-round(o.cy / 0.5), o.cx))
    count: dict[str, int] = {}
    for o in objs:
        count[o.kind] = count.get(o.kind, 0) + 1
        o.id = f"{PREFIX[o.kind]}{count[o.kind]:02d}"
    _height_from_elevation(objs, e_blocks, e_fills)
    place_on_ground(objs, ground, cfg)
    return objs


def place_on_ground(objects: list[GardenObject], ground: dict[str, BaseGeometry], cfg: Config) -> None:
    soft = union([ground.get("lawn", Polygon()), ground.get("soil", Polygon())])
    for o in objects:
        o.z0 = -cfg.garden_lawn_drop if not soft.is_empty and soft.contains(Point(o.cx, o.cy)) else 0.0


# --- building the garden ----------------------------------------------------------------------

def build_garden(doc, cfg: Config, unit_scale: float, footprint: BaseGeometry, zones: list[tuple],
                 warnings: list[str]) -> Garden | None:
    """Read the garden of the drawing. ``zones``: the elevations (drawing units), whose hatches and blocks are
    not the garden. ``footprint``: the building, walls included, in metres."""
    dist = cfg.arc_tolerance / unit_scale
    user_window = cfg.garden_area or (cfg.area if cfg.area and not cfg.area_auto else None)
    fills, blocks, e_fills, e_blocks = _scan(doc, cfg, dist, [tuple(z[:4]) for z in zones],
                                             [tuple(b) for b in cfg.garden_exclude], user_window)
    for f in (*fills, *e_fills):
        f.geom = _scaled(f.geom, unit_scale)
    for b in (*blocks, *e_blocks):
        b.cx, b.cy, b.length, b.width, b.top, b.bottom = (v * unit_scale for v in
                                                            (b.cx, b.cy, b.length, b.width, b.top, b.bottom))
    wanted = tuple(v * unit_scale for v in user_window) if user_window else None
    fills = [f for f in fills if f.geom.difference(footprint).area >= MIN_SURFACE]
    fills, window = _find_window(fills, footprint, wanted)
    ground, pools, shells = build_ground(fills, footprint, cfg)
    garden = Garden(ground, pools, shells, [], window.bounds)
    garden.objects = _objects(blocks, e_blocks, e_fills, footprint, window, ground, cfg)
    if garden.empty:
        return None
    return garden


# --- mesh -------------------------------------------------------------------------------------

def _box(o: GardenObject, z0: float, z1: float) -> Slab:
    ux, uy = o.axis
    return Slab(z0, z1, oriented_rect(o.cx, o.cy, ux, uy, o.length, -o.width / 2.0, o.width / 2.0))


def add_garden(mesh: Mesh, garden: Garden, cfg: Config) -> None:
    """The ground slabs, the pools and the stand-ins of the plants and the furniture."""
    th = cfg.garden_thickness
    for kind in KINDS:
        geom = garden.surfaces.get(kind)
        if geom is not None and not geom.is_empty:
            top = -cfg.garden_lawn_drop if kind in ("lawn", "soil") else 0.0
            mesh.add_extrusion(GROUND_NAMES[kind], [Slab(top - th, top, geom)])
    for pool, shell in zip(garden.pools, garden.shells):
        mesh.add_extrusion("Vasca", [Slab(-cfg.pool_depth - cfg.pool_wall, -cfg.pool_depth, shell),
                                     Slab(-cfg.pool_depth, 0.0, shell.difference(pool))])
        mesh.add_extrusion("Acqua", [Slab(-cfg.pool_depth, -cfg.pool_water_drop, pool)])
    for o in garden.objects:
        if o.keep:
            add_object(mesh, o)


def add_object(mesh: Mesh, o: GardenObject) -> None:
    z0, h = o.z0, o.height
    if o.kind == "tree":
        radius = min(0.15, max(0.06, 0.03 * h))
        trunk_top = z0 + 0.5 * h
        mesh.add_extrusion(f"Tronco_{o.id}", [Slab(z0, trunk_top, Point(o.cx, o.cy).buffer(radius, quad_segs=3))])
        mesh.add_ellipsoid(f"Chioma_{o.id}", (o.cx, o.cy, z0 + 0.62 * h),
                           (o.length / 2.0, o.width / 2.0, 0.38 * h), o.angle)
    elif o.kind == "shrub":
        mesh.add_ellipsoid(f"Cespuglio_{o.id}", (o.cx, o.cy, z0 + h / 2.0),
                           (o.length / 2.0, o.width / 2.0, h / 2.0), o.angle)
    elif o.kind == "hedge":
        mesh.add_extrusion(f"Siepe_{o.id}", [_box(o, z0, z0 + h)])
    else:
        mesh.add_extrusion(f"Arredo_{o.id}", [_box(o, z0, z0 + h)])


def summary(garden: Garden) -> str:
    """One line for the report: the ground and the objects."""
    parts = []
    for kind, label in (("paving", "pavimentazione"), ("lawn", "prato"), ("soil", "terreno"), ("edge", "bordi")):
        if garden.area(kind) >= 0.1:
            parts.append(f"{label} {garden.area(kind):.0f} m2")
    if garden.pools:
        parts.append(f"{len(garden.pools)} piscina/e {sum(p.area for p in garden.pools):.0f} m2")
    names = {"tree": "alberi", "hedge": "siepi", "shrub": "cespugli", "furniture": "arredi"}
    for kind, label in names.items():
        n = sum(1 for o in garden.objects if o.kind == kind and o.keep)
        if n:
            parts.append(f"{n} {label}")
    return ", ".join(parts)
