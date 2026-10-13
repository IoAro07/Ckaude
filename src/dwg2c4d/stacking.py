"""Storeys stacked into one model.

A house of several floors is often drawn on one sheet as a plan view per floor (PIANTA PIANO TERRA, PIANTA PIANO PRIMO),
side by side. The tool converts one plan view per run; this module finds the other floors of the same building among the
views of the sheet, lays each over the plan below it (the outline of its walls over the outline of the floor below) and
says at which height each one stands. The pipeline converts every floor with the usual machinery (walls, openings,
floors...), puts them at their heights and builds the mesh of the stack, every group under the name of its storey
(P0_Muri, P1_Muri, P1_Telai_F01...).

Nothing here guesses on weak evidence: a floor is stacked only when its view says which storey it is (a title, or the
title of a copy of it) and its outline registers on the plan below; whatever is left out is said in the report.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field, replace
from typing import Sequence

import numpy as np
import shapely
from scipy.signal import fftconvolve
from shapely import affinity
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from .autodetect import Analysis, View, _other_state
from .config import Config
from .elevmatch import storey_stated
from .export import is_site_group
from .geom import union
from .mesh import Mesh, Slab, storey_tag
from .model import Plan, add_roofs, build_mesh, building_footprint, floor_footprint
from .openings import Opening
from .roofview import Registration, fit_roof

SIZE_RATIO = (0.5, 2.0)  # a floor of the same building is about as wide and as deep as the converted plan (this share, at most)
MIN_IOU = 0.60  # the filled outline of a floor overlaps the one below at least this well (storeys have balconies and porticoes)
FORCED_IOU = 0.45  # ... with --piani (the user says they are floors of one building) at least this well
MIN_COVER = 0.70  # ... and this share of the floor stands over the one below (grown by what it may overhang)
DEFAULT_SLAB = 0.30  # m: the slab between two storeys when the elevations do not draw it and --spessore-solaio is not given
MAX_STOREYS = 6  # no more floors than this are stacked
RISE_RANGE = (2.0, 5.0)  # m: a height from one floor to the next that makes sense (the elevations may say otherwise: ignored)
MIN_WALL = 1.8  # m: the walls of a storey are not lower than this once the slab is taken off the rise
LEVEL_TOLERANCE = 0.05  # m: two readings of a floor level this close are the same one
SLAB_TOLERANCE = 0.03  # m: two elevations draw the same slab if its thickness differs by less than this
WALL_STEP = 0.05  # m: cell of the grid on which the walls of a floor are laid over the walls below
WALL_GROW = 0.03  # m: the walls below are grown by this when the overlay is counted (the drawing is not exact)
SNAP_RADIUS = 5.0  # m: the walls move a floor this far from the shift of the outlines, at most...
SNAP_IOU = 0.03  # ... among the shifts at which the outlines overlap this much less than at best, at most
MIN_PACE = 0.005  # m: the exact search for the shift stops at this step
MAX_GRID = 4_000_000  # no grid of more cells than this is made (the cell grows instead)


@dataclass
class Candidate:
    """A plan view that is a floor above the converted plan."""

    index: int  # 0 ground floor, 1 first floor...
    view: View
    how: str  # what says so, for the report


@dataclass
class Stack:
    base_index: int  # the storey of the converted plan
    base_how: str
    candidates: list[Candidate] = field(default_factory=list)  # the floors above it, one per storey, consecutive
    notes: list[str] = field(default_factory=list)


def _copy_group(views: list[View], v: View) -> list[View]:
    """The view and the views that are copies of it (or that it is a copy of): the same drawing, repeated."""
    linked: dict[int, set[int]] = {w.id: set() for w in views}
    for w in views:
        if w.copy_of in linked:
            linked[w.id].add(w.copy_of)
            linked[w.copy_of].add(w.id)
    seen: set[int] = set()
    todo = [v.id]
    while todo:
        i = todo.pop()
        if i not in seen:
            seen.add(i)
            todo.extend(linked[i])
    return [w for w in views if w.id in seen]


def _stated(by_id: dict[int, View], w: View) -> dict[int, str]:
    """{storey: title} the titles of a view, and of the bigger drawing it stands in, name."""
    found: dict[int, str] = {}
    for t in [w, by_id[w.parent]] if w.parent in by_id else [w]:
        for title in t.titles:
            storey = storey_stated(title)
            if storey is not None:
                found.setdefault(storey, title)
    return found


def storey_of_view(views: list[View], v: View) -> tuple[int | None, str]:
    """(the storey the view shows, what says so): from its own title (or the one of the drawing it stands in), else from
    the titles of its copies (a plan repeated in the site plan, which is called PIANTA PIANO TERRA); None when nothing says
    it or the titles disagree."""
    by_id = {w.id: w for w in views}
    found = _stated(by_id, v)
    for w in _copy_group(views, v) if not found else []:
        for storey, title in _stated(by_id, w).items():
            found.setdefault(storey, title)
    if len(found) != 1:
        return None, ""
    storey, title = next(iter(found.items()))
    return storey, f"il titolo '{title}'"


def _size_fits(base: View, other: View, scale: float) -> bool:
    """Is the view about as big as the plan (the same scale): each side within ``SIZE_RATIO`` of the plan's, either way round?"""
    (bw, bh), (ow, oh) = base.size_m(scale), other.size_m(scale)
    lo, hi = SIZE_RATIO

    def near(a: float, b: float) -> bool:
        return b > 0 and lo <= a / b <= hi

    return (near(ow, bw) and near(oh, bh)) or (near(ow, bh) and near(oh, bw))


