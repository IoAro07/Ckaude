"""The steps of the stairs outside the building: treads and risers built from the rows of step lines the plan draws.

A stair is drawn as a row of equal parallel lines a tread apart (``falsewalls`` finds them and keeps them out of the walls, where
they would pair up into a solid block as high as the walls). Here a stair that stands outside the building is built as
what it is: ``count`` steps, each one riser higher than the one before, one object (``Sito_Gradini_01``) with its own material.

What the drawing does not say is guessed, and the report says it:

* The riser. The stair climbs ``count`` risers; when the sheet knows the level of a storey above the floor and that level is
  ``count`` risers of a plausible height (``RISE_RANGE``), the stair reaches it. Else each riser is ``DEFAULT_RISE``.
* Which end is the top. The end that touches the building (the door the stair leads to) is the top; a stair that touches it at
  neither end or at both climbs towards the house.
* The foot. The first tread stands on the platform of the house (or on the terrain, where there is one) and the steps rise from
  there.

A stair inside the building (the stair of the house, between its floors) is not built here: floors, not steps, are what a
storey is made of.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from .falsewalls import Outline
from .mesh import SITE_PREFIX, Mesh, _normal, triangulate
from .terrainmesh import EMBED

DEFAULT_RISE = 0.17  # m: the height of a riser, when no level of the sheet says
RISE_RANGE = (0.12, 0.20)  # m: a riser the level of a storey can set is between these
OUTSIDE = 0.7  # a stair with less than this share outside the building is a stair of the house
TOUCH = 0.6  # m: an end of the stair this close to the building is where it reaches it
FOOT_DEPTH = 0.20  # m: on flat ground the steps reach this far below the platform (no gap shows under them)
KIND_NAME = "Gradini"


@dataclass
class Staircase:
    """One stair: its footprint, which way it climbs, how many steps and how high each is."""

    id: str  # Sito_Gradini_01
    footprint: Polygon  # metres, the frame of the plan
    run: tuple[float, float]  # the unit vector along which the stair climbs
    foot: float  # coordinate of the foot along ``run`` (the footprint lies between ``foot`` and ``top``)
    top: float
    across: tuple[float, float]  # the footprint's extent across the run
    count: int  # steps (risers)
    rise: float  # m, one riser
    rise_source: str  # predefinita | piano
    toward: str  # where the top is: "il lato che tocca la casa" | "la casa"
    keep: bool = True

    @property
    def tread(self) -> float:
        return abs(self.top - self.foot) / self.count

    @property
    def height(self) -> float:
        return self.count * self.rise

    def _rect(self, a: float, b: float) -> Polygon:
        """The part of the footprint between the coordinates ``a`` and ``b`` along the run."""
        ux, uy = self.run
        wx, wy = -uy, ux
        c0, c1 = self.across
        return Polygon([(s * ux + c * wx, s * uy + c * wy) for s, c in ((a, c0), (b, c0), (b, c1), (a, c1))])

    def region(self, k: int) -> Polygon:
        """What lies beyond the foot by ``k - 1`` treads (steps ``k`` to ``count``): the part higher than step ``k - 1``."""
        direction = 1.0 if self.top > self.foot else -1.0
        a = self.foot + direction * (k - 1) * self.tread
        return self._rect(*sorted((a, self.top)))

    def end(self, at: float) -> Polygon:
        """The tread at one end of the stair (``at``: the coordinate of the foot or of the top)."""
        inward = 1.0 if at <= (self.foot + self.top) / 2.0 else -1.0
        return self._rect(*sorted((at, at + inward * self.tread)))


def _axes(outline: Outline) -> tuple[float, float, tuple[float, float]]:
    """(lowest, highest coordinate along the run, extent across it) of the footprint of a stair."""
    ux, uy = outline.run
    pts = np.asarray(outline.geom.exterior.coords)[:-1]
    s = pts[:, 0] * ux + pts[:, 1] * uy
    c = -pts[:, 0] * uy + pts[:, 1] * ux
    return float(s.min()), float(s.max()), (float(c.min()), float(c.max()))


def _level_for(count: int, levels: list[float]) -> float | None:
    """The level (m above the platform) a stair of ``count`` risers reaches: of the levels where each riser would be plausible, the
    one nearest to ``count`` default risers."""
    fits = [level for level in levels if count * RISE_RANGE[0] <= level <= count * RISE_RANGE[1]]
    return min(fits, key=lambda level: abs(level - count * DEFAULT_RISE)) if fits else None


def build_stairs(outlines: list[Outline], building: BaseGeometry, levels: list[float]) -> list[Staircase]:
    """The stairs outside the building among ``outlines`` (the rows of step lines ``falsewalls`` found), with their steps.
    ``levels``: the heights (m above the finished floor) of the storeys above, which a stair may climb to."""
    out: list[Staircase] = []
    for o in outlines:
        if o.kind != "scala" or o.geom is None or o.geom.is_empty or not o.pitch:
            continue
        if o.geom.difference(building).area < OUTSIDE * o.geom.area:
            continue  # a stair of the house
        lo, hi, across = _axes(o)
        count = o.steps + 1  # the lines are the edges between the treads: one tread more than lines
        stair = Staircase("", o.geom, o.run, lo, hi, across, count, DEFAULT_RISE, "predefinita", "la casa")
        d_lo, d_hi = (stair.end(at).distance(building) for at in (lo, hi))
        if min(d_lo, d_hi) <= TOUCH and abs(d_lo - d_hi) >= TOUCH / 3:
            top_hi = d_hi < d_lo
            stair.toward = "il lato che tocca la casa"
        else:
            centre = building.centroid
            top_hi = stair.end(hi).centroid.distance(centre) < stair.end(lo).centroid.distance(centre)
        if not top_hi:
            stair.foot, stair.top = hi, lo
        level = _level_for(count, levels)
        if level is not None:
            stair.rise, stair.rise_source = level / count, "piano"
        out.append(stair)
    out.sort(key=lambda s: (-s.footprint.area, s.footprint.centroid.x))
    for n, s in enumerate(out, 1):
        s.id = f"{SITE_PREFIX}{KIND_NAME}_{n:02d}"
    return out


def _solid(st: Staircase, base: float, low: float) -> list[tuple[list[tuple[float, float, float]], tuple[float, float, float]]]:
    """The faces of the solid of a stair as (points, outward normal): the treads, the risers, the back, the bottom and the two
    sides, which are one stepped profile each, cut in triangles through every corner of it, so that no edge of a face ends in the
    middle of another's (the solid is closed)."""
    ux, uy = st.run
    wx, wy = -uy, ux
    c0, c1 = st.across
    up = 1.0 if st.top > st.foot else -1.0  # the sign of the run towards the top, along the coordinate s
    stops = [st.foot + up * k * st.tread for k in range(st.count)] + [st.top]  # where each step begins, then the back
    z = [base + k * st.rise for k in range(st.count + 1)]  # the top of the step k is z[k] (z[0]: the foot)

    def at(s: float, c: float, h: float) -> tuple[float, float, float]:
        return s * ux + c * wx, s * uy + c * wy, h

    faces = []
    for k in range(1, st.count + 1):
        a, b = stops[k - 1], stops[k]
        faces.append(([at(a, c0, z[k]), at(b, c0, z[k]), at(b, c1, z[k]), at(a, c1, z[k])], (0.0, 0.0, 1.0)))  # the tread
        foot = low if k == 1 else z[k - 1]
        faces.append(([at(a, c0, foot), at(a, c1, foot), at(a, c1, z[k]), at(a, c0, z[k])], (-up * ux, -up * uy, 0.0)))  # the riser
    faces.append(([at(st.top, c0, low), at(st.top, c1, low), at(st.top, c1, z[-1]), at(st.top, c0, z[-1])], (up * ux, up * uy, 0.0)))
    faces.append(([at(st.foot, c0, low), at(st.top, c0, low), at(st.top, c1, low), at(st.foot, c1, low)], (0.0, 0.0, -1.0)))
    profile = [(st.foot, low), (st.top, low), (st.top, z[-1])]  # the stepped outline of a side, seen from the side
    for k in range(st.count - 1, 0, -1):
        profile += [(stops[k], z[k + 1]), (stops[k], z[k])]
    profile += [(st.foot, z[1])]
    for c, sign in ((c0, -1.0), (c1, 1.0)):
        for tri in triangulate(Polygon(profile)):
            faces.append(([at(s, c, h) for s, h in tri], (sign * -uy, sign * ux, 0.0)))
    return faces


