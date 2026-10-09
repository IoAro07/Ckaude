"""Which facade of the plan does an elevation show? Elevations that are not drawn straight below or above the plan.

An elevation drawn next to the plan, or on another tavola, or rotated, cannot be used by position. But it carries
what a person uses to recognise a facade: the row of windows and doors, with their widths and spacing. This module
compares that row with the openings of the plan along each possible facade and keeps the facade (and the shift of
the drawing along it) where most symbols meet an opening. The sill and the height of the matched openings then come
from the elevation, as they do for an elevation drawn in line with the plan.

Facades are seen from OUTSIDE: standing in front of a facade whose outward normal is n, left to right runs along
r = (-n_y, n_x) (for a south facade, n = (0, -1), r = (1, 0): west to east). The drawing of that facade has its x
running left to right, so a symbol at x_e stands at t = x_e + shift along r. Nothing is assumed about north or about
the orientation of the building: every orientation of the walls that carry openings gives two facades.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from .config import Config
from .elevation import Elevation, Symbol, _horizontal_segments, named_symbols
from .openings import Opening
from .reader import read_items

AXIS_TOLERANCE = math.radians(12.0)  # wall directions closer than this are one orientation
CENTRE_TOLERANCE = 0.25  # m: a symbol and an opening whose centres are this close along the facade can be the same; the
# closer the better (a pair weighs 1 at no distance and 0 at this one, with the square in between)
WIDTH_LESS = 0.30  # an elevation symbol may be this share narrower than the plan opening (a drawn frame vs a clear width)...
WIDTH_MORE = 1.60  # ...and this many times wider plus WIDTH_EXTRA (shutters and jambs drawn in the elevation)
WIDTH_EXTRA = 0.50  # m
MIN_PAIRS = 3  # fewer symbols meeting an opening is a coincidence, not a facade
MIN_SHARE = 0.34  # of the symbols of the elevation (the rest are other storeys, details, openings the plan lacks)
MIN_SIGNIFICANCE = 2.0  # matched pairs (weighted) beyond what chance would give
MIN_MARGIN = 0.6  # the best facade must lead the next one that says something else by this much (3 windows are weak evidence)
SAME_HEIGHT = 0.05  # m: two readings of the same opening this close are the same height
HIDE_DISTANCE = 25.0  # m: an opening with a wall in front of it within this distance does not face the outside
TITLE_BONUS = 0.5  # a title that says the side of the facade (SUD, NORD...) breaks a tie, nothing more

SIDE_NAMES = ("est", "nord", "ovest", "sud")  # outward normal at 0, 90, 180, 270 degrees (x to the east, y to the north)
TITLE_SIDES = {
    "sud": ("SUD", "SOUTH", "MERIDIONALE"),
    "nord": ("NORD", "NORTH", "SETTENTRIONALE"),
    "est": ("EST", "EAST", "ORIENTALE"),
    "ovest": ("OVEST", "WEST", "OCCIDENTALE"),
}
STOREY_WORDS = (  # (storey index, words): which floor a plan view shows
    (0, ("PIANO TERRA", "PIANTA TERRA", "GROUND FLOOR", "PT")),
    (1, ("PIANO PRIMO", "PRIMO PIANO", "FIRST FLOOR", "P1")),
    (2, ("PIANO SECONDO", "SECONDO PIANO", "SECOND FLOOR", "P2")),
    (3, ("PIANO TERZO", "TERZO PIANO", "THIRD FLOOR", "P3")),
    (-1, ("PIANO INTERRATO", "SEMINTERRATO", "BASEMENT", "P-1")),
)


@dataclass
class FacadeOpening:
    opening: Opening
    t: float  # m along the facade, left to right seen from outside
    width: float
    depth: float  # m along the outward normal: the outermost opening of a slot is the one the elevation shows


@dataclass
class Facade:
    normal: tuple[float, float]  # outward, unit
    openings: list[FacadeOpening] = field(default_factory=list)

    @property
    def run(self) -> tuple[float, float]:
        """Left to right, seen from outside."""
        return -self.normal[1], self.normal[0]

    @property
    def side(self) -> str:
        """sud / nord / est / ovest when the facade faces that way within 20 degrees, else 'a N gradi'."""
        angle = math.degrees(math.atan2(self.normal[1], self.normal[0])) % 360.0
        nearest = round(angle / 90.0) % 4
        if abs((angle - nearest * 90.0 + 180.0) % 360.0 - 180.0) <= 20.0:
            return SIDE_NAMES[nearest]
        return f"a {angle:.0f} gradi"

    @property
    def span(self) -> float:
        ts = [f.t for f in self.openings]
        return (max(ts) - min(ts)) if ts else 0.0


@dataclass
class Pair:
    symbol: Symbol
    opening: Opening
    residual: float  # m

    @property
    def weight(self) -> float:
        """1 for a symbol at the very place of the opening and as wide as it, less as it is off or of another width."""
        wide = self.symbol.x1 - self.symbol.x0
        like = min(wide, self.opening.width) / max(wide, self.opening.width, 1e-9)
        return max(0.0, 1.0 - (self.residual / CENTRE_TOLERANCE) ** 2) * (0.6 + 0.4 * like)


@dataclass
class ElevationMatch:
    facade: Facade
    shift: float  # t = x_e + shift
    pairs: list[Pair]
    symbols: int  # symbols considered
    score: float
    margin: float  # score of this reading minus the next best one that says something different
    significance: float
    confidence: str  # alta | media
    by_title: bool = False

    @property
    def matched(self) -> int:
        return len(self.pairs)

    @property
    def residual(self) -> float:
        return sum(abs(p.residual) for p in self.pairs) / len(self.pairs) if self.pairs else 0.0


# --- the facades of the plan ------------------------------------------------------------------

def _faces_out(o: Opening, normal: tuple[float, float], walls: BaseGeometry | None) -> bool:
    """Is there nothing built in front of the opening, on the side the facade looks to? The window of a west wall does not
    face east: the whole house, with its east wall, stands in front of it."""
    if walls is None or walls.is_empty:
        return True
    reach = o.thickness / 2.0 + 0.03  # beyond the outer face of its own wall
    start = (o.center[0] + normal[0] * reach, o.center[1] + normal[1] * reach)
    end = (start[0] + normal[0] * HIDE_DISTANCE, start[1] + normal[1] * HIDE_DISTANCE)
    return not walls.intersects(LineString([start, end]))


def facades_of(openings: list[Opening], walls: BaseGeometry | None = None) -> list[Facade]:
    """Every orientation of the walls that carry openings gives two facades (one for each way the wall may face). The
    openings of a facade are those with nothing built in front of them (``walls``: the walls of the plan; without them
    every opening of the orientation is listed). Where two are at the same place along the facade the outermost is the
    one a symbol is paired with."""
    usable = [o for o in openings if o.kind in ("door", "window") and o.keep and o.width > 0.2]
    angles: list[list[float]] = []  # orientation clusters: [mean angle, weight]
    for o in usable:
        a = math.atan2(o.axis[1], o.axis[0]) % math.pi
        for c in angles:
            if abs((a - c[0] + math.pi / 2) % math.pi - math.pi / 2) <= AXIS_TOLERANCE:
                d = (a - c[0] + math.pi / 2) % math.pi - math.pi / 2
                c[0] = (c[0] + d * 1.0 / (c[1] + 1.0)) % math.pi
                c[1] += 1.0
                break
        else:
            angles.append([a, 1.0])
    out: list[Facade] = []
    for angle, _ in sorted(angles, key=lambda c: -c[1]):
        u = (math.cos(angle), math.sin(angle))
        chosen = [o for o in usable
                  if abs((math.atan2(o.axis[1], o.axis[0]) % math.pi - angle + math.pi / 2) % math.pi - math.pi / 2)
                  <= AXIS_TOLERANCE]
        for sign in (1.0, -1.0):
            n = (-u[1] * sign, u[0] * sign)  # the normal of a wall along u, one way or the other
            f = Facade(n)
            r = f.run
            f.openings = sorted((FacadeOpening(o, o.center[0] * r[0] + o.center[1] * r[1], o.width,
                                               o.center[0] * n[0] + o.center[1] * n[1])
                                 for o in chosen if _faces_out(o, n, walls)), key=lambda fo: fo.t)
            if f.openings:
                out.append(f)
    return out


# --- the match ----------------------------------------------------------------------------------

def _compatible(symbol_width: float, plan_width: float) -> bool:
    return (symbol_width >= plan_width * (1.0 - WIDTH_LESS) - 0.05
            and symbol_width <= plan_width * WIDTH_MORE + WIDTH_EXTRA)


def _pair_up(symbols: list[Symbol], facade: Facade, shift: float) -> list[Pair]:
    """One-to-one pairs at this shift, the best residuals first; of several openings at the same place (one behind the
    other) the outermost is the one the elevation shows."""
    cands: list[tuple[float, int, int]] = []
    for i, s in enumerate(symbols):
        centre = (s.x0 + s.x1) / 2.0 + shift
        for j, fo in enumerate(facade.openings):
            res = centre - fo.t
            if abs(res) > CENTRE_TOLERANCE:
                continue
            if not _compatible(s.x1 - s.x0, fo.width):
                continue
            cands.append((abs(res) - 1e-4 * fo.depth, i, j))  # equal residuals: the outer one wins
    cands.sort()
    used_s: set[int] = set()
    used_o: set[int] = set()
    pairs: list[Pair] = []
    for _, i, j in cands:
        if i in used_s or j in used_o:
            continue
        used_s.add(i)
        used_o.add(j)
        s, fo = symbols[i], facade.openings[j]
        pairs.append(Pair(s, fo.opening, (s.x0 + s.x1) / 2.0 + shift - fo.t))
    return pairs


def _expected(symbols: int, facade: Facade) -> float:
    """What a random drawing would still score: the density of openings along the facade times the window of the match
    (2/3 of a pair on average inside it, because of the weights)."""
    span = max(facade.span + 2.0, 4.0)
    return symbols * min(1.0, len(facade.openings) * 2.0 * CENTRE_TOLERANCE / span) * 2.0 / 3.0


def _best_shift(symbols: list[Symbol], facade: Facade) -> tuple[float, list[Pair]] | None:
    """The shift along the facade that pairs the most symbols with openings (the lowest residuals on a tie)."""
    shifts: list[float] = []
    for s in symbols:
        centre = (s.x0 + s.x1) / 2.0
        for fo in facade.openings:
            if _compatible(s.x1 - s.x0, fo.width):
                shifts.append(fo.t - centre)
    if not shifts:
        return None
    shifts.sort()
    candidates: list[float] = []
    for v in shifts:  # one candidate per cluster of shifts closer than 3 cm
        if not candidates or v - candidates[-1] > 0.03:
            candidates.append(v)
    best: tuple[tuple[float, float], float, list[Pair]] | None = None
    for shift in candidates:
        pairs = _pair_up(symbols, facade, shift)
        if not pairs:
            continue
        # refine on the pairs found (the first shift came from one pair only), then look again
        shift = shift - sum(p.residual for p in pairs) / len(pairs)
        pairs = _pair_up(symbols, facade, shift)
        key = (sum(p.weight for p in pairs), -sum(abs(p.residual) for p in pairs))
        if best is None or key > best[0]:
            best = (key, shift, pairs)
    return (best[1], best[2]) if best else None


def title_side(title: str) -> str | None:
    """sud/nord/est/ovest when the title of a view says it (PROSPETTO SUD, FACCIATA NORD-EST: the first word wins)."""
    words = re.split(r"[^A-Z]+", title.upper())
    for w in words:
        for side, names in TITLE_SIDES.items():
            if w in names:
                return side
    return None


def storey_of(title: str) -> int:
    """The floor a plan view shows, from its title ('PIANTA PIANO PRIMO' -> 1); the ground floor when it does not say."""
    up = " ".join(re.split(r"[^A-Z0-9\-]+", title.upper()))
    for storey, words in STOREY_WORDS:
        if any(re.search(rf"(?<![A-Z0-9]){re.escape(w)}(?![A-Z0-9])", up) for w in words):
            return storey
    return 0


def match_symbols(symbols: list[Symbol], facades: list[Facade], title: str = "") -> ElevationMatch | None:
    """The facade (and the shift) that the symbols of an elevation fit best, or None when no facade fits convincingly."""
    symbols = [s for s in symbols if s.x1 - s.x0 > 0.05]
    if len(symbols) < 1 or not facades:
        return None
    hint = title_side(title) if title else None
    readings: list[ElevationMatch] = []
    for facade in facades:
        if not facade.openings:
            continue
        found = _best_shift(symbols, facade)
        if found is None:
            continue
        shift, pairs = found
        expected = _expected(len(symbols), facade)
        sig = sum(p.weight for p in pairs) - expected
        by_title = hint is not None and facade.side == hint
        score = sig + (TITLE_BONUS if by_title else 0.0)
        readings.append(ElevationMatch(facade, shift, pairs, len(symbols), score, 0.0, sig, "", by_title))
    if not readings:
        return None
    readings.sort(key=lambda m: (-m.score, m.residual))
    best = readings[0]
    if best.matched < MIN_PAIRS or best.significance < MIN_SIGNIFICANCE or best.matched < MIN_SHARE * len(symbols):
        return None
    # the next reading that says something else: another facade whose pairs do not give the same heights
    margin = best.score
    for other in readings[1:]:
        if _same_story(best, other):
            continue
        margin = best.score - other.score
        break
    best.margin = margin
    best.confidence = "alta" if best.matched >= 4 and margin >= 2.0 and best.matched >= 0.6 * len(symbols) else "media"
    if margin < MIN_MARGIN and not best.by_title:
        return None  # two facades fit as well: which one it is cannot be told from the row of windows
    return best


def _same_story(a: ElevationMatch, b: ElevationMatch) -> bool:
    """Do two readings give the same height to the openings they have in common? Then which one is right does not matter."""
    heights = {id(p.opening): (p.symbol.y0, p.symbol.y1) for p in a.pairs}
    common = [(heights[id(p.opening)], (p.symbol.y0, p.symbol.y1)) for p in b.pairs if id(p.opening) in heights]
    if len(common) < max(2, 0.7 * len(a.pairs)):
        return False
    return all(abs(x[0] - y[0]) <= SAME_HEIGHT and abs(x[1] - y[1]) <= SAME_HEIGHT for x, y in common)


# --- from the match to the heights of the openings ---------------------------------------------

def apply_match(match: ElevationMatch, floor: float, cfg: Config, warnings: list[str]) -> int:
    """Set z0/z1 of the paired openings from their symbols (``floor``: the y of the finished floor in the elevation).
    An opening already set from a better elevation is left as it is. Returns how many were set."""
    done = 0
    for p in match.pairs:
        o, s = p.opening, p.symbol
        if o.from_elevation:
            continue
        z0 = max(0.0, s.y0 - floor)
        z1 = min(cfg.wall_height, s.y1 - floor)
        if s.kind == "door" or z0 < 0.05:
            z0 = 0.0
        if z1 - z0 < 0.2:
            continue
        o.z0, o.z1, o.from_elevation = z0, z1, True
        o.src["height"] = "prospetto"
        o.src["sill"] = "prospetto"
        done += 1
    return done


# --- the elevations of a sheet --------------------------------------------------------------------

@dataclass
class ViewSymbols:
    """What an elevation view holds that matters here."""

    symbols: list[Symbol]
    floor: float | None = None  # absolute y (m) of the finished floor of the ground storey
    floor_source: str = ""
    levels: list[float] = field(default_factory=list)  # absolute y of the floors, ascending, when the view shows several
    hlines: list[tuple[float, float, float]] = field(default_factory=list)  # (y, x0, x1) roof silhouette lines
    notes: list[str] = field(default_factory=list)


def view_symbols(doc, cfg: Config, bbox: tuple[float, float, float, float], unit: str,
                 unit_scale: float) -> ViewSymbols:
    """The doors and windows of an elevation view (``bbox`` in drawing units), the floor level and the roof lines."""
    items = read_items(doc, cfg, area=tuple(bbox), ignore_veto=True, keep_other=True, unit=unit).items
    symbols = named_symbols(items)
    doors = [s.y0 for s in symbols if s.kind == "door"]
    return ViewSymbols(symbols, min(doors) if doors else None, "porta" if doors else "", [],
                       _horizontal_segments(items))


def _inside(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float], slack: float) -> bool:
    return (inner[0] >= outer[0] - slack and inner[1] >= outer[1] - slack
            and inner[2] <= outer[2] + slack and inner[3] <= outer[3] + slack)


def _touches(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def _storey_band(vs: ViewSymbols, storey: int) -> tuple[list[Symbol], float | None, str]:
    """The symbols that belong to the storey of the plan, and the y of its floor. An elevation that shows several storeys
    gives each its band (from its floor to the next one); one that does not says nothing about the upper floors."""
    if vs.levels and 0 <= storey < len(vs.levels):
        floor = vs.levels[storey]
        top = vs.levels[storey + 1] if storey + 1 < len(vs.levels) else math.inf
        band = [s for s in vs.symbols if floor - 0.2 <= s.y0 < top - 0.2]
        return band, floor, vs.floor_source if storey == 0 else "quota del piano"
    if storey > 0:
        return [], None, ""
    return vs.symbols, vs.floor, vs.floor_source


def _roof_elevation(match: ElevationMatch, vs: ViewSymbols, floor: float) -> Elevation | None:
    """Silhouette lines of a south or north elevation, in plan x, for the ridges of the roof. (East and west elevations
    show the ridges end on: they say nothing about their length.)"""
    nx, ny = match.facade.normal
    if abs(nx) > 0.05 or abs(ny) < 0.99 or not vs.hlines:
        return None
    south = ny < 0
    hlines = []
    for y, x0, x1 in vs.hlines:
        a, b = x0 + match.shift, x1 + match.shift  # along the facade, left to right seen from outside
        hlines.append((y, a, b) if south else (y, -b, -a))
    return Elevation("south" if south else "north", floor, vs.floor_source, [], hlines)


def match_views(doc, cfg: Config, analysis, openings: list[Opening], walls: BaseGeometry, unit: str,
                unit_scale: float, taken: list[tuple[float, float, float, float]],
                warnings: list[str]) -> tuple[list[Elevation], list[dict]]:
    """Use the elevations of the sheet that are not drawn in line with the plan: each is matched to the facade of the
    plan it shows, and the sill and the height of the paired openings are set from its symbols. ``taken``: the zones
    (drawing units) of elevations already read in line with the plan. Returns the elevations (for the roof) and the
    report lines."""
    plan = analysis.plan
    if plan is None:
        return [], []
    candidates = [v for v in analysis.views if v.kind == "elevation" and v is not plan
                  and not _inside(v.bbox, plan.bbox, 1.0 / unit_scale) and not any(_touches(v.bbox, z) for z in taken)]
    if not candidates:
        return [], []
    facades = facades_of(openings, walls)
    storey = storey_of(" ".join(plan.titles))
    found: list[tuple[ElevationMatch, ViewSymbols, float, object, int]] = []
    for v in candidates:
        label = f"Prospetto della vista {v.id}" + (f" ({v.titles[0]})" if v.titles else "")
        if not facades:
            v.matched = "nessuna apertura in pianta da confrontare"
            continue
        vs = view_symbols(doc, cfg, v.bbox, unit, unit_scale)
        band, floor, source = _storey_band(vs, storey)
        if not band:
            v.matched = "nessun simbolo di porta o finestra riconosciuto"
            warnings.append(f"{label}: nessuna porta o finestra riconosciuta"
                            + (" per il piano della pianta" if storey else "") + ".")
            continue
        match = match_symbols(band, facades, " ".join(v.titles))
        if match is None:
            v.matched = f"nessuna facciata corrisponde ({len(band)} simboli)"
            warnings.append(f"{label}: la fila delle sue {len(band)} aperture non corrisponde a nessuna facciata della "
                            "pianta (disegnata in un'altra scala, o non e' una facciata, o la pianta ha aperture diverse).")
            continue
        if floor is None:
            v.matched = f"facciata {match.facade.side}, ma senza la quota del pavimento"
            warnings.append(f"{label}: e' la facciata {match.facade.side} ({match.matched} aperture abbinate) ma non ho "
                            "trovato la quota del pavimento: indica '+0,00' nel prospetto o disegna una porta.")
            continue
        found.append((match, vs, floor, source, v.id))
        v.matched = f"facciata {match.facade.side}, {match.matched} di {len(band)}"
    elevations: list[Elevation] = []
    report: list[dict] = []
    for match, vs, floor, source, view_id in sorted(found, key=lambda f: -f[0].score):
        done = apply_match(match, floor, cfg, warnings)
        roof = _roof_elevation(match, vs, floor)
        if roof is not None:
            elevations.append(roof)
        report.append({"side": match.facade.side, "label": match.facade.side, "view": view_id, "matched": done,
                       "total": match.symbols, "zero_source": source or "indicata",
                       "confidence": match.confidence, "residual": match.residual, "title_hint": match.by_title})
    return elevations, report
