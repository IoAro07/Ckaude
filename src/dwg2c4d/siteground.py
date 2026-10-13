"""The site round the buildings of a site plan (see sitemode): the garden, the terrain, the low walls.

The buildings of a site come from their roofs, so the ground round them is read by the machinery of a house with a garden
(``garden``), with the differences that a site plan makes:

* what is "inside" is not the walls of one house but the roofs of every building as drawn, with every other hatch of tiles
  (a canopy, a well cover: no ground lies under a roof);
* the sheet holds elevations and sections beside the site plan: nothing in them is ground, and nothing beyond the site plan
  is either;
* a pool is the layer that is called one (Piscina): a blue hatch on another layer (the panels of a carport) is no water;
* what the names call floors (the layer Pavimenti) is the paving of the open air: there is no plan of rooms to be the floor of.

Round the garden come the terrain (``siteterrain``: every building and pool at the height of the ground under it) and the
free-standing walls (``sitewalls``: retaining walls drawn as thin closed outlines, with the height written beside them).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from shapely.geometry.base import BaseGeometry

from .config import LayerRules
from .garden import Garden, add_garden, build_garden, place_on_terrain
from .garden_table import apply_garden_table, read_garden_table
from .geom import union
from .mesh import Mesh
from .siteterrain import site_terrain
from .sitewalls import SiteWalls, add_walls, site_walls, wall_outlines
from .texts import read_texts
from .terrain import Terrain
from .terrainmesh import add_terrain

GARDEN_SKIPPED_KINDS = ("elevation", "section", "roof", "detail")  # views whose contents are never garden
FLOOR_WORDS = ("pavim", "floor", "solai")  # beginnings of the words a layer of paving is called by


class OpenAirRules(LayerRules):
    """The layer rules of a site plan: no plan of a building lies in it, so what the names call floors (Pavimenti, Pavimentazione) is
    the paving of the open air, not the floors of rooms."""

    def garden_kind(self, layer: str) -> str | None:
        kind = super().garden_kind(layer)
        if kind is None and any(t.startswith(FLOOR_WORDS) for t in self.tokens(layer)) and "int" not in self.tokens(layer):
            return "paving"
        return kind


@dataclass
class Around:
    """What lies round the buildings of a site: the garden (ground, pool, plants) and the terrain under it all."""

    garden: Garden | None = None
    terrain: Terrain | None = None
    walls: SiteWalls | None = None  # the free-standing walls (retaining walls, garden walls)


def _box(views: list) -> tuple[float, float, float, float]:
    """The box (drawing units) that holds the views."""
    return (min(v.bbox[0] for v in views), min(v.bbox[1] for v in views),
            max(v.bbox[2] for v in views), max(v.bbox[3] for v in views))


def site_garden(doc, cfg, analysis, site, walls: list[BaseGeometry], notes: list[str]) -> Garden | None:
    """The garden of the site, or None. The buildings are the roofs as drawn (``site.roofs`` and the outlines of the buildings,
    eaves included: the hatches of tiles are no paving), the garden lies in the views the roofs were read in (``--area-giardino``
    says otherwise), and the elevations of the sheet give the heights of the plants they draw. ``walls``: the free-standing walls
    (``sitewalls``): no ground is laid under them, and the hatch that fills a wall is no paving."""
    if not cfg.garden:
        return None
    views = [v for v in analysis.views if v.id in site.views]
    others = [v.bbox for v in analysis.views if v.id not in site.views and v.kind in GARDEN_SKIPPED_KINDS]
    zones = [v.bbox for v in analysis.views if v.kind == "elevation"]
    inside = union([site.roofs, *(b.outline for b in site.buildings)])
    where = replace(cfg, garden_exclude=[*cfg.garden_exclude, *others], garden_reach=cfg.garden_reach or _box(views))
    try:
        garden = build_garden(doc, where, analysis.unit.scale, inside, zones, notes, union(walls))
    except Exception as exc:  # the garden is a help: whatever goes wrong in it is never the reason a conversion fails
        notes.append(f"Giardino non costruito: {type(exc).__name__}: {exc}. Gli edifici sono completi; per escludere il giardino "
                     "usa --no-giardino, per limitarlo --area-giardino.")
        return None
    if cfg.garden_table_in:  # the table of a former conversion, corrected by hand
        if garden is None:
            notes.append("Tabella del giardino indicata, ma non ho trovato nessun giardino a cui applicarla.")
        elif not apply_garden_table(garden, read_garden_table(cfg.garden_table_in), cfg, notes):
            notes.append("Tabella del giardino: nessuna riga con celle MODIFICA_* compilate da applicare.")
    return garden


def around_site(doc, cfg, analysis, site, notes: list[str]) -> Around:
    """The garden, the terrain and the low walls of the site (``notes`` gets what the report says of them). The terrain sets the
    floor height of every building (``Building.base``)."""
    open_air = replace(cfg, layers=OpenAirRules(cfg.layers.overrides))  # (no plan of rooms: the layer of the floors is paving)
    outlines = []
    if cfg.fences:  # the free-standing walls first: the garden leaves them their ground
        try:
            outlines = wall_outlines(doc, open_air, analysis, site)
        except Exception as exc:  # a help: whatever goes wrong in it is never the reason a conversion fails
            notes.append(f"Muri liberi non cercati: {type(exc).__name__}: {exc}. Il resto della conversione e' completo; per escluderli "
                         "usa --no-recinzioni.")
    garden = site_garden(doc, open_air, analysis, site, outlines, notes)
    terrain = None
    if cfg.terrain or cfg.terrain_auto:
        shells = garden.shells if garden is not None else []
        window = garden.window if garden is not None else None
        try:
            terrain = site_terrain(doc, cfg, analysis, site, shells, window, notes)
        except Exception as exc:  # a help: whatever goes wrong in it is never the reason a conversion fails
            for b in site.buildings:
                b.base = 0.0
            notes.append(f"Terreno non costruito: {type(exc).__name__}: {exc}. Il resto della conversione e' completo; per escluderlo "
                         "usa --no-terreno.")
    if terrain is not None and garden is not None:
        place_on_terrain(garden.objects, terrain)
    walls = None
    if outlines:
        try:
            words = read_texts(doc, cfg, analysis.unit.scale) if cfg.texts else []
            walls = site_walls(outlines, cfg, garden.shells if garden is not None else [], words, notes)
        except Exception as exc:
            notes.append(f"Muri liberi non costruiti: {type(exc).__name__}: {exc}. Il resto della conversione e' completo; per escluderli "
                         "usa --no-recinzioni.")
    return Around(garden, terrain, walls)


def add_around(mesh: Mesh, around: Around, cfg, notes: list[str]) -> None:
    """Put what lies round the buildings in ``mesh``. It is a help: what fails is taken out again and the buildings stay."""
    if around.terrain is not None:
        add_terrain(mesh, around.terrain)
    if around.walls is not None:
        add_walls(mesh, around.walls, around.terrain)
    if around.garden is not None:
        before = set(mesh.groups)
        try:
            add_garden(mesh, around.garden, cfg, around.terrain)
        except Exception as exc:
            for name in set(mesh.groups) - before:
                del mesh.groups[name]
            around.garden = None
            notes.append(f"Giardino non costruito: {type(exc).__name__}: {exc}. Gli edifici sono completi.")
