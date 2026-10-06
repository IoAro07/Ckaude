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


def _flatten(entities, depth: int = 0):
    """Yield leaf entities, expanding nested block references."""
    for e in entities:
        t = e.dxftype()
        if t == "INSERT" and depth < MAX_BLOCK_DEPTH:
            yield from _flatten(e.virtual_entities(), depth + 1)
        elif t == "MLINE":
            yield from e.virtual_entities()
        else:
            yield e


class _Reader:
    def __init__(self, doc: Drawing, cfg: Config, dist: float):
        self.doc, self.cfg, self.dist = doc, cfg, dist
        self.items: list[Item] = []
        self.skipped = 0

    def visible(self, layer: str) -> bool:
        if self.cfg.include_hidden or not self.doc.layers.has_entry(layer):
            return True
        entry = self.doc.layers.get(layer)
        return not (entry.is_off() or entry.is_frozen())

    def prims_of(self, entities) -> list[Prim]:
        out: list[Prim] = []
        for e in _flatten(entities):
            try:
                out.extend(_entity_prims(e, self.dist))
            except Exception:  # damaged/unsupported entity: keep going
                self.skipped += 1
        return out

    def walk(self, entities, depth: int = 0, parent_layer: str | None = None) -> None:
        rules = self.cfg.layers
        for e in entities:
            t = e.dxftype()
            layer = e.dxf.layer
            if layer == "0" and parent_layer:
                layer = parent_layer
            if not self.visible(layer):
                continue
            if t == "INSERT":
                block = e.dxf.name
                cat = rules.classify(layer, block)
                try:
                    virtual = list(e.virtual_entities())
                except Exception:
                    self.skipped += 1
                    continue
                if cat:
                    prims = self.prims_of(virtual)
                    if prims:
                        self.items.append(Item(layer, block, cat, prims))
                elif depth < MAX_BLOCK_DEPTH:
                    # e.g. a whole plan inserted as one block: classify inner entities
                    self.walk(virtual, depth + 1, parent_layer=layer if layer != "0" else None)
                continue
            cat = rules.classify(layer)
            if not cat:
                continue
            prims = self.prims_of([e])
            if prims:
                self.items.append(Item(layer, None, cat, prims))


def read_items(doc: Drawing, cfg: Config) -> ReadResult:
    warnings: list[str] = []
    unit = cfg.units or INSUNITS_TO_NAME.get(int(doc.header.get("$INSUNITS", 0) or 0))
    guessed = unit is None
    provisional = UNIT_TO_METERS[unit] if unit else 0.01
    reader = _Reader(doc, cfg, dist=cfg.arc_tolerance / provisional)
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
    window = box(*cfg.area) if cfg.area else None
    kept: list[Item] = []
    for it in items:
        if window is not None and not any(window.intersects(p.geom) for p in it.prims):
            continue
        for p in it.prims:
            p.geom = affinity.scale(p.geom, scale, scale, origin=(0, 0))
        kept.append(it)

    if reader.skipped:
        warnings.append(f"{reader.skipped} entita' non leggibili sono state ignorate.")
    return ReadResult(kept, unit, scale, guessed, reader.skipped, warnings)


def layer_summary(doc: Drawing, cfg: Config) -> list[dict]:
    """Per-layer overview for ``--elenca-layer``."""
    counts: dict[str, Counter] = defaultdict(Counter)
    blocks: dict[str, set] = defaultdict(set)
    for e in doc.modelspace():
        layer = e.dxf.layer
        counts[layer][e.dxftype()] += 1
        if e.dxftype() == "INSERT":
            blocks[layer].add(e.dxf.name)
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
        })
    return rows
