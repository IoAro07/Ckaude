"""The terrain of a site: the contour lines of the site plan under buildings that each stand at their own height.

A house on a plan is the zero of the model and the ground falls and rises away from its flat platform (``terrain``). The
buildings of a farm stand on a hill, a few metres above or below one another: each has its finished floor at the height of the
ground under it (the median of the contour lines over its footprint), measured from the floor of the biggest building, which is
the zero of the model. Every building (and every pool) gets a flat platform of its own at that height, and the ground between
them runs as it would from each of them (never steeper than ``SLOPE_MAX``), so the one a point lies closest to does not decide:
the ground lies where the contour lines say, within the reach of every platform.

The contour lines are in the frame of the site plan the roofs were read in; a survey drawn in another view of the sheet is
laid on it by the lines the two share (``sitefit``). Nothing is guessed beyond that: no contour lines, no terrain, the
buildings stand on the flat zero.
"""

from __future__ import annotations

import numpy as np
import shapely
from scipy.ndimage import gaussian_filter
from shapely.geometry import MultiPoint, box
from shapely.geometry.base import BaseGeometry

from .geom import union
from .mesh import Mesh
from .sitefit import Placement, register
from .terrain import (CROP, HULL_SLACK, MARGIN, MIN_LEVELS, MIN_LEVELS_ASKED, MIN_POINTS, PLATFORM_MARGIN, POOL_MARGIN, SLOPE_MAX,
                      SMOOTH, Terrain, _grid, _ground_under, _same_frame, contour_view, read_contours, surface)
from .terrainmesh import add_terrain


def relief(points: np.ndarray, window: tuple[float, float, float, float], platforms: list[tuple[BaseGeometry, float]], hole: BaseGeometry,
           floor: float, level: float, thickness: float) -> Terrain:
    """The terrain over ``window``: ``points`` (N x 3, the contour vertices in the frame of the plan, heights absolute); ``floor`` the
    height in them that is the zero of the model; ``platforms`` the flat grounds (shape, height from the zero) the ground keeps
    within ``SLOPE_MAX`` of; ``level`` the platform of the zero (what ``Terrain.lift`` is measured from)."""
    step, nx, ny = _grid(window)
    xs, ys = np.meshgrid(window[0] + np.arange(nx + 1) * step, window[1] + np.arange(ny + 1) * step)
    near = (points[:, 0] >= window[0] - CROP) & (points[:, 0] <= window[2] + CROP) \
        & (points[:, 1] >= window[1] - CROP) & (points[:, 1] <= window[3] + CROP)
    points = points[near] if near.sum() >= MIN_POINTS else points
    natural = gaussian_filter(surface(points, xs.ravel(), ys.ravel()).reshape(xs.shape), SMOOTH / step, mode="nearest") - floor
    spots = shapely.points(xs.ravel(), ys.ravel())
    low = np.full(xs.shape, -np.inf)
    high = np.full(xs.shape, np.inf)
    for shape, height in platforms:
        away = SLOPE_MAX * shapely.distance(shape, spots).reshape(xs.shape)
        low, high = np.maximum(low, height - away), np.minimum(high, height + away)
    z = np.where(low > high, (low + high) / 2.0, np.clip(natural, low, high))  # two platforms too far apart in height for the ground between them: a steep step
    return Terrain(window[0], window[1], step, z, level, float(z.min()) - thickness, union(shape for shape, _ in platforms), hole)


def place(analysis, scale: float, contours, views: list) -> tuple[Placement | None, bool, int | None]:
    """(the move that lays the site plan on the frame of the contour lines, whether it had to be found, the number of the view that
    holds the lines): none when the lines are in a view of the sheet the site plan shares nothing with."""
    view = contour_view(analysis, contours.xyz[:, :2], scale)
    if view is None or any(_same_frame(analysis, v, view) for v in views):
        return Placement(), False, view.id if view is not None else None
    return register(analysis, scale, views[0], view, {contours.layer}), True, view.id