def _distance(a: View, b: View) -> float:
    (ax, ay), (bx, by) = [((v.bbox[0] + v.bbox[2]) / 2, (v.bbox[1] + v.bbox[3]) / 2) for v in (a, b)]
    return math.hypot(ax - bx, ay - by)


def find_storeys(a: Analysis) -> Stack | None:
    """The floors above the converted plan among the views of the sheet (``Stack.candidates``, none when the sheet shows no
    other floor of its building), with the remarks about floors it cannot use; None when there is no plan. A candidate is a
    plan view of about the size of the plan that says which storey it is, is neither a copy of the plan (unless its own title
    names another storey) nor another state of the building (existing, project) nor drawn inside a bigger view; of the copies
    of one floor the one nearest to the plan is taken (the calculation sheet repeats the floors far from the drawings). The
    storeys must be consecutive: a PIANO SECONDO without a PIANO PRIMO is not stacked."""
    plan = a.plan
    if plan is None or plan.kind != "plan":
        return None
    scale = a.unit.scale
    base_index, how = storey_of_view(a.views, plan)
    if base_index is None or base_index < 0:
        base_index, how = 0, ""
    by_id = {w.id: w for w in a.views}
    # the copies of the plan are the plan again, unless their own title names another storey (two floors drawn alike)
    mine = {w.id for w in _copy_group(a.views, plan) if set(_stated(by_id, w)) <= {base_index}}
    stack = Stack(base_index, how or "nessun titolo: presumo il piano terra")
    pool: dict[int, list[tuple[View, str]]] = {}
    for v in a.views:
        if v.kind != "plan" or v.id in mine or v.parent is not None or _other_state(plan, v) or not _size_fits(plan, v, scale):
            continue
        index, why = storey_of_view(a.views, v)
        if index is not None:
            pool.setdefault(index, []).append((v, why))
    wanted = stack.base_index + 1
    while wanted in pool and len(stack.candidates) < MAX_STOREYS:
        view, why = min(pool[wanted], key=lambda c: _distance(plan, c[0]))
        stack.candidates.append(Candidate(wanted, view, why))
        wanted += 1
    later = sorted(i for i in pool if i >= wanted)
    if later and stack.candidates:
        stack.notes.append(f"Il foglio ha anche la pianta del piano {later[0]} (vista {pool[later[0]][0][0].id}) ma manca "
                           f"quella del piano {wanted}: non l'ho impilata.")
    elif later:
        stack.notes.append(f"Il foglio ha la pianta del piano {later[0]} (vista {pool[later[0]][0][0].id}) ma non quella del "
                           f"piano {wanted}, che viene subito sopra la vista {plan.id}: non l'ho impilata.")
    return stack


def stacking_on(cfg, a: Analysis | None, layer_storeys: bool) -> bool:
    """Is stacking on for this run? Not when the user said no (--no-piani), when the layers name the storeys (P1_, P2_: the
    other way of drawing several floors), when this conversion is a storey of a stack already, when the sheet was not analysed,
    or when the user chose the view or the area (--vista, --area) unless --piani says the views are floors of one building."""
    if cfg.stacking == "no" or cfg.part or a is None or a.plan is None or layer_storeys or cfg.floor is not None \
            or cfg.user is None:  # (the other storeys are converted from the options as the user gave them)
        return False
    return cfg.stacking == "yes" or (cfg.user.view is None and cfg.user.area is None)


