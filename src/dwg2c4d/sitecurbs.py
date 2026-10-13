"""The curbs and the retaining walls of the site: the double lines of the site plan, built low, each an object of its own.

A curb, a path edge, a low wall, a retaining wall are drawn in a site plan as two lines that run side by side, 6 to 60 cm apart
(a thicker double line is a wall). The strips between such lines (the same ones the fence of the lot is looked for in, see
``lotfence``) that are long enough, lie outside the building and are no fence are built here, one object for each:

* A strip that a written word says is a wall (muro, contenimento, sostegno, scarpata, argine), that has its height written beside
  it ("Hmuro= 1,00 ml.", ``lowheights``), or that is as thick as a wall (``WALL_THICKNESS``) is a wall of the site,
  ``Sito_Muro_01``. Its height is the written one, else the step of the ground across it (the terrain, where there is one: the
  ground is higher on one side than on the other, the wall holds the difference), else ``cfg.low_wall_height``.
* Any other is a curb, ``Sito_Cordolo_01``, ``cfg.curb_height`` high (or the written height).

The terrain decides nothing about which of the two it is: the drawing does; the report says which was which and why, so the heights
can be changed with ``--altezza-muretti`` and ``--altezza-cordoli`` or in Cinema 4D.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from .config import Config
from .geom import polygons_of, union
from .lotfence import _chains, _pairs, _wall_strips
from .lowheights import REACH, SAME, written_height
from .mesh import SITE_PREFIX, Mesh, Slab
from .terrainmesh import add_draped

GAP = (0.06, 0.60)  # m: two lines this far apart are the sides of a curb or a wall (closer: a railing, a line drawn twice)
MIN_RUN = 6.0  # m: a strip shorter than this is a kerbstone, a joint of a paving, a bar of a parking: no curb
INSIDE = 0.3  # a strip with more than this share inside the building, or in what is taken, is no strip of the site
WALL_THICKNESS = 0.35  # m: a strip this thick or thicker is a wall, a thinner one a curb
WALL_WORDS = re.compile(r"\b(?:mur[oi]|contenimento|sostegno|scarpata|argine)\b", re.I)
STEP_SAMPLE = 1.0  # m: spacing of the points at which the step of the ground across a wall is read
STEP_OFFSET = 1.0  # m: ... on each side of the wall, this far from its axis
STEP_RANGE = (0.3, 3.5)  # m: a step of the ground across a wall that is lower is no step the wall holds, a higher one is cut
KIND_NAMES = {"muro": "Muro", "cordolo": "Cordolo"}


@dataclass
class SiteWall:
    """A strip of curb or wall: its footprint and axis (metres, the frame of the plan), what it is and how high."""

    id: str  # Sito_Muro_01
    kind: str  # muro | cordolo
    geom: Polygon
    axis: LineString
    thickness: float
    layer: str
    height: float  # m, above the ground
    height_source: str = "predefinita"  # testo | dislivello | predefinita
    why: str = ""  # how the kind was told
    keep: bool = True


Words = list[tuple[str, float, float]]  # (text, x, y): the texts of the site plan, in metres in the frame of the plan


def _near(words: Words, strip: Polygon) -> Words:
    return [w for w in words if strip.distance(Point(w[1], w[2])) <= REACH]


def _wall_or_curb(strip, near: Words) -> tuple[bool, str, tuple[float, str] | None]:
    """(is it a wall, why, (the height written beside it, the text)) for a strip with the texts near it."""
    named = next((t.strip() for t, _, _ in near if WALL_WORDS.search(t)), None)
    heights = {round(h / SAME): (h, t.strip()) for t, _, _ in near if (h := written_height(t)) is not None}
    written = next(iter(heights.values())) if len(heights) == 1 else None  # two heights written near it: none is its own
    thick = f"spessore {strip.thickness * 100:.0f} cm"
    if named:
        return True, f"scritta '{named}'", written
    if written:
        return True, f"{thick}, altezza scritta vicino", written
    return strip.thickness >= WALL_THICKNESS, thick, None


def find_walls(lines: list[tuple[str, LineString]], building: BaseGeometry, taken: BaseGeometry, fills: BaseGeometry,
               words: Words, cfg: Config) -> list[SiteWall]:
    """The curbs and walls of a site plan's lines (metres, the frame of the plan): the strips between lines that run side by
    side (``GAP``), at least ``MIN_RUN`` long, outside the building and what is ``taken`` (the fence of the lot), not in a
    hatch (the joints of a paving)."""
    avoid = union([building, taken])
    walls: list[SiteWall] = []
    for s in _wall_strips([], _pairs(_chains(lines), GAP, MIN_RUN)):
        if s.axis.length < MIN_RUN or s.body.intersection(avoid).area > INSIDE * s.body.area:
            continue
        if not fills.is_empty and s.body.intersection(fills).area > 0.5 * s.body.area:
            continue
        wall, why, written = _wall_or_curb(s, _near(words, s.body))
        site = SiteWall("", "muro" if wall else "cordolo", s.body, s.axis, s.thickness, s.layer,
                        cfg.low_wall_height if wall else cfg.curb_height, why=why)
        if written is not None:
            site.height, site.height_source = written[0], "testo"
            site.why += f"; altezza {written[0]:g} m dalla scritta '{written[1]}'"
        walls.append(site)
    walls.sort(key=lambda w: (w.kind != "muro", -w.axis.length))
    count: dict[str, int] = {}
    for w in walls:
        count[w.kind] = count.get(w.kind, 0) + 1
        w.id = f"{SITE_PREFIX}{KIND_NAMES[w.kind]}_{count[w.kind]:02d}"
    return walls


def step_across(wall: SiteWall, ground) -> float | None:
    """How much higher the ground is on one side of the wall than on the other, along it (the median of the points read), or
    None when the step is not one a wall holds (``STEP_RANGE``)."""
    n = max(int(wall.axis.length / STEP_SAMPLE), 2)
    pts = shapely.line_interpolate_point(wall.axis, np.linspace(0.0, wall.axis.length, n + 1))
    xy = shapely.get_coordinates(pts)
    ahead = shapely.get_coordinates(shapely.line_interpolate_point(wall.axis, np.minimum(np.linspace(0.0, wall.axis.length, n + 1) + 0.5,
                                                                                         wall.axis.length)))
    d = ahead - xy
    length = np.hypot(d[:, 0], d[:, 1])
    ok = length > 1e-6
    if not ok.any():
        return None
    left = np.stack([-d[ok, 1], d[ok, 0]], axis=1) / length[ok, None] * STEP_OFFSET
    a, b = xy[ok] + left, xy[ok] - left
    step = float(np.median(np.abs(ground.height(a[:, 0], a[:, 1]) - ground.height(b[:, 0], b[:, 1]))))
    return min(step, STEP_RANGE[1]) if step >= STEP_RANGE[0] else None


def add_site_walls(mesh: Mesh, walls: list[SiteWall], ground=None) -> None:
    """Every curb and wall as a slab of its own: on a terrain it stands on the ground and follows it (a wall as high as the step
    of the ground across it, when the drawing does not write its height), else it is extruded from zero. The height each got is
    left in ``height`` and ``height_source`` for the report."""
    for w in walls:
        if not w.keep:
            continue
        if ground is not None and w.kind == "muro" and w.height_source not in ("testo", "tabella"):
            step = step_across(w, ground)
            if step is not None:
                w.height, w.height_source = step, "dislivello"
        if ground is None:
            mesh.add_extrusion(w.id, [Slab(0.0, w.height, w.geom)], bottom=True)
        else:
            add_draped(mesh, ground, w.id, w.geom, w.height)


def summary(walls: list[SiteWall]) -> str:
    """One line for the report: the curbs and the walls, how long, how high."""
    parts = []
    for kind, label in (("muro", "muri"), ("cordolo", "cordoli")):
        mine = [w for w in walls if w.kind == kind and w.keep]
        if mine:
            heights = sorted({round(w.height, 2) for w in mine})
            parts.append(f"{len(mine)} {label} ({sum(w.axis.length for w in mine):.0f} m, alti "
                         + ", ".join(f"{h:g}" for h in heights[:4]) + " m)")
    return ", ".join(parts)


def notes(walls: list[SiteWall], warnings: list[str]) -> None:
    """What was guessed, to be checked in the 3D preview."""
    if not walls:
        return
    n_wall = sum(w.kind == "muro" for w in walls)
    warnings.append(
        f"Muri del sito: {len(walls)} coppie di linee parallele lunghe (a 6-60 cm una dall'altra, fuori dalla casa) costruite "
        f"basse, ognuna un oggetto a se': {n_wall} muri (Sito_Muro_NN: spessi come un muro, o con una scritta che lo dice) e "
        f"{len(walls) - n_wall} cordoli (Sito_Cordolo_NN). Il disegno non dice se sono davvero un muro o un cordolo ne' quanto "
        "sono alti: un muro e' alto come dice la scritta vicino, o come il dislivello del terreno ai suoi due lati, altrimenti come "
        "un muretto; controlla nell'anteprima 3D, correggi con --altezza-muretti e --altezza-cordoli, o usa --no-superfici-sito "
        "per non averli.")
