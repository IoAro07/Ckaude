"""Read a DXF document into categorised, metre-scaled 2D items."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np
from ezdxf import path as ezpath
from ezdxf.document import Drawing
from shapely import affinity
from shapely.geometry import LineString, Polygon, box
from shapely.geometry.base import BaseGeometry

from .config import INSUNITS_TO_NAME, UNIT_TO_METERS, Config, floor_of
from .geom import fix, nest_polygons

LINE_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}
FILL_TYPES = {"HATCH", "SOLID", "TRACE"}  # filled shapes: an elevation's glass is often a hatch on an unnamed layer
MAX_BLOCK_DEPTH = 8
SWING_RADIUS = (0.55, 1.40)  # m: the arc a door leaf sweeps (a quarter circle, drawn on any layer)
SWING_SPAN = (70.0, 110.0)  # degrees
THIN_MIN_LENGTH = 0.6  # m: shorter straight pieces are no part of a glazing strip (glazing.py looks at the longer ones)
THIN_MAX_VERTICES = 40  # a polyline with more vertices is a contour or a curve, not a pane
THIN_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE"}


@dataclass
class Prim:
    """One drawing primitive. ``kind``: line (open), ring (closed outline), fill (hatch/solid)."""

    geom: BaseGeometry
    kind: str
    meta: dict = field(default_factory=dict)  # e.g. {"arc": (cx, cy, r, span_deg, (mid_x, mid_y))}


@dataclass
class Item:
    """A top-level entity, or a whole block reference (one symbol), with its category."""

    layer: str
    block: str | None
    category: str
    prims: list[Prim] = field(default_factory=list)


@dataclass
class ReadResult:
    items: list[Item]
    unit: str
    unit_scale: float
    unit_guessed: bool
    skipped_entities: int
    warnings: list[str]
    thin: np.ndarray = field(default_factory=lambda: np.zeros((0, 4)))  # x0, y0, x1, y1 (m): long straight pieces of any layer


def guess_unit(span: float) -> str:
    """Guess drawing units from the largest plan dimension when the file has none."""
    if span < 300:
        return "m"
    if span < 8000:
        return "cm"
    return "mm"


def storeys(doc: Drawing) -> list[int]:
    """The storeys the layer names mention (P1_, pianta2...), sorted."""
    cached = getattr(doc, "_dwg2c4d_storeys", None)
    if cached is None:
        cached = sorted({f for lay in doc.layers if (f := floor_of(lay.dxf.name)) is not None})
        doc._dwg2c4d_storeys = cached
    return cached


def layer_used(doc: Drawing, cfg: Config, layer: str) -> bool:
    """Is the layer read? Not when it is off/frozen (unless ``include_hidden``) or belongs to another storey
    than the one chosen (``cfg.floor``, default the lowest one named in the drawing)."""
    floor = floor_of(layer)
    if floor is not None:
        found = storeys(doc)
        wanted = cfg.floor if cfg.floor is not None else (found[0] if found else None)
        if wanted is not None and floor != wanted:
            return False
    if cfg.include_hidden or not doc.layers.has_entry(layer):
        return True
    entry = doc.layers.get(layer)
    return not (entry.is_off() or entry.is_frozen())


def _arc_info(pts) -> tuple | None:
    """(cx, cy, r, span in degrees, mid point) of a flattened circular arc, from three of its points."""
    if len(pts) < 3:
        return None
    (x1, y1), (x2, y2), (x3, y3) = pts[0], pts[len(pts) // 2], pts[-1]
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return None
    ux = ((x1 ** 2 + y1 ** 2) * (y2 - y3) + (x2 ** 2 + y2 ** 2) * (y3 - y1) + (x3 ** 2 + y3 ** 2) * (y1 - y2)) / d
    uy = ((x1 ** 2 + y1 ** 2) * (x3 - x2) + (x2 ** 2 + y2 ** 2) * (x1 - x3) + (x3 ** 2 + y3 ** 2) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    a1, a2, a3 = (math.atan2(y - uy, x - ux) for y, x in ((y1, x1), (y2, x2), (y3, x3)))
    wrap = lambda a: (a + math.pi) % (2 * math.pi) - math.pi  # noqa: E731
    span = abs(wrap(a2 - a1) + wrap(a3 - a2))
    return ux, uy, r, math.degrees(span), (x2, y2)


def _close_enough(a, b, tol=1e-9) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _entity_prims(e, dist: float) -> list[Prim]:
    kind = e.dxftype()
    prims: list[Prim] = []
    if kind in LINE_TYPES:
        p = ezpath.make_path(e)
        for sub in p.sub_paths():
            pts = [(v.x, v.y) for v in sub.flattening(dist)]
            if len(pts) < 2:
                continue
            closed = sub.is_closed or (len(pts) >= 4 and _close_enough(pts[0], pts[-1]))
            if closed and len(pts) >= 3:
                poly = Polygon(pts)
                if poly.area > 0 or not poly.is_valid:
                    prims.append(Prim(fix(poly), "ring"))
                    continue
            meta = {}
            if kind == "ARC":
                arc = _arc_info(pts)
                if arc:
                    meta["arc"] = arc
            prims.append(Prim(LineString(pts), "line", meta))
    elif kind == "HATCH":
        loops = []
        for sub in ezpath.from_hatch(e):
            pts = [(v.x, v.y) for v in sub.flattening(dist)]
            if len(pts) >= 3:
                loops.append(Polygon(pts))
        shape = nest_polygons(loops)
        if not shape.is_empty:
            prims.append(Prim(shape, "fill"))
    elif kind in ("SOLID", "TRACE"):
        pts = [(v.x, v.y) for v in e.wcs_vertices()]
        if len(pts) >= 3:
            poly = Polygon(pts)
            if not poly.is_empty:
                prims.append(Prim(fix(poly), "fill"))
    return prims


def _flatten(entities, on_error, depth: int = 0):
    """Yield leaf entities, expanding nested block references.

    A block reference that cannot be expanded (e.g. its definition is missing, as
    happens with anonymous blocks in some DWG->DXF conversions) is reported to
    ``on_error`` and skipped, never fatal."""
    for e in entities:
        t = e.dxftype()
        if t == "INSERT" and depth < MAX_BLOCK_DEPTH or t == "MLINE":
            try:
                expanded = list(e.virtual_entities())
            except Exception:
                on_error()
                continue
            if t == "MLINE":
                yield from expanded
            else:
                yield from _flatten(expanded, on_error, depth + 1)
        else:
            yield e


class _Reader:
    def __init__(self, doc: Drawing, cfg: Config, dist: float, ignore_veto: bool = False,
                 keep_other: bool = False, keep_fills: bool = False):
        self.doc, self.cfg, self.dist = doc, cfg, dist
        self.ignore_veto = ignore_veto
        self.keep_other = keep_other
        self.keep_fills = keep_fills
        self.items: list[Item] = []
        self.arcs: list[Item] = []  # arcs on layers that are no category: maybe the swing of a door
        self.shape_arcs = cfg.shape_openings and not ignore_veto and not keep_other  # not in the elevations
        self.thin: list[tuple[float, float, float, float]] | None = None  # long straight pieces, drawing units (None: not collected)
        self.thin_min = 0.0  # THIN_MIN_LENGTH in drawing units
        self.skipped = 0
        self.wall_layer_blocks = 0
        self._block_cat: dict[tuple[str, str], str | None] = {}
        self._by_layer: dict[str, tuple[bool, str | None, bool]] = {}  # layer -> (visible, category, vetoed)

    def _count_skipped(self) -> None:
        self.skipped += 1

    def layer_info(self, layer: str) -> tuple[bool, str | None, bool]:
        """(read?, category by the layer name, vetoed?): worked out once per layer, not once per entity."""
        info = self._by_layer.get(layer)
        if info is None:
            rules = self.cfg.layers
            visible = layer_used(self.doc, self.cfg, layer)
            info = (visible, rules.classify_layer(layer, self.ignore_veto) if visible else None,
                    rules.vetoed(layer) if visible else False)
            self._by_layer[layer] = info
        return info

    def visible(self, layer: str) -> bool:
        return self.layer_info(layer)[0]

    def _note_thin(self, e) -> None:
        """Keep the straight pieces of a line or a small polyline that are long enough to be a glazing strip: the
        panes of a ribbon window are drawn as thin rectangles or bundles of lines on any layer."""
        if e.dxftype() == "LINE":
            s, f = e.dxf.start, e.dxf.end
            if math.hypot(f.x - s.x, f.y - s.y) >= self.thin_min:
                self.thin.append((s.x, s.y, f.x, f.y))
            return
        vertices = len(e) if e.dxftype() == "LWPOLYLINE" else len(e.vertices)
        if vertices > THIN_MAX_VERTICES:
            return
        for p in self.prims_of([e]):
            for part in getattr(p.geom, "geoms", [p.geom]):  # a ring that is not valid comes back in several parts
                pts = list((part.exterior if part.geom_type == "Polygon" else part).coords)
                self.thin.extend((a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:])
                                 if math.hypot(b[0] - a[0], b[1] - a[1]) >= self.thin_min)

    def prims_of(self, entities) -> list[Prim]:
        out: list[Prim] = []
        for e in _flatten(entities, self._count_skipped):
            try:
                out.extend(_entity_prims(e, self.dist))
            except Exception:  # damaged/unsupported entity: keep going
                self.skipped += 1
        return out

    def walk(self, entities, depth: int = 0, parent: str = "0") -> None:
        """``parent``: the layer of the block reference these entities come from."""
        rules = self.cfg.layers
        for e in entities:
            t = e.dxftype()
            layer = e.dxf.layer
            inherits = bool(depth) and layer == "0"
            if inherits:
                if not self.keep_other:
                    continue  # inside a block, layer 0 means "inherit": the insert itself was not a category
                layer = parent  # an elevation draws its windows, doors and level marks as blocks of layer 0 lines
            if not self.visible(layer):
                continue
            if t == "INSERT":
                block = e.dxf.name
                if (layer, block) not in self._block_cat:
                    self._block_cat[(layer, block)] = rules.classify(layer, block, self.ignore_veto)
                cat = None if inherits else self._block_cat[(layer, block)]
                try:
                    virtual = list(e.virtual_entities())
                except Exception:
                    self.skipped += 1
                    continue
                if cat == "wall" and not self.cfg.walls_from_blocks:
                    # Furniture, fixtures and symbols are very often inserted on the wall layer
                    # (or on layer 0). Their lines are not walls.
                    self.wall_layer_blocks += 1
                    cat = None
                elif cat:
                    prims = self.prims_of(virtual)
                    if prims:
                        self.items.append(Item(layer, block, cat, prims))
                    continue
                if depth < MAX_BLOCK_DEPTH:
                    # e.g. a whole plan inserted as one block: classify inner entities
                    self.walk(virtual, depth + 1, layer)
                continue
            _, cat, vetoed = self.layer_info(layer)
            if inherits:
                cat = None  # loose linework: what the layer of the reference is called says nothing about it
            if self.shape_arcs and t == "ARC" and cat in (None, "wall") and not vetoed:
                prims = [p for p in self.prims_of([e]) if p.meta.get("arc")]
                if prims:  # maybe the swing of a door: decided later, when the walls are known
                    self.arcs.append(Item(layer, None, "door", prims))
            if self.thin is not None and t in THIN_TYPES and not vetoed:
                self._note_thin(e)
            if not cat:
                if not (self.keep_other and (t in LINE_TYPES or (self.keep_fills and t in FILL_TYPES))):
                    continue
                cat = "other"  # loose linework (and, on request, fills) of any layer, e.g. an elevation's roof silhouette
            prims = self.prims_of([e])
            if prims:
                self.items.append(Item(layer, None, cat, prims))


_ALL = object()  # "use cfg.area" marker


def read_items(doc: Drawing, cfg: Config, area=_ALL, ignore_veto: bool = False,
               keep_other: bool = False, unit: str | None = None, keep_fills: bool = False) -> ReadResult:
    """Read the modelspace into categorised items, scaled to metres.

    ``area``: crop window in drawing units (default ``cfg.area``; ``None`` = everything).
    ``unit``: reuse the drawing unit already decided by the main read, so secondary reads
    (elevations, roof plan) never guess differently.
    ``ignore_veto`` / ``keep_other``: used for elevation drawings (see ``LayerRules.classify``).
    ``keep_fills``: with ``keep_other``, hatches and solids of layers without a category are kept too
    (an elevation draws its glass as a hatch on any layer).
    """
    warnings: list[str] = []
    area = cfg.area if area is _ALL else area
    unit = unit or cfg.units or INSUNITS_TO_NAME.get(int(doc.header.get("$INSUNITS", 0) or 0))
    guessed = unit is None
    provisional = UNIT_TO_METERS[unit] if unit else 0.01
    reader = _Reader(doc, cfg, dist=cfg.arc_tolerance / provisional, ignore_veto=ignore_veto,
                     keep_other=keep_other, keep_fills=keep_fills)
    if cfg.shape_openings and not ignore_veto and not keep_other and not guessed:
        reader.thin, reader.thin_min = [], THIN_MIN_LENGTH / provisional  # glazing strips: see glazing.py
    reader.walk(doc.modelspace())
    items = reader.items

    if guessed:
        geoms = [p.geom for it in items if it.category == "wall" for p in it.prims] or \
                [p.geom for it in items for p in it.prims]
        if geoms:
            xs0, ys0, xs1, ys1 = zip(*(g.bounds for g in geoms))
            span = max(max(xs1) - min(xs0), max(ys1) - min(ys0))
            unit = guess_unit(span)
        else:
            unit = "m"
        warnings.append(
            f"Il file non dichiara le unita' di disegno: assumo '{unit}'. "
            "Controlla le dimensioni nel riepilogo o forza l'unita' con --unita."
        )
    scale = UNIT_TO_METERS[unit]

    # Optional crop (given in drawing units), then scale everything to metres.
    window = box(*area[:4]) if area else None
    kept: list[Item] = []
    for it in items:
        if window is not None and not any(window.intersects(p.geom) for p in it.prims):
            continue
        for p in it.prims:
            p.geom = affinity.scale(p.geom, scale, scale, origin=(0, 0))
            arc = p.meta.get("arc")
            if arc:
                cx, cy, r, span, (mx, my) = arc
                p.meta["arc"] = (cx * scale, cy * scale, r * scale, span, (mx * scale, my * scale))
        kept.append(it)

    for it in reader.arcs:  # swing arcs found by their shape, on layers whose names say nothing
        prims = []
        for p in it.prims:
            cx, cy, r, span, (mx, my) = p.meta["arc"]
            r *= scale
            if not (SWING_RADIUS[0] <= r <= SWING_RADIUS[1] and SWING_SPAN[0] <= span <= SWING_SPAN[1]):
                continue
            if window is not None and not window.intersects(affinity.scale(p.geom, scale, scale, origin=(0, 0))):
                continue
            p.geom = affinity.scale(p.geom, scale, scale, origin=(0, 0))
            p.meta = {"arc": (cx * scale, cy * scale, r, span, (mx * scale, my * scale)), "shape_door": True}
            prims.append(p)
            # The leaf, open and shut: the two radii close the arc into the sector a door symbol draws, whose
            # shape (not the arc's chord) tells which way the wall runs.
            c = (cx * scale, cy * scale)
            ends = (p.geom.coords[0], p.geom.coords[-1])
            prims.extend(Prim(LineString([c, e]), "line", {"shape_door": True}) for e in ends)
        if prims:
            kept.append(Item(it.layer, None, "door", prims))

    thin = np.asarray(reader.thin or [], float).reshape(-1, 4) * scale
    if window is not None and len(thin):
        mid_x, mid_y = (thin[:, 0] + thin[:, 2]) / 2, (thin[:, 1] + thin[:, 3]) / 2
        x0, y0, x1, y1 = (v * scale for v in area[:4])
        thin = thin[(mid_x >= x0) & (mid_x <= x1) & (mid_y >= y0) & (mid_y <= y1)]

    if reader.wall_layer_blocks:
        warnings.append(
            f"{reader.wall_layer_blocks} blocchi inseriti su un layer di muri sono stati ignorati "
            "(di solito sono arredi). Se contengono muri usa --muri-da-blocchi."
        )
    if reader.skipped:
        warnings.append(
            f"{reader.skipped} entita' o blocchi non leggibili sono stati ignorati "
            "(blocchi senza definizione o entita' danneggiate)."
        )
    return ReadResult(kept, unit, scale, guessed, reader.skipped, warnings, thin)


def _rough_bounds(e) -> list[tuple[float, float]] | None:
    """Cheap extent points of an entity, in drawing units (no block expansion)."""
    t = e.dxftype()
    try:
        if t == "LINE":
            return [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
        if t == "LWPOLYLINE":
            return [(p[0], p[1]) for p in e.get_points("xy")]
        if t in ("CIRCLE", "ARC"):
            c, r = e.dxf.center, e.dxf.radius
            return [(c.x - r, c.y - r), (c.x + r, c.y + r)]
        if t == "INSERT":
            return [(e.dxf.insert.x, e.dxf.insert.y)]
    except Exception:
        return None
    return None


def declared_units(doc: Drawing) -> str | None:
    return INSUNITS_TO_NAME.get(int(doc.header.get("$INSUNITS", 0) or 0))


def layer_summary(doc: Drawing, cfg: Config) -> list[dict]:
    """Per-layer overview for ``--elenca-layer``."""
    counts: dict[str, Counter] = defaultdict(Counter)
    blocks: dict[str, set] = defaultdict(set)
    extent: dict[str, list[float]] = {}
    for e in doc.modelspace():
        layer = e.dxf.layer
        counts[layer][e.dxftype()] += 1
        if e.dxftype() == "INSERT":
            blocks[layer].add(e.dxf.name)
        pts = _rough_bounds(e)
        if pts:
            xs, ys = zip(*pts)
            box_ = extent.setdefault(layer, [min(xs), min(ys), max(xs), max(ys)])
            box_[0], box_[1] = min(box_[0], *xs), min(box_[1], *ys)
            box_[2], box_[3] = max(box_[2], *xs), max(box_[3], *ys)
    rows = []
    for layer in sorted(counts):
        hidden = False
        if doc.layers.has_entry(layer):
            entry = doc.layers.get(layer)
            hidden = entry.is_off() or entry.is_frozen()
        rows.append({
            "layer": layer,
            "category": cfg.layers.classify(layer),
            "garden": cfg.layers.garden_kind(layer) if cfg.layers.classify_layer(layer) is None else None,
            "hidden": hidden,
            "entities": dict(counts[layer]),
            "blocks": sorted(blocks[layer]),
            "bounds": tuple(extent[layer]) if layer in extent else None,
        })
    return rows
