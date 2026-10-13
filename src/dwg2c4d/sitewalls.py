"""The free-standing walls of a site: retaining walls and garden walls, with the height written beside them.

A site plan draws such a wall as a long narrow closed outline (a polyline round a strip 40-50 cm wide) in the open, and writes
how high it is next to it ("Hmuro= 1,00 ml.", see ``lowheights``). A closed outline is a wall when the strip it encloses is thin
(``WALL_THICKNESS``) and long (``MIN_LENGTH``) and lies outside the buildings and the pools; the texts that give a height
(``lowheights.written_height``) decide how high each part of it is: a point of the wall takes the height of the nearest text, so
a wall written 1,00 m at one end and 0,50 m at the other has two heights. A wall that no text reaches (``REACH``) is as high as
a low wall is by default (``--altezza-muretti``), and the report says so: every height is a guess to be checked.

The walls are built as the low walls of a plan are: ``Muretti`` (thicker than a curb) and ``Cordoli`` (thin), laid on the terrain
when there is one. The pairs of lines that a fence or a wall is drawn with (``lotfence``) are not read here: a site plan has too
many lines for that search.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import shapely
from shapely.geometry import MultiPoint, Point, box
from shapely.geometry.base import BaseGeometry

from .falsewalls import CURB_THICKNESS
from .geom import polygons_of, union
from .lotfence import read_linework
from .lowheights import REACH, SAME, written_height
from .mesh import Mesh, Slab
from .terrainmesh import add_draped

WALL_THICKNESS = (0.15, 0.60)  # m: the strip a wall outline encloses is this thick (2 * area / perimeter): thinner is a line, thicker a surface
MIN_LENGTH = 3.0  # m: a strip shorter than this (half its perimeter, less its width) is a pillar, a step, a mark
INSIDE = 0.5  # a strip with more than this share under a roof or in a pool is no free-standing wall
MIN_PIECE = 0.02  # m2: the part of a wall that a text reaches is this big at least to count
OTHER_VIEWS = ("elevation", "section", "roof", "detail")  # views of the sheet whose lines are no wall of the site


@dataclass
class Wall:
    """A piece of free-standing wall: its footprint (m), its height and where the height came from."""

    shape: BaseGeometry
    height: float
    text: str = ""  # the text that wrote the height; empty: the default

    @property
    def curb(self) -> bool:
        """Thin enough to be a curb (group ``Cordoli``)."""
        return 2.0 * self.shape.area / self.shape.length < CURB_THICKNESS if self.shape.length else True


@dataclass
class SiteWalls:
    walls: list[Wall] = field(default_factory=list)
    unused: list[str] = field(default_factory=list)  # texts that gave a height to no wall

    @property
    def length(self) -> float:
        return sum(_length(w.shape) for w in self.walls)


def _thickness(shape: BaseGeometry) -> float:
    return 2.0 * shape.area / shape.length if shape.length else 0.0


def _length(strip: BaseGeometry) -> float:
    """The length of a strip: half of its perimeter less its width."""
    return strip.length / 2.0 - _thickness(strip)


def wall_outlines(doc, cfg, analysis, site) -> list[BaseGeometry]:
    """The closed outlines of the site plan that are thin and long strips outside the buildings."""
    views = [v for v in analysis.views if v.id in site.views]
    skip = [v.bbox for v in analysis.views if v.id not in site.views and v.kind in OTHER_VIEWS]
    window = (min(v.bbox[0] for v in views), min(v.bbox[1] for v in views), max(v.bbox[2] for v in views), max(v.bbox[3] for v in views))
    roofs = union([site.roofs, *(b.outline for b in site.buildings)])
    lines = read_linework(doc, cfg, analysis.unit.scale, window, skip)
    out = []
    for _, ring in lines.rings:
        if not WALL_THICKNESS[0] <= _thickness(ring) <= WALL_THICKNESS[1] or _length(ring) < MIN_LENGTH:
            continue
        if not roofs.is_empty and ring.intersection(roofs).area > INSIDE * ring.area:
            continue
        out.append(ring)
    return polygons_of(union(out))


def nearest_texts(body: BaseGeometry, texts: list[tuple[float, float, float, str]]) -> list[tuple[float, float, float, str]]:
    """The texts (x, y, height, text) within ``REACH`` of a wall."""
    return [t for t in texts if body.distance(Point(t[0], t[1])) <= REACH]


def split_by_texts(body: BaseGeometry, texts: list[tuple[float, float, float, str]]) -> list[tuple[BaseGeometry, float, str]]:
    """(part, height, text) for the parts of ``body`` that a text reaches: every point takes the height of the text nearest to it.
    Parts with the same height are one."""
    if len(texts) == 1:
        return [(body, texts[0][2], texts[0][3])]
    x0, y0, x1, y1 = body.bounds
    cells = shapely.voronoi_polygons(MultiPoint([(t[0], t[1]) for t in texts]), extend_to=box(x0 - 1.0, y0 - 1.0, x1 + 1.0, y1 + 1.0))
    parts: dict[float, list] = {}
    for cell in cells.geoms:
        k = min(range(len(texts)), key=lambda i: cell.distance(Point(texts[i][0], texts[i][1])))
        piece = body.intersection(cell)
        if piece.area >= MIN_PIECE:
            parts.setdefault(round(texts[k][2] / SAME), []).append((piece, texts[k][2], texts[k][3]))
    return [(union(p for p, _, _ in group), group[0][1], group[0][2]) for group in parts.values()]


def site_walls(bodies: list[BaseGeometry], cfg, shells: list[BaseGeometry], words: list, notes: list[str]) -> SiteWalls | None:
    """The free-standing walls among the outlines ``bodies`` (``wall_outlines``), or None: those in a pool are no walls. ``words``:
    the texts of the sheet (``texts.RawText``: x, y in m); ``notes`` gets what the report says."""
    pools = union(shells)
    bodies = [b for b in bodies if pools.is_empty or b.intersection(pools).area <= INSIDE * b.area]
    if not bodies:
        return None
    texts = []
    if cfg.low_heights_auto:  # a height the user gave beats the one the drawing writes
        texts = [(w.x, w.y, h, w.text.strip()) for w in words if (h := written_height(w.text)) is not None]
    result = SiteWalls()
    used: set[str] = set()
    for body in bodies:
        near = nearest_texts(body, texts)
        parts = split_by_texts(body, near) if near else []
        for shape, height, text in parts:
            result.walls.append(Wall(shape, height, text))
            used.add(text)
        rest = body.difference(union(p for p, _, _ in parts).buffer(0.001)) if parts else body
        for piece in polygons_of(rest):
            if piece.area >= MIN_PIECE:
                result.walls.append(Wall(piece, cfg.curb_height if _thickness(piece) < CURB_THICKNESS else cfg.low_wall_height))
    result.unused = sorted({t[3] for t in texts} - used)
    notes.extend(wall_notes(result, cfg))
    return result


def wall_notes(result: SiteWalls, cfg) -> list[str]:
    """What the report says of the walls: how they were read, and every height that is a guess."""
    written = [w for w in result.walls if w.text]
    plain = [w for w in result.walls if not w.text]
    out = [f"Muri liberi: {len(result.walls)} pezzi di muro di sostegno o di giardino, {result.length:.0f} m in tutto, dai contorni chiusi "
           f"stretti e lunghi ({WALL_THICKNESS[0] * 100:.0f}-{WALL_THICKNESS[1] * 100:.0f} cm di spessore, almeno {MIN_LENGTH:g} m) fuori "
           "dagli edifici: controlla nell'anteprima 3D che siano muri."]
    if written:
        heights = sorted({(w.height, w.text) for w in written})
        out.append("Muri liberi: altezze scritte nel disegno vicino ai muri (ogni punto del muro prende quella della scritta piu' vicina): "
                   + ", ".join(f"{h:g} m da '{t}'" for h, t in heights) + ". Controlla che le scritte siano di quei muri.")
    if plain:
        out.append(f"Muri liberi: {len(plain)} pezzi senza nessuna altezza scritta vicino (a meno di {REACH:g} m) sono alti {cfg.low_wall_height:g} m "
                   f"(i sottili {cfg.curb_height:g} m): e' un'ipotesi, usa --altezza-muretti per cambiarla.")
    if result.unused:
        out.append("Muri liberi: " + ", ".join(f"'{t}'" for t in result.unused) + " non ha nessun muro libero vicino: l'altezza non e' stata usata.")
    return out


def add_walls(mesh: Mesh, walls: SiteWalls, ground=None) -> None:
    """The walls as low walls (``Muretti``) and curbs (``Cordoli``): closed solids standing on the terrain (their top ``height`` over
    the ground, flat ground with no terrain)."""
    for wall in walls.walls:
        name = "Cordoli" if wall.curb else "Muretti"
        before = len(mesh.groups.get(name, []))
        try:
            if ground is None:
                raise ValueError  # flat ground: the plain extrusion below
            add_draped(mesh, ground, name, wall.shape, wall.height + ground.level)
        except Exception:
            del mesh.groups.get(name, [])[before:]
            mesh.add_extrusion(name, [Slab(0.0, wall.height, wall.shape)], bottom=True)