def add_stairs(mesh: Mesh, stairs: list[Staircase], ground=None) -> None:
    """Each stair as one closed solid of ``count`` steps, each one riser higher than the one before it. On a terrain the foot
    stands on the ground and the steps go down to it; on flat ground they rise from the platform."""
    for st in stairs:
        if not st.keep:
            continue
        if ground is None:
            base, low = 0.0, -FOOT_DEPTH
        else:
            feet = st.end(st.foot).centroid
            xs, ys = np.asarray(st.footprint.exterior.coords).T
            base = float(ground.lift(feet.x, feet.y))
            low = min(float(np.min(ground.lift(xs, ys))), base) - EMBED
        for pts, hint in _solid(st, base, low):
            n = _normal(pts)
            if n is None:
                continue
            if sum(a * b for a, b in zip(n, hint)) < 0:  # wound the other way: turn it round
                pts, n = pts[::-1], tuple(-v for v in n)
            mesh.add_face(st.id, pts, n)


def _number(stair: Staircase) -> str:
    return stair.id.rsplit("_", 1)[1]


def summary(stairs: list[Staircase]) -> str:
    """One line for the report: how many stairs and steps, how high."""
    kept = [s for s in stairs if s.keep]
    return f"{len(kept)} scale all'aperto, {sum(s.count for s in kept)} gradini (" + ", ".join(
        f"{_number(s)}: {s.count} gradini alti {s.rise * 100:.0f} cm = {s.height:.2f} m" for s in kept) + ")"


def notes(stairs: list[Staircase], warnings: list[str]) -> None:
    """What was guessed, to be checked in the 3D preview."""
    if not stairs:
        return
    by_level = [s for s in stairs if s.rise_source == "piano"]
    ends = "; ".join(f"{_number(s)}: sale verso {s.toward}" for s in stairs)
    warnings.append(
        f"Gradini: {len(stairs)} scale fuori dalla casa (righe di linee parallele a un passo di distanza) costruite a gradini, "
        f"ognuna un oggetto a se' (Sito_Gradini_NN). Il disegno non dice quanto sale ne' da che parte ({ends}). L'alzata e' "
        f"{DEFAULT_RISE * 100:.0f} cm" + (f", tranne {len(by_level)} che arrivano al livello del piano di sopra" if by_level else "")
        + ": controlla nell'anteprima 3D; per non averle usa --no-superfici-sito.")
