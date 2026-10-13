"""Storeys stacked into one model.

A house of several floors is often drawn on one sheet as a plan view per floor (PIANTA PIANO TERRA, PIANTA PIANO PRIMO),
side by side. The tool converts one plan view per run; this module finds the other floors of the same building among the
views of the sheet, lays each over the converted plan (the outline of its walls over the outline of the floor below) and
says at which height each one stands. The pipeline converts every floor with the usual machinery (walls, openings,
floors...) and puts the meshes together, every group under the name of its storey (P0_Muri, P1_Muri, P1_Telai_F01...).

Nothing here guesses on weak evidence: a floor is stacked only when its view says which storey it is (a title, or the
title of a copy of it) and its outline registers on the plan below; whatever is left out is said in the report.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, TYPE_CHECKING

from shapely import affinity
from shapely.geometry.base import BaseGeometry

from .autodetect import Analysis, View, _other_state
from .elevmatch import storey_stated
from .geom import polygons_of, union
from .mesh import Mesh, Slab, storey_tag
from .model import building_footprint, floor_footprint
from .roofview import Registration, fit_roof

if TYPE_CHECKING:
    from .openings import Opening

SIZE_RATIO = (0.5, 2.0)  # a floor of the same building is about as wide and as deep as the converted plan (this share, at most)
MIN_IOU = 0.60  # the filled outline of a floor overlaps the one below at least this well (storeys have balconies and porticoes)
FORCED_IOU = 0.45  # ... with --piani (the user says they are floors of one building) at least this well
MIN_COVER = 0.70  # ... and this share of the floor stands over the one below (grown by what it may overhang)
DEFAULT_SLAB = 0.30  # m: the slab between two storeys when the sections are not read and --spessore-solaio is not given
MAX_STOREYS = 6  # no more floors than this are stacked


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


def storey_of_view(views: list[View], v: View) -> tuple[int | None, str]:
    """(the storey the view shows, what says so): from its own title, else from the titles of its copies and of the
    drawings they stand in (a plan repeated in the site plan, which is called PIANTA PIANO TERRA); None when nothing says
    it or the titles disagree."""
    by_id = {w.id: w for w in views}
    found: dict[int, str] = {}
    for w in _copy_group(views, v):
        titled = [w, by_id.get(w.parent)] if w.parent in by_id else [w]
        for t in titled:
            for title in (t.titles if t is not None else []):
                storey = storey_stated(title)
                if storey is not None:
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
    (ax, ay), (bx, by) = ((v.bbox[0] + v.bbox[2]) / 2, (v.bbox[1] + v.bbox[3]) / 2) for v in (a, b)
    return math.hypot(ax - bx, ay - by)


def find_storeys(a: Analysis) -> Stack | None:
    """The floors above the converted plan among the views of the sheet, or None when the sheet shows no other floor of
    its building. A candidate is a plan view of about the size of the plan that says which storey it is, is neither a copy
    of the plan nor another state of the building (existing, project) nor drawn inside a bigger view; of the copies of one
    floor the one nearest to the plan is taken (the calculation sheet repeats the floors far from the drawings). The
    storeys must be consecutive: a PIANO SECONDO without a PIANO PRIMO is not stacked."""
    plan = a.plan
    if plan is None or plan.kind != "plan":
        return None
    scale = a.unit.scale
    mine = {v.id for v in _copy_group(a.views, plan)}
    base_index, how = storey_of_view(a.views, plan)
    stack = Stack(0 if base_index is None else base_index, how or "nessun titolo: presumo il piano terra")
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
    return stack if stack.candidates else None


def stacking_on(cfg, a: Analysis) -> bool:
    """Is stacking on for this run? Not when the user said no (--no-piani), when the layers name the storeys (P1_, P2_: the
    other way of drawing several floors), when this conversion is a storey of a stack already, or when the user chose
    the view or the area (--vista, --area) unless --piani says the views are floors of one building."""
    if cfg.stacking == "no" or cfg.part or a.plan is None:
        return False
    user = cfg.user if cfg.user is not None else cfg
    if cfg.floor is not None:
        return False
    if cfg.stacking == "yes":
        return True
    return user.view is None and user.area is None


def outline(solid: BaseGeometry) -> BaseGeometry:
    """The outer outline of the walls of a floor, filled: the building and what its walls enclose."""
    return union([building_footprint(solid), floor_footprint(solid)])


def register(upper: BaseGeometry, below: BaseGeometry, forced: bool) -> tuple[Registration | None, str]:
    """(the move that lays the outline of a floor over the one below, '') or (None, why not). Only a shift counts: a floor drawn
    turned or mirrored is not stacked (its openings and its elevations would have to turn too). The move is ``reg.shift`` in
    metres, from the frame of the upper plan to the one of the plan below."""
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
    return reg, ""


def key_of(o: "Opening") -> str:
    """The name of an opening in a model of several storeys: P1_F03 (the sigla alone is the same on every floor); the
    sigla itself in a model of one."""
    return o.id if o.storey is None else f"{storey_tag(o.storey)}_{o.id}"


def levels_of(heights: list[float], to_next: list[bool], slab: float) -> list[float]:
    """The height of the finished floor of every storey, the lowest at 0. ``heights``: the walls of each storey; ``to_next[i]``:
    that height already reaches the floor of the storey above (the elevation gave the height between two floors, which holds
    the slab); else the slab is built on top of the walls."""
    levels = [0.0]
    for height, whole in zip(heights[:-1], to_next):
        levels.append(levels[-1] + height + (0.0 if whole else slab))
    return levels


@dataclass
class Floor:
    """A storey of the stack: what it is, where it stands and what its plan gave. The report of the run lists them, the
    converted plan first."""

    index: int
    view: int  # the number of its view on the sheet
    how: str  # what says which storey it is
    shift: tuple[float, float] = (0.0, 0.0)  # m: from the frame of its plan to the frame of the converted plan
    iou: float | None = None  # how well its outline overlays the one below (None: it is the converted plan)
    level: float = 0.0  # m: its finished floor
    height: float = 0.0  # m: its walls
    height_source: str = "predefinita"
    slab: float = 0.0  # m: the slab under it (0: none)
    walls_m2: float = 0.0
    pieces: int = 0
    doors: int = 0
    windows: int = 0
    passages: int = 0
    columns: int = 0
    report: object | None = None  # the ConversionReport of an upper storey (the converted plan's own is the run's)


def _slab_footprint(plan, slab: float) -> BaseGeometry:
    """What the slab under a storey covers that the floors of its rooms do not: the walls and the gaps between rooms."""
    solid = plan.solid_walls
    floors = union(g for _, g in plan.floors) if plan.floors else floor_footprint(solid)
    rest = outline(solid).difference(floors)
    return union(p for p in polygons_of(rest.buffer(-slab / 10.0).buffer(slab / 10.0)) if p.area > 1e-4)


def stack_mesh(base: Mesh, floors: list[Floor], is_site: Callable[[str], bool]) -> Mesh:
    """The model of the stack: the mesh of every storey moved to its place (plan shift, height of its floor), its groups under
    the name of the storey (P1_Muri...) except the ground and the garden, which belong to the site, and under every upper
    storey the slab that the floors of its rooms do not cover."""
    mesh = Mesh()
    for fl in floors:
        tag = storey_tag(fl.index)
        source = base if fl.report is None else fl.report.mesh
        mesh.add_mesh(source, (fl.shift[0], fl.shift[1], fl.level),
                      lambda name, tag=tag: name if is_site(name) else f"{tag}_{name}")
        if fl.report is not None and fl.slab > 0.0:
            rest = affinity.translate(_slab_footprint(fl.report.plan, fl.slab), *fl.shift)
            if not rest.is_empty:
                mesh.add_extrusion(f"{tag}_Solaio", [Slab(fl.level - fl.slab, fl.level, rest)])
    return mesh