def outline(solid: BaseGeometry) -> BaseGeometry:
    """The outer outline of the walls of a floor, filled: the building and what its walls enclose."""
    return union([building_footprint(solid), floor_footprint(solid)])


def register(upper: BaseGeometry, below: BaseGeometry, forced: bool, upper_walls: BaseGeometry | None = None,
             below_walls: BaseGeometry | None = None) -> tuple[Registration | None, str]:
    """(the move that lays the outline of a floor over the one below, '') or (None, why not). Only a shift counts: a floor drawn
    turned or mirrored is not stacked (its openings and its elevations would have to turn too). The move is ``reg.shift`` in
    metres, from the frame of the upper plan to the one of the plan below. With the walls of both (``upper_walls``,
    ``below_walls``) the move is then made exact: a smaller floor over a bigger one fits the outline as well at many shifts, and
    the one that lays most wall over wall is the one."""
    reg = fit_roof(upper, below)
    floor = FORCED_IOU if forced else MIN_IOU
    if reg is None:
        return None, "nessun contorno da confrontare"
    if reg.turns or reg.mirror:
        return None, (f"il suo contorno combacia meglio con la pianta sotto ruotato di {reg.turns * 90} gradi"
                      f"{' e specchiato' if reg.mirror else ''} che com'e': non lo impilo")
    if reg.iou < floor or reg.cover < MIN_COVER:
        return None, (f"il contorno dei suoi muri combacia con quello del piano sotto solo per il {reg.iou * 100:.0f} % "
                      f"(ce ne vuole almeno {floor * 100:.0f} %)")
    if upper_walls is not None and below_walls is not None:
        reg = snap_to_walls(reg, upper, below, upper_walls, below_walls)
    return reg, ""


def _cells(shape: BaseGeometry, x0: float, y0: float, nx: int, ny: int, step: float) -> np.ndarray:
    """``shape`` on a grid of ``ny`` x ``nx`` cells of ``step`` metres whose lower left corner is (x0, y0)."""
    gx, gy = np.meshgrid(x0 + (np.arange(nx) + 0.5) * step, y0 + (np.arange(ny) + 0.5) * step)
    return shapely.contains_xy(shape, gx, gy).astype(np.float32)


def snap_to_walls(reg: Registration, upper: BaseGeometry, below: BaseGeometry, upper_walls: BaseGeometry,
                  below_walls: BaseGeometry) -> Registration:
    """The shift near the one of the outlines that lays most of the walls of the floor over the walls below. Both are drawn on
    a grid and the overlap is worked out for every shift at once (a correlation), for the outlines and for the walls; among the
    shifts within ``SNAP_RADIUS`` of the outlines' own at which the outlines overlap nearly as well as at the best
    (``SNAP_IOU``) the walls choose, the nearest to the outlines' own on a tie; the exact shift is then looked for around it."""
    ux0, uy0, ux1, uy1 = upper.bounds
    bx0, by0, bx1, by1 = below.bounds
    step = WALL_STEP
    while ((bx1 - bx0) / step) * ((by1 - by0) / step) > MAX_GRID:  # a very big plan: coarser cells
        step *= 2.0
    fx0, fy0 = bx0 - step, by0 - step
    fnx, fny = int(math.ceil((bx1 - bx0) / step)) + 3, int(math.ceil((by1 - by0) / step)) + 3
    rnx, rny = int(math.ceil((ux1 - ux0) / step)) + 1, int(math.ceil((uy1 - uy0) / step)) + 1
    fixed = {"outline": _cells(below, fx0, fy0, fnx, fny, step),
             "walls": _cells(below_walls.buffer(WALL_GROW), fx0, fy0, fnx, fny, step)}
    moving = {"outline": _cells(upper, ux0, uy0, rnx, rny, step), "walls": _cells(upper_walls, ux0, uy0, rnx, rny, step)}
    corr = {k: fftconvolve(fixed[k], moving[k][::-1, ::-1], mode="full") for k in fixed}
    both = corr["outline"]
    iou = np.where(both > 0.5, both / np.maximum(float(fixed["outline"].sum()) + float(moving["outline"].sum()) - both, 1.0), 0.0)
    rows, cols = np.indices(iou.shape)
    sx = fx0 - ux0 + (cols - (rnx - 1)) * step  # the cell of the corner of the floor over the corner of the grid below
    sy = fy0 - uy0 + (rows - (rny - 1)) * step
    away = np.hypot(sx - reg.shift[0], sy - reg.shift[1])
    near = away <= SNAP_RADIUS
    if not near.any():
        return reg
    ok = near & (iou >= iou[near].max() - SNAP_IOU)
    k = int(np.argmax(np.where(ok, corr["walls"] - away * 1e-3, -np.inf)))
    dx, dy = float(sx.flat[k]), float(sy.flat[k])
    grown = below_walls.buffer(WALL_GROW)

    def over(x: float, y: float) -> float:
        return affinity.translate(upper_walls, x, y).intersection(grown).area

    best, pace = over(dx, dy), step
    while pace > MIN_PACE:  # exact, around the cell found
        for ex, ey in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            value = over(dx + ex * pace, dy + ey * pace)
            if value > best + 1e-9:
                best, dx, dy = value, dx + ex * pace, dy + ey * pace
                break
        else:
            pace /= 2.0
    return replace(reg, shift=(dx, dy))


