"""Read door/window heights (and roof silhouette lines) from elevation drawings.

An elevation (prospetto) is only usable when it is drawn *aligned with the plan*: for a
south (north) elevation placed below (above) the plan, the horizontal drawing coordinate
x is the same as in the plan. That is how elevations are normally projected from a plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry import LineString, box
from shapely.geometry.base import BaseGeometry

from .config import Config
from .geom import polygons_of, union
from .openings import Opening, _symbols
from .reader import Item

MIN_LINE = 0.5  # horizontal lines shorter than this are not roof/eave candidates (m)
HORIZONTAL_TOL = 1e-3  # a line is horizontal if its endpoints differ in y by less than this (m)
MIN_OVERLAP = 0.6  # fraction of the narrower of {symbol, opening} that must overlap in x
FACADE_DEPTH = 0.5  # an opening of a *different* kind must be this close to the outer face (m)


@dataclass
class Symbol:
    kind: str  # "door" | "window"
    x0: float
    x1: float
    y0: float  # absolute drawing y, metres
    y1: float


@dataclass
class Elevation:
    side: str  # "south" | "north"
    zero: float  # drawing y (m) of the finished floor
    zero_source: str  # "porta" | "indicata"
    symbols: list[Symbol] = field(default_factory=list)
    hlines: list[tuple[float, float, float]] = field(default_factory=list)  # (y, x0, x1), metres


def _horizontal_segments(items: list[Item]) -> list[tuple[float, float, float]]:
    out = []
    for it in items:
        for p in it.prims:
            geoms = [p.geom] if p.geom.geom_type == "LineString" else \
                [LineString(poly.exterior.coords) for poly in polygons_of(p.geom)]
            for ls in geoms:
                c = list(ls.coords)
                for (ax, ay), (bx, by) in zip(c, c[1:]):
                    if abs(ay - by) < HORIZONTAL_TOL and abs(bx - ax) >= MIN_LINE:
                        out.append((round((ay + by) / 2.0, 4), min(ax, bx), max(ax, bx)))
    return sorted(set(out))


def named_symbols(items: list[Item]) -> list[Symbol]:
    """The doors and windows of an elevation drawn on layers (or in blocks) that the names call doors and windows."""
    symbols: list[Symbol] = []
    for kind in ("door", "window"):
        for sym in _symbols(items, kind):
            if sym.geom.is_empty:
                continue
            x0, y0, x1, y1 = sym.geom.bounds
            symbols.append(Symbol(kind, x0, x1, y0, y1))
    # A window drawn as a frame plus an inner pane (nested rectangles) is one window: keep
    # the outermost shape.
    tol = 0.01
    return [a for a in symbols
            if not any(b is not a and b.kind == a.kind
                       and b.x0 <= a.x0 + tol and b.x1 >= a.x1 - tol
                       and b.y0 <= a.y0 + tol and b.y1 >= a.y1 - tol
                       and (b.x1 - b.x0) * (b.y1 - b.y0) > (a.x1 - a.x0) * (a.y1 - a.y0)
                       for b in symbols)]


def read_elevation(items: list[Item], spec: tuple[float, ...], unit_scale: float,
                   plan_bounds: tuple[float, float, float, float], warnings: list[str],
                   index: int) -> Elevation | None:
    """Interpret one elevation drawing. ``items`` come from ``read_items`` restricted to its
    area (veto ignored, loose lines kept); ``spec`` is (xmin, ymin, xmax, ymax[, floor_y])."""
    label = f"Prospetto {index + 1}"
    _, py0, _, py1 = plan_bounds
    centre_y = (spec[1] + spec[3]) / 2.0 * unit_scale
    if centre_y < py0:
        side = "south"
    elif centre_y > py1:
        side = "north"
    else:
        warnings.append(
            f"{label}: l'area si sovrappone alla pianta (o sta di lato): i prospetti vanno disegnati "
            "sotto (facciata sud) o sopra (facciata nord) la pianta, con le stesse coordinate X."
        )
        return None

    symbols = named_symbols(items)
    if not symbols:
        warnings.append(f"{label}: nessuna porta o finestra riconosciuta nell'area.")
        return None

    if len(spec) == 5:
        zero, source = spec[4] * unit_scale, "indicata"
    else:
        doors = [s.y0 for s in symbols if s.kind == "door"]
        if not doors:
            warnings.append(
                f"{label}: nessuna porta da cui ricavare la quota del pavimento: aggiungi la quota Y "
                "del pavimento finito come quinto valore di --prospetto."
            )
            return None
        zero, source = min(doors), "porta"
    return Elevation(side, zero, source, symbols, _horizontal_segments(items))


def apply_elevation(elev: Elevation, openings: list[Opening], walls: BaseGeometry,
                    cfg: Config, warnings: list[str]) -> tuple[int, int]:
    """Set z0/z1 of the plan openings that appear in ``elev``.
    Returns (symbols matched, symbols in the elevation)."""
    matched = 0
    south = elev.side == "south"
    for sym in elev.symbols:
        width = sym.x1 - sym.x0
        best: tuple[tuple[int, float], Opening] | None = None
        for o in openings:
            if abs(o.axis[0]) < 0.85:  # only walls running along x can face a south/north elevation
                continue
            ox0, _, ox1, _ = o.cut.bounds
            overlap = min(sym.x1, ox1) - max(sym.x0, ox0)
            if overlap < MIN_OVERLAP * min(width, ox1 - ox0):
                continue
            # same kind first, then the outermost opening in this x range (the facade one)
            key = (0 if o.kind == sym.kind else 1, o.center[1] if south else -o.center[1])
            if best is None or key < best[0]:
                best = (key, o)
        if best is None:
            continue
        o = best[1]
        if o.kind != sym.kind:
            # a door drawn as a window (or vice versa): only if it is on the outer face
            strip = walls.intersection(box(max(sym.x0, o.cut.bounds[0]), -1e9,
                                           min(sym.x1, o.cut.bounds[2]), 1e9))
            if strip.is_empty:
                continue
            outer = strip.bounds[1] if south else strip.bounds[3]
            if abs(o.center[1] - outer) > FACADE_DEPTH:
                continue
        z0 = max(0.0, sym.y0 - elev.zero)
        z1 = min(cfg.wall_height, sym.y1 - elev.zero)
        if sym.kind == "door" or z0 < 0.05:
            z0 = 0.0
        if z1 - z0 < 0.2:
            continue
        o.z0, o.z1, o.from_elevation = z0, z1, True
        o.z1_drawn = sym.y1 - elev.zero
        matched += 1
    unmatched = len(elev.symbols) - matched
    if unmatched:
        warnings.append(
            f"Prospetto ({'sud' if south else 'nord'}): {unmatched} su {len(elev.symbols)} simboli "
            "non corrispondono a nessuna apertura della pianta (stesse coordinate X?)."
        )
    return matched, len(elev.symbols)


ELEVATION_LAYER_TOKENS = ("prospett", "elevat", "facciat")
ZONE_MARGIN = 1.0  # m around the drawn lines of an elevation layer: roof lines, ground line... may lie beyond them
ROOF_RISE = 4.0  # m: how far past the layer's lines (towards the plan) roof lines are looked for
PLAN_CLEARANCE = 0.5  # the zone never comes closer than this to the plan
MIN_X_OVERLAP = 0.5  # of the narrower of {zone, plan}


def is_elevation_layer(name: str) -> bool:
    import re

    return any(t.startswith(ELEVATION_LAYER_TOKENS) for t in re.split(r"[^a-z]+", name.lower()) if t)


def find_elevation_zones(items: list[Item], plan_bounds: tuple[float, float, float, float],
                         unit_scale: float, cores: bool = False) -> list[tuple]:
    """Facade elevations found by their layer name ("Prospetto ..."): [(layer names, zone)] with the zone in
    drawing units. A zone counts when it lies wholly below or above the plan, shares the plan's x range
    (elevations are projected straight from the plan) and holds door/window symbols: an interior
    elevation (a kitchen wall, a wardrobe) has none and is left out.
    With ``cores`` each entry also has the *core* of the zone: the extent of the drawn layers plus the margin,
    without the stretch towards the plan where the roof lines are looked for ([(layers, zone, core)])."""
    px0, py0, px1, py1 = plan_bounds
    boxes: list[tuple[str, tuple[float, float, float, float]]] = []
    for it in items:
        if not is_elevation_layer(it.layer):
            continue
        geoms = [p.geom for p in it.prims if not p.geom.is_empty]
        if geoms:
            x0, y0, x1, y1 = union(geoms).bounds
            boxes.append((it.layer, (x0, y0, x1, y1)))
    # layers whose boxes overlap are the same drawing
    zones: list[list] = []
    for layer, b in boxes:
        for z in zones:
            zb = z[1]
            if b[0] <= zb[2] and b[2] >= zb[0] and b[1] <= zb[3] and b[3] >= zb[1]:
                z[0].add(layer)
                z[1] = (min(zb[0], b[0]), min(zb[1], b[1]), max(zb[2], b[2]), max(zb[3], b[3]))
                break
        else:
            zones.append([{layer}, b])
    symbols = [x.geom.bounds for kind in ("door", "window") for x in _symbols(items, kind) if not x.geom.is_empty]
    out = []
    for layers, (x0, y0, x1, y1) in zones:
        if not (y1 < py0 or y0 > py1):
            continue  # beside or over the plan: not an elevation projected from it
        x0, x1 = x0 - ZONE_MARGIN, x1 + ZONE_MARGIN
        core = (x0 / unit_scale, (y0 - ZONE_MARGIN) / unit_scale, x1 / unit_scale, (y1 + ZONE_MARGIN) / unit_scale)
        # the roof of a facade rises above the drawn layer, towards the plan: look further that way
        y0, y1 = (y0 - ZONE_MARGIN, min(y1 + ROOF_RISE, py0 - PLAN_CLEARANCE)) if y1 < py0 else \
            (max(y0 - ROOF_RISE, py1 + PLAN_CLEARANCE), y1 + ZONE_MARGIN)
        overlap = min(x1, px1) - max(x0, px0)
        if overlap < MIN_X_OVERLAP * min(x1 - x0, px1 - px0):
            continue
        inside = [b for b in symbols if x0 <= b[0] and b[2] <= x1 and y0 <= b[1] and b[3] <= y1]
        if not inside:
            continue
        zone = (x0 / unit_scale, y0 / unit_scale, x1 / unit_scale, y1 / unit_scale)
        out.append((", ".join(sorted(layers)), zone, core) if cores else (", ".join(sorted(layers)), zone))
    return sorted(out, key=lambda z: z[1][1])
