"""What the elevations of a site sheet say of its buildings: how high their roofs start and rise, and which building each shows.

An elevation of a farm is drawn with no plan next to it. Its roofs are hatches of tiles: the box of each hatch is a roof
seen from the side, its bottom the eaves, its top the ridge, its width the length of the side of the roof that the
elevation faces. The roofs of the site plan have sides too: a building whose roof has a side as long as the roof of the
elevation (within a few per cent) is likely the building drawn, and the more elevations agree the likelier it is. So

* ``roof_bands`` reads the tile hatches of every elevation, in metres above its floor (the level mark +0,00);
* ``roof_levels`` joins the bands of one height (a roof is seen in two or three elevations);
* ``match_levels`` gives each body of a roof (sitemode) the level whose bands are the sides of it: the eaves height, and the
  pitch from the rise of the roof over its half span. A body no level fits keeps the default and the report says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from .geom import polygons_of, union

MIN_BAND = 2.0  # m: a tile hatch narrower than this in an elevation is a chimney pot, a cap, not a roof
MIN_RISE = 0.3  # m: ... and so is one lower than this
SAME_EAVES = 0.45  # m: bands whose eaves differ by less than this ...
SAME_RIDGE = 0.35  # m: ... and whose ridges differ by less than this are one roof (the eaves of a wing step by a few cm)
SIDE_TOLERANCE = 0.08  # a band is a side of a roof if the lengths differ by less than this share ...
SIDE_SLACK = 0.30  # m: ... or by less than this
MIN_SIDE = 2.0  # m: shorter sides of a roof are not looked at
STRAIGHT = 3.0  # degrees: sides of a roof that run within this of one another are one side
SIMPLIFY = 0.15  # m: the outline of a roof is simplified by this before its sides are measured
MIN_MATCHES = 2  # a level fits a body if this many of its bands are sides of it (or one band: a roof seen once, see ONLY_ONE)
ONLY_ONE = 6.0  # m: ... one band is enough if it is this long and no other body has a side of that length
OWNER_SHARE = 0.75  # a body owns a level if it explains this share of what the best body explains of it
AIR_GAP = 0.35  # m: a side of a roof with another body this close along it is inside the building, not a facade ...
AIR_SHARE = 0.5  # ... if the other body is along this share of it
FRONT_OVERLAP = 0.3  # a roof that overlaps the roof of a facade in the elevation by this share is in front of it, not beside it
FRONT_LOWER = 0.5  # m: ... and is lower than it by this much (else it says nothing)
CUE_MIN = 1.0  # m: a roof this close to the line through the facade (or its middle) says nothing of which side it is
EDGE_SLACK = 0.3  # m: a symbol this far beyond the ends of the roof of its facade is still under it
TOP_CLEAR = 0.25  # m: the top of an opening is this far under the eaves, at most
MIN_HEIGHT = 0.4  # m: an opening that stands less high than this above its floor (and under the eaves) is not put
DOUBLE_DOOR = 1.3  # m: a door wider than this has two leaves
WIDE_WINDOW = 1.6  # m: a window wider than this is divided in sashes of about ``SASH_WIDTH``
SASH_WIDTH = 0.8  # m
PITCH_RANGE = (8.0, 45.0)  # degrees: a pitch the elevations give outside this is not believed (a band of the wrong roof)


@dataclass
class RoofBand:
    """A roof as an elevation draws it: metres above the floor of the elevation, the sheet's metres along it."""

    view: int
    eave: float
    ridge: float
    x0: float
    x1: float

    @property
    def length(self) -> float:
        return self.x1 - self.x0


