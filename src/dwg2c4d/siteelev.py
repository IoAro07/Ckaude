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

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from .geom import polygons_of

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


def side_lengths(shape: BaseGeometry) -> list[float]:
    """The lengths of the straight sides of the outline of a roof: pieces that run the same way, one after the other, are one."""
    lengths = []
    for poly in polygons_of(shape):
        pts = list(poly.simplify(SIMPLIFY).exterior.coords)[:-1]
        sides = [(pts[k], pts[(k + 1) % len(pts)]) for k in range(len(pts))]
        runs: list[list[float]] = []  # [length, direction in degrees]
        for a, b in sides:
            d = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0
            if runs and min(abs(runs[-1][1] - d), 180.0 - abs(runs[-1][1] - d)) <= STRAIGHT:
                runs[-1][0] += math.dist(a, b)
            else:
                runs.append([math.dist(a, b), d])
        if len(runs) > 1 and min(abs(runs[0][1] - runs[-1][1]), 180.0 - abs(runs[0][1] - runs[-1][1])) <= STRAIGHT:
            runs[0][0] += runs.pop()[0]
        lengths.extend(r[0] for r in runs if r[0] >= MIN_SIDE)
    return lengths


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