@dataclass
class Floor:
    """A storey of the stack: what it is, where it stands and what its plan gave. The report of the run lists them, the
    converted plan first."""

    index: int
    view: int  # the number of its view on the sheet
    how: str  # what says which storey it is
    plan: Plan
    cfg: Config  # the one it was converted with (wall_height: the height it was converted with, then the one it is built with)
    openings: list[Opening]
    shift: tuple[float, float] = (0.0, 0.0)  # m: from the frame of its plan to the frame of the converted plan
    iou: float | None = None  # how well its outline overlays the one below (None: it is the converted plan)
    level: float = 0.0  # m: its finished floor above the finished floor of the converted plan
    height_source: str = "predefinita"
    slab: float = 0.0  # m: the slab under it (0: none)
    report: object | None = None  # the ConversionReport of an upper storey (the converted plan's own is the run's)

    @property
    def height(self) -> float:
        return self.cfg.wall_height

    @height.setter
    def height(self, value: float) -> None:
        self.cfg = replace(self.cfg, wall_height=value)


def consensus_levels(readings: list[list[float]]) -> list[float]:
    """The heights of the floors above the ground floor [0, 3.2, ...] the elevations agree on: for every storey the value most
    of the readings that show it give (within ``LEVEL_TOLERANCE``); an elevation that shows fewer storeys does not contradict
    one that shows more. Where two heights have as many readings the elevations do not agree, and the list stops there (empty:
    no floor above the ground is known)."""
    levels = [0.0]
    for i in range(1, max((len(r) for r in readings), default=0)):
        values = [r[i] for r in readings if len(r) > i]
        groups = [[v for v in values if abs(v - c) <= LEVEL_TOLERANCE] for c in values]
        best = max(groups, key=len)
        if len({round(statistics.median(g), 2) for g in groups if len(g) == len(best)}) > 1:
            break  # two heights have as many votes: the elevations do not agree
        levels.append(statistics.median(best))
    return levels if len(levels) > 1 else []


def consensus_slabs(readings: list[list[float | None]]) -> list[float | None]:
    """The thickness of the slab under every floor above the ground (indexed by the storey) that most of the elevations that
    show that floor draw alike (within ``SLAB_TOLERANCE``); None where they do not say or disagree."""
    out: list[float | None] = []
    for i in range(max((len(r) for r in readings), default=0)):
        shown = [r[i] for r in readings if len(r) > i]
        values = [v for v in shown if v is not None]
        groups = [[v for v in values if abs(v - c) <= SLAB_TOLERANCE] for c in values]
        best = max(groups, key=len, default=[])
        out.append(statistics.median(best) if best and len(best) * 2 > len(shown) else None)
    return out