@dataclass
class RoofLevel:
    """Roofs of the elevations that start at one height and rise to one: the bands of one roof seen from the sides."""

    bands: list[RoofBand] = field(default_factory=list)

    @property
    def eave(self) -> float:
        return sorted(b.eave for b in self.bands)[len(self.bands) // 2]

    @property
    def ridge(self) -> float:
        return sorted(b.ridge for b in self.bands)[len(self.bands) // 2]

    @property
    def rise(self) -> float:
        return self.ridge - self.eave

    @property
    def views(self) -> list[int]:
        return sorted({b.view for b in self.bands})


@dataclass
class ElevationRead:
    """An elevation of the sheet as ``elevsymbols`` read it."""

    view: object  # the autodetect.View
    symbols: object  # the elevsymbols.ViewSymbols
    floor: float  # the y of its finished floor, metres


def read_elevations(doc, cfg, analysis) -> list[ElevationRead]:
    """The elevations of the sheet whose floor level is known (a level mark +0,00, the doors): without it nothing in them can
    be measured."""
    from .elevsymbols import read_view_symbols

    reads = []
    for v in analysis.views:
        if v.kind != "elevation":
            continue
        vs = read_view_symbols(doc, cfg, v.bbox, analysis.unit.unit, analysis.unit.scale)
        if vs.floor is not None:
            reads.append(ElevationRead(v, vs, vs.floor))
    return reads


def roof_bands(analysis, reads: list[ElevationRead]) -> list[RoofBand]:
    """The roofs the elevations draw: the box of every hatch of tiles in them, above the floor of its elevation."""
    from .autodetect import TILE_PATTERNS

    scale = analysis.unit.scale
    bands = []
    for r in reads:
        for h in analysis.soup.hatches:
            if not any(t in h["pattern"].upper() for t in TILE_PATTERNS):
                continue
            x0, y0, x1, y1 = (c * scale for c in h["bbox"])
            if not (r.view.contains(h["bbox"][0], h["bbox"][1]) and r.view.contains(h["bbox"][2], h["bbox"][3])):
                continue
            if x1 - x0 >= MIN_BAND and y1 - y0 >= MIN_RISE:
                bands.append(RoofBand(r.view.id, y0 - r.floor, y1 - r.floor, x0, x1))
    return bands


def roof_levels(bands: list[RoofBand]) -> list[RoofLevel]:
    """The bands joined into levels: those whose eaves and ridges are about the same height are one roof."""
    levels: list[RoofLevel] = []
    for band in sorted(bands, key=lambda b: -b.eave):
        for level in levels:
            if any(abs(band.eave - b.eave) <= SAME_EAVES and abs(band.ridge - b.ridge) <= SAME_RIDGE for b in level.bands):
                level.bands.append(band)
                break
        else:
            levels.append(RoofLevel([band]))
    return levels


@dataclass
class Side:
    """A straight side of the outline of a roof, walked counter-clockwise: the outside is on its right. The elevation of that
    side, seen from outside, runs along it from ``a`` (the left) to ``b`` (the right)."""

    a: tuple[float, float]
    b: tuple[float, float]

    @property
    def length(self) -> float:
        return math.dist(self.a, self.b)

    @property
    def direction(self) -> tuple[float, float]:
        """The unit vector from a to b: to the right of a person who looks at the side from outside."""
        return (self.b[0] - self.a[0]) / self.length, (self.b[1] - self.a[1]) / self.length

    @property
    def normal(self) -> tuple[float, float]:
        """The unit vector that points away from the building."""
        dx, dy = self.direction
        return dy, -dx


def _same_way(d0: float, d1: float) -> bool:
    gap = abs(d0 - d1) % 180.0
    return min(gap, 180.0 - gap) <= STRAIGHT


def sides_of(shape: BaseGeometry) -> list[Side]:
    """The straight sides of the outline of a roof: pieces that run the same way, one after the other, are one side."""
    sides = []
    for poly in polygons_of(shape):
        pts = list(orient(poly.simplify(SIMPLIFY), 1.0).exterior.coords)[:-1]
        runs: list[list] = []  # [start, end, direction in degrees]
        for k in range(len(pts)):
            a, b = pts[k], pts[(k + 1) % len(pts)]
            d = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
            if runs and _same_way(runs[-1][2], d):
                runs[-1][1] = b
            else:
                runs.append([a, b, d])
        if len(runs) > 1 and _same_way(runs[0][2], runs[-1][2]):
            runs[0][0] = runs.pop()[0]
        sides.extend(Side(r[0], r[1]) for r in runs if math.dist(r[0], r[1]) >= MIN_SIDE)
    return sides


def side_lengths(shape: BaseGeometry) -> list[float]:
    return [s.length for s in sides_of(shape)]


def _is_side(length: float, sides: list[float]) -> bool:
    return any(abs(length - s) <= max(SIDE_TOLERANCE * length, SIDE_SLACK) for s in sides)


def fit(level: RoofLevel, sides: list[float]) -> list[RoofBand]:
    """The bands of ``level`` that are sides of a roof whose sides are ``sides``, one per elevation (an elevation draws a roof
    once, though two sides may be as long as it)."""
    seen: dict[int, RoofBand] = {}
    for b in level.bands:
        if _is_side(b.length, sides):
            seen.setdefault(b.view, b)
    return list(seen.values())


def match_levels(buildings: list, levels: list[RoofLevel]) -> list[tuple]:
    """Give each body of each building the level of the elevations whose bands are sides of its roof: its eaves height, the
    pitch from the rise over the half span. Returns [(building, body, level, the bands that fit)] for the report.

    A body owns a level if at least ``MIN_MATCHES`` elevations draw it with a band as long as a side of the body, and the body
    explains the level about as well as the best body of the site does (``OWNER_SHARE``: a small house with a side as long as
    the front of the farm does not own the farm's roof); one elevation is enough for a long band that no other body has a side
    of that length for. A body that owns two levels takes the one it explains better; a tie says nothing."""
    from .sitemode import body_outline, half_span

    candidates = []  # (building, body, sides)
    for b in buildings:
        for body in b.bodies:
            candidates.append((b, body, side_lengths(body_outline(b, body))))
    owned: dict[int, list[tuple[float, int, int, list[RoofBand]]]] = {}  # body index -> (share of the level explained, bands, level, the bands)
    for index, level in enumerate(levels):
        fits = [fit(level, sides) for _, _, sides in candidates]
        top = max((len(f) for f in fits), default=0)
        for k, bands in enumerate(fits):
            if not bands or len(bands) < OWNER_SHARE * top:
                continue
            if len(bands) < MIN_MATCHES:
                length = max(x.length for x in bands)
                if length < ONLY_ONE or any(_is_side(length, s) for j, (_, _, s) in enumerate(candidates) if j != k):
                    continue
            owned.setdefault(k, []).append((len(bands) / len(set(x.view for x in level.bands)), len(bands), index, bands))
    matched = []
    for k, options in owned.items():
        options.sort(reverse=True)
        if len(options) > 1 and options[0][:2] == options[1][:2]:
            continue
        _, _, index, bands = options[0]
        b, body, _ = candidates[k]
        level = levels[index]
        body.eave_height, body.eave_source, body.level = level.eave, "prospetto", level
        run = half_span(b, body)
        pitch = math.degrees(math.atan2(level.rise, run)) if run > 0 else None
        body.measured_pitch = pitch if pitch is not None and PITCH_RANGE[0] <= pitch <= PITCH_RANGE[1] else None
        matched.append((b, body, level, bands))
    return matched


# ------------------------------------------------------------------------------------------------
# doors and windows

def facade_sides(building, body) -> list[Side]:
    """The sides of the roof of a body that face the open air: not those along another body of the building (inside)."""
    from .sitemode import body_outline

    others = union(body_outline(building, o) for o in building.bodies if o is not body)
    return [s for s in sides_of(body_outline(building, body))
            if others.is_empty or others.buffer(AIR_GAP).intersection(LineString([s.a, s.b])).length < AIR_SHARE * s.length]


@dataclass
class Facade:
    """The facade of a body that an elevation shows: which side of its roof, how sure, and the band that tells the position."""

    building: object
    body: object
    side: Side
    band: RoofBand
    score: int  # the cues that agree with it less those against it (the neighbouring roofs the elevation shows)
    view: int


def _centre(building, body) -> tuple[float, float]:
    from .sitemode import body_outline

    c = body_outline(building, body).centroid
    return c.x, c.y


def _cue(side: Side, band: RoofBand, body_centre: tuple[float, float], eave: float,
         other: RoofBand, other_centre: tuple[float, float], other_eave: float) -> int:
    """+1 if the roof the elevation shows beside the roof of ``side`` is where the plan puts it when ``side`` is the facade
    drawn, -1 if it is on the other side, 0 if the plan cannot tell: beside it (to its left or right in the drawing), or in
    front of it (a lower roof that hides part of it)."""
    dx, dy = other_centre[0] - body_centre[0], other_centre[1] - body_centre[1]
    overlap = min(band.x1, other.x1) - max(band.x0, other.x0)
    if overlap < FRONT_OVERLAP * min(band.length, other.length):  # beside it
        along = dx * side.direction[0] + dy * side.direction[1]
        if abs(along) < CUE_MIN:
            return 0
        return 1 if (along > 0) == ((other.x0 + other.x1) > (band.x0 + band.x1)) else -1
    if other_eave < eave - FRONT_LOWER:  # a lower roof drawn over its facade stands in front of it
        out = dx * side.normal[0] + dy * side.normal[1]
        return 0 if abs(out) < CUE_MIN else (1 if out > 0 else -1)
    return 0


def find_facades(matched: list[tuple]) -> tuple[list[Facade], list[str]]:
    """The facade each elevation shows of each body it matched to: one of the sides of the roof as long as the band the elevation
    draws, the one the neighbouring roofs in the elevation agree with. (Facade, notes): a facade two sides fit equally is left
    out and told."""
    by_view: dict[int, list[tuple]] = {}
    for b, body, _, bands in matched:
        for band in bands:
            by_view.setdefault(band.view, []).append((b, body, band))
    facades: list[Facade] = []
    notes: list[str] = []
    for view, shown in sorted(by_view.items()):
        for b, body, band in shown:
            sides = [s for s in facade_sides(b, body) if _is_side(band.length, [s.length])]
            if not sides:
                continue
            centre, eave = _centre(b, body), body.eave_height or 0.0
            scores = [sum(_cue(s, band, centre, eave, ob, _centre(b2, body2), body2.eave_height or 0.0)
                          for b2, body2, ob in shown if body2 is not body) for s in sides]
            best = max(scores)
            top = [s for s, sc in zip(sides, scores) if sc == best]
            if len(top) > 1 or (len(sides) > 1 and best <= 0):
                notes.append(f"Edificio {b.id}, corpo {b.bodies.index(body) + 1}: il prospetto {view} mostra una facciata lunga "
                             f"{band.length:.1f} m ma {len(top)} lati del tetto hanno quella misura e nulla nel prospetto dice quale: "
                             "non ho messo le sue aperture.")
                continue
            facades.append(Facade(b, body, top[0], band, best, view))
    return facades, notes


def _leaves(width: float) -> list[dict]:
    """A door up to ``DOUBLE_DOOR`` wide has one leaf hinged on the left, a wider one two."""
    if width <= DOUBLE_DOOR:
        return [{"hinge": -1, "width": width, "swing": 1}]
    return [{"hinge": -1, "width": width / 2, "swing": 1}, {"hinge": 1, "width": width / 2, "swing": 1}]


def _owner(facades: list[Facade], x: float, top: float) -> Facade | None:
    """The facade a symbol of an elevation belongs to: the one whose roof is over it, the lowest such roof when several
    (a lean-to's roof over a door, the farm's roof over the lean-to's). ``x``: along the drawing; ``top``: the height of the
    symbol's top above the floor."""
    over = [f for f in facades if f.band.x0 - EDGE_SLACK <= x <= f.band.x1 + EDGE_SLACK and f.band.eave >= top]
    return min(over, key=lambda f: f.band.eave, default=None)


def place_openings(facades: list[Facade], reads: list[ElevationRead], cfg, titles: dict[int, str]) -> tuple[list, list[str]]:
    """The doors and windows the elevations draw, put on the facades they show: each symbol on the side of the roof its band
    belongs to, along it as far from its end as the elevation draws it (in proportion: the band is the length of the roof's
    side), in the wall (``site_wall`` thick), from the sill to the top the elevation gives above its floor. (Openings,
    notes.) The openings are the program's reading of a drawing that has no plan: the notes say so."""
    from .openings import Opening
    from .sitemode import eave_groups, wall_ring

    openings: list = []
    cut = {"unknown": 0, "small": 0, "outside": 0}
    seen: dict[tuple, Facade] = {}
    for f in sorted(facades, key=lambda f: -f.score):  # a facade two elevations show: the one the cues agree with better
        seen.setdefault((f.building.id, id(f.body), round(f.side.a[0] * 10), round(f.side.a[1] * 10)), f)
    shown = [f for f in facades if any(f is g for g in seen.values())]
    walls: dict[int, BaseGeometry] = {}
    for read in reads:
        mine = [f for f in shown if f.view == read.view.id]
        for s in read.symbols.symbols:
            xc = (s.x0 + s.x1) / 2.0
            z0, z1 = max(s.y0 - read.floor, 0.0), s.y1 - read.floor
            if getattr(s, "arched", False):
                z1 -= (s.x1 - s.x0) / 2.0  # an arch is cut square, at its springing line: an opening is a box
            f = _owner(mine, xc, z1)
            if f is None:
                cut["unknown"] += 1
                continue
            z1 = min(z1, (f.body.eave_height or 0.0) - TOP_CLEAR)
            if z1 - z0 < MIN_HEIGHT:
                cut["small"] += 1
                continue
            ux, uy = f.side.direction
            nx, ny = f.side.normal
            along = min(max((xc - f.band.x0) / f.band.length, 0.0), 1.0) * f.side.length
            inset = f.building.overhang + cfg.site_wall / 2.0
            centre = (f.side.a[0] + ux * along - nx * inset, f.side.a[1] + uy * along - ny * inset)
            if f.building.id not in walls:
                walls[f.building.id] = union(wall_ring(shape, cfg.site_wall) for _, shape in eave_groups(f.building))
            if not walls[f.building.id].buffer(0.02).intersects(Point(centre)):
                cut["outside"] += 1
                continue
            width = s.x1 - s.x0
            op = Opening(s.kind, Polygon(), Polygon(), z0, z1, None, axis=(ux, uy), center=centre, width=width,
                         thickness=cfg.site_wall, building=f.building.id)
            op.rebuild(cfg)
            op.src = {"width": "prospetto", "height": "prospetto", "sill": "prospetto"}
            op.notes.append(f"messa dal prospetto {titles.get(f.view, f.view)}, senza pianta: posizione lungo la facciata in "
                            "proporzione a quella nel prospetto")
            if s.kind == "door":
                op.leaves = _leaves(width)
            elif width > WIDE_WINDOW:
                n = max(2, round(width / SASH_WIDTH))
                op.dividers = [-width / 2 + width * k / n for k in range(1, n)]
            openings.append(op)
    notes = []
    if openings:
        notes.append(f"Aperture posizionate dai prospetti, senza pianta: controlla. {len(openings)} porte e finestre dei prospetti "
                     "sono sulle facciate dei tetti che i prospetti mostrano: la facciata e' dedotta dal confronto dei tetti "
                     "(lunghezza del lato, tetti vicini nel prospetto), la posizione lungo la facciata e' proporzionale a quella nel "
                     "prospetto, le quote sono dalla quota +0,00 in su.")
    if cut["unknown"]:
        notes.append(f"{cut['unknown']} aperture dei prospetti non stanno sotto nessun tetto abbinato a un edificio (loggiati, "
                     "pensiline, edifici senza tetto trovato): non le ho messe.")
    if cut["small"]:
        notes.append(f"{cut['small']} aperture dei prospetti sono sotto la quota 0,00 o sotto la gronda e restano alte meno di "
                     f"{MIN_HEIGHT * 100:.0f} cm: non le ho messe.")
    if cut["outside"]:
        notes.append(f"{cut['outside']} aperture cadono fuori dal muro (angoli, lati che non stanno all'aperto): non le ho messe.")
    return openings, notes