def site_terrain(doc, cfg, analysis, site, shells: list[BaseGeometry], window: tuple[float, float, float, float] | None,
                 notes: list[str]) -> Terrain | None:
    """The terrain of the site, or None (``notes`` says why when the contour lines were there). Sets ``base`` of every building of
    ``site``: where its floor lies above the zero of the model. ``shells``: the pools (dug flat, at the height of the ground under
    them); ``window``: the garden's box (m), which the terrain covers."""
    asked = cfg.terrain
    scale = analysis.unit.scale
    found = read_contours(doc, cfg, scale, MIN_LEVELS_ASKED if asked else MIN_LEVELS)
    if not found:
        if asked:
            notes.append("Terreno: non ho trovato curve di livello (polilinee ognuna a una sua quota, a intervalli regolari) in nessun "
                          "layer del foglio: il terreno non e' stato costruito.")
        return None
    c = found[0]
    views = [v for v in analysis.views if v.id in site.views]
    placement, registered, view_id = place(analysis, scale, c, views)
    if placement is None:
        placement = Placement() if asked else None  # the user says the contour lines are over the site plan: they are, unless they are far away
    if placement is None:
        notes.append(f"Terreno: il foglio ha {len(c.levels)} curve di livello (layer {c.layer}) ma non riesco a capire dove stanno gli "
                     "edifici sul rilievo (nessuna linea in comune tra la planimetria generale e il disegno delle curve): nessun "
                     "terreno. Se le curve e gli edifici sono nello stesso riferimento usa --terreno.")
        return None
    xy = placement.to_plan(c.xyz[:, :2])
    points = np.column_stack([xy, c.xyz[:, 2]])
    buildings = union(b.footprint for b in site.buildings)
    near = MultiPoint(points[:: max(1, len(points) // 3000), :2]).convex_hull.distance(buildings)
    if near > HULL_SLACK:
        notes.append(f"Terreno: le curve di livello del layer {c.layer} stanno a {near:.0f} m dagli edifici: non coprono il punto dove "
                     "stanno, nessun terreno (curve di un altro lotto?).")
        return None
    floors = [_ground_under(points, b.footprint) for b in site.buildings]
    zero = floors[0]  # the biggest building: its floor is the zero of the model
    pool_floors = [_ground_under(points, s) for s in shells]
    drop = cfg.floor_thickness
    platforms = [(b.footprint.buffer(PLATFORM_MARGIN, join_style="mitre"), f - zero - drop) for b, f in zip(site.buildings, floors)]
    platforms += [(s.buffer(POOL_MARGIN, join_style="mitre"), f - zero - drop) for s, f in zip(shells, pool_floors)]
    covered = box(*MultiPoint(points[:, :2]).bounds)
    areas = [box(*buildings.bounds).buffer(MARGIN), *(p for p, _ in platforms)]
    if window:
        areas.append(box(*window))
    wanted = union(areas)
    ground = relief(points, wanted.intersection(covered).bounds, platforms, union(shells), zero, -drop, cfg.terrain_thickness)
    add_terrain(Mesh(), ground)  # the mesh must work out before the buildings are put on it
    for b, f in zip(site.buildings, floors):
        b.base = f - zero
    x0, y0, x1, y1 = ground.window
    ground.info = {"layer": c.layer, "levels": len(c.levels), "step": c.step, "low": c.levels[0], "high": c.levels[-1], "floor": zero,
                   "floor_source": f"terreno sotto l'edificio {site.buildings[0].id}", "level": -drop, "size": (x1 - x0, y1 - y0),
                   "thickness": cfg.terrain_thickness, "view": view_id, "registered": registered, "shift": placement.shift,
                   "common": placement.common, "views": placement.views}
    notes.extend(terrain_notes(ground, site, registered))
    return ground


def terrain_notes(ground: Terrain, site, registered: bool) -> list[str]:
    """What was guessed, to be checked in the 3D preview."""
    i = ground.info
    out = []
    if registered:
        out.append(f"Terreno: le curve di livello stanno nella vista {i['view']} del foglio, la planimetria generale in un'altra: l'ho "
                   f"messa sul rilievo facendo combaciare {i['common']:.0f} m di linee tra le viste {', '.join(map(str, i['views']))} "
                   f"(spostamento di {i['shift'][0]:.1f}, {i['shift'][1]:.1f} m). Controlla nell'anteprima 3D che gli edifici siano "
                   "dove devono stare sul terreno.")
    heights = ", ".join(f"edificio {b.id} a {b.base:+.2f} m" for b in site.buildings)
    out.append(f"Terreno: ogni edificio sta su una piattaforma piana al livello del terreno sotto di lui (la mediana delle curve di "
               f"livello sulla sua impronta), contato dal pavimento dell'edificio 1, che e' lo zero ({heights}); intorno a ogni piattaforma "
               f"il suolo e' piano per {PLATFORM_MARGIN:g} m e poi una scarpata al massimo 1:{1 / SLOPE_MAX:g} lo raccorda alle curve di "
               "livello. I piani dei pavimenti sono presunti dal terreno: i prospetti non li danno. Controlla nell'anteprima 3D.")
    return out