def place_storeys(floors: list[Floor], marks: list[float], slab: float, stated_height: bool,
                  slabs: Sequence[float | None] = ()) -> list[str]:
    """Set the level, the walls' height and the slab of every floor (``floors``: lowest first, ``height`` holding what its
    own conversion gave); returns the notes for the report. ``marks``: the heights of the floors of the storeys above the
    ground floor, from the elevations (indexed by the number of the storey). A storey's walls reach the underside of the slab
    under the next one: the rise to the next floor less the slab when the elevations give the floors, else the walls it
    was converted with, and the slab on top of them. The top storey keeps its own height. ``slab``: the thickness of the slabs
    (the user's, or the default); ``slabs``: what the elevations draw (indexed by the storey), used where ``slab`` is the
    default; ``stated_height``: the user gave the height of the walls (--altezza-muri): then the elevations do not decide it."""
    notes: list[str] = []
    for k, fl in enumerate(floors):
        fl.slab = 0.0 if k == 0 else (slabs[fl.index] if fl.index < len(slabs) and slabs[fl.index] else slab)
        if k and fl.slab != slab:
            notes.append(f"Piano {fl.index}: solaio di {fl.slab * 100:.0f} cm sotto, letto nei prospetti (le due linee del suo bordo).")
    level = 0.0
    for fl, nxt in zip(floors, floors[1:]):
        fl.level = level
        rise = None
        if not stated_height and fl.index < len(marks) and nxt.index < len(marks):
            rise = marks[nxt.index] - marks[fl.index]
        if rise is not None and RISE_RANGE[0] <= rise <= RISE_RANGE[1] and rise - nxt.slab >= MIN_WALL:
            fl.height, fl.height_source = rise - nxt.slab, "prospetto"
            notes.append(f"Piano {fl.index}: i prospetti danno il piano sopra a {rise:.2f} m: i muri arrivano sotto il solaio "
                         f"({rise - nxt.slab:.2f} m, solaio di {nxt.slab * 100:.0f} cm).")
        else:
            rise = fl.height + nxt.slab
        level += rise
    floors[-1].level = level
    return notes


def slab_pieces(solid: BaseGeometry, floors: BaseGeometry, level: float, slab: float, finish: float) -> list[Slab]:
    """The slab under a storey, in the frame of the converted plan: over the outline of its walls (and what they enclose),
    from ``level - slab`` to ``level``. ``floors``: where the floors of the rooms lie (built with the storey, ``finish`` thick
    under ``level``): the slab stops under them and goes on to the level where they are not (under the walls, between rooms)."""
    full = outline(solid)
    top = min(finish, slab) if finish > 0 and floors.area > 1e-4 else 0.0
    if top <= 0.0:
        return [Slab(level - slab, level, full)]
    pieces = [Slab(level - slab, level - top, full)]
    rest = full.difference(floors)
    if rest.area > 1e-4:
        pieces.append(Slab(level - top, level, rest))
    return pieces


def moved(geom: BaseGeometry, shift: tuple[float, float]) -> BaseGeometry:
    """``geom`` of a storey in the frame of the converted plan."""
    return affinity.translate(geom, *shift)


def fit_openings(floors: list[Floor]) -> None:
    """No lintel stands higher than the walls it is in: the walls of a storey now end under the slab of the next one."""
    for fl in floors:
        for o in fl.openings:
            o.z1 = min(o.z1, fl.height)


def _floor_shape(fl: Floor) -> BaseGeometry:
    """Where the floors of the rooms of a storey lie (as ``model.build_mesh`` lays them): empty with no floor at all."""
    if fl.cfg.floor_thickness <= 0:
        return Polygon()
    if fl.plan.floors:
        return union(g for _, g in fl.plan.floors)
    return floor_footprint(fl.plan.solid_walls)


def stack_mesh(floors: list[Floor], base: Mesh, roof_plan: Plan | None, roof_cfg: Config, warnings: list[str]) -> Mesh:
    """The model of the stack: the mesh of every storey moved to its place (plan shift, height of its floor), its groups under
    the name of the storey (P1_Muri...) except the garden, which belongs to the lot, the slab under every upper storey and
    the roof on the top one, its eaves at ``roof_cfg.wall_height``. ``base`` is the mesh of the converted plan (``floors[0]``);
    the others are built here."""
    mesh = Mesh()
    for k, fl in enumerate(floors):
        tag = storey_tag(fl.index)
        own: list[str] = []
        source = base if k == 0 else build_mesh(fl.plan, fl.cfg, own, roof=False)
        warnings.extend(f"Piano {fl.index}: {w}" for w in own)
        mesh.add_mesh(source, (fl.shift[0], fl.shift[1], fl.level),
                      lambda name, tag=tag: name if is_site_group(name) else f"{tag}_{name}")
        if fl.slab > 0:
            pieces = slab_pieces(moved(fl.plan.solid_walls, fl.shift), moved(_floor_shape(fl), fl.shift), fl.level, fl.slab,
                                 fl.cfg.floor_thickness)
            mesh.add_extrusion(f"{tag}_Solaio", pieces)
    if roof_plan is not None and (roof_plan.roof is not None or roof_plan.flat_roof is not None):
        roofs = Mesh()
        add_roofs(roofs, roof_plan, roof_cfg, roof_cfg.wall_height)
        top = storey_tag(floors[-1].index)
        mesh.add_mesh(roofs, rename=lambda name: f"{top}_{name}")
    return mesh
