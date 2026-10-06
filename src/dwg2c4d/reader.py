"""Read a DXF document into categorised, metre-scaled 2D items."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ezdxf import path as ezpath
from ezdxf.document import Drawing
from shapely import affinity
from shapely.geometry import LineString, Polygon, box
from shapely.geometry.base import BaseGeometry

from .config import INSUNITS_TO_NAME, UNIT_TO_METERS, Config
from .geom import fix, nest_polygons

LINE_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}
MAX_BLOCK_DEPTH = 8


@dataclass
class Prim:
    """One drawing primitive. ``kind``: line (open), ring (closed outline), fill (hatch/solid)."""

    geom: BaseGeometry
    kind: str


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


def guess_unit(span: float) -> str:
    """Guess drawing units from the largest plan dimension when the file has none."""
    if span < 300:
        return "m"
    if span < 8000:
        return "cm"
    return "mm"


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
            prims.append(Prim(LineString(pts), "line"))
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
                 keep_other: bool = False):
        self.doc, self.cfg, self.dist = doc, cfg, dist
        self.ignore_veto = ignore_veto
        self.keep_other = keep_other
        self.items: list[Item] = []
        self.skipped = 0
        self.wall_layer_blocks = 0

    def _count_skipped(self) -> None:
        self.skipped += 1

    def visible(self, layer: str) -> bool:
        if self.cfg.include_hidden or not self.doc.layers.has_entry(layer):
            return True
        entry = self.doc.layers.get(layer)
        return not (entry.is_off() or entry.is_frozen())

    def prims_of(self, entities) -> list[Prim]:
        out: list[Prim] = []
        for e in _flatten(entities, self._count_skipped):
            try:
                out.extend(_entity_prims(e, self.dist))
            except Exception:  # damaged/unsupported entity: keep going
                self.skipped += 1
        return out

    def walk(self, entities, depth: int = 0) -> None:
        rules = self.cfg.layers
        for e in entities:
            t = e.dxftype()
            layer = e.dxf.layer
            if depth and layer == "0":
                continue  # inside a block, layer 0 means "inherit": the insert itself was not a category
            if not self.visible(layer):
                continue
            if t == "INSERT":
                block = e.dxf.name
                cat = rules.classify(layer, block, self.ignore_veto)
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
                    self.walk(virtual, depth + 1)
                continue
            cat = rules.classify_layer(layer, self.ignore_veto)
            if not cat:
                if not (self.keep_other and t in LINE_TYPES):
                    continue
                cat = "other"  # loose linework of any layer (e.g. an elevation's roof silhouette)
            prims = self.prims_of([e])
            if prims:
                self.items.append(Item(layer, None, cat, prims))


_ALL = object()  # "use cfg.area" marker


def read_items(doc: Drawing, cfg: Config, area=_ALL, ignore_veto: bool = False,
               keep_other: bool = False, unit: str | None = None) -> ReadResult:
    """Read the modelspace into categorised items, scaled to metres.

    ``area``: crop window in drawing units (default ``cfg.area``; ``None`` = everything).
    ``unit``: reuse the drawing unit already decided by the main read, so secondary reads
    (elevations, roof plan) never guess differently.
    ``ignore_veto`` / ``keep_other``: used for elevation drawings (see ``LayerRules.classify``).
    """
    warnings: list[str] = []
    area = cfg.area if area is _ALL else area
    unit = unit or cfg.units or INSUNITS_TO_NAME.get(int(doc.header.get("$INSUNITS", 0) or 0))
    guessed = unit is None
    provisional = UNIT_TO_METERS[unit] if unit else 0.01
    reader = _Reader(doc, cfg, dist=cfg.arc_tolerance / provisional, ignore_veto=ignore_veto,
                     keep_other=keep_other)
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
        kept.append(it)

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
    return ReadResult(kept, unit, scale, guessed, reader.skipped, warnings)


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
            "hidden": hidden,
            "entities": dict(counts[layer]),
            "blocks": sorted(blocks[layer]),
            "bounds": tuple(extent[layer]) if layer in extent else None,
        })
    return rows
