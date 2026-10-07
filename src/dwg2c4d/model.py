"""Assemble the 3D mesh from the 2D plan elements."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from functools import cached_property

from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry

from .config import Config
from .geom import polygons_of, union
from .fixtures import add_fixtures
from .garden import add_garden
from .mesh import Mesh, Slab
from .openings import Opening
from .roof import Roof
from .walls import clean_footprint

SLIVER = 0.004  # wall remnants thinner than 2*SLIVER next to an opening are removed
FLOOR_CLOSING = 0.6  # gaps up to twice this (door openings) are bridged when filling the floor


@dataclass
class Plan:
    walls: BaseGeometry  # as drawn (may have gaps where doors/windows are)
    columns: BaseGeometry
    openings: list[Opening] = field(default_factory=list)
    merge_tolerance: float = 0.01
    roof: Roof | None = None
    floors: list[tuple[str, BaseGeometry]] = field(default_factory=list)  # (name, shape): one object each
    skirting: BaseGeometry = field(default_factory=Polygon)
    partitions: BaseGeometry = field(default_factory=Polygon)  # the thin walls, apart from the main walls
    garden: object | None = None  # the ground, pools, plants and furniture outside (garden.Garden)

    @cached_property
    def solid_walls(self) -> BaseGeometry:
        """Walls with every opening footprint filled in. The openings are cut back
        out between their own z0/z1, so a wall drawn with a gap at a door or window
        is rebuilt above (and, for windows, below) the opening."""
        filled = union([self.walls] + [o.fill for o in self.openings])
        return clean_footprint(filled, self.merge_tolerance)


def floor_footprint(walls: BaseGeometry) -> BaseGeometry:
    """Filled outline of every wall network that encloses space."""
    closed = walls.buffer(FLOOR_CLOSING, join_style="mitre").buffer(-FLOOR_CLOSING, join_style="mitre")
    return union(Polygon(p.exterior) for p in polygons_of(closed) if p.interiors)


def wall_slabs(walls: BaseGeometry, openings: list[Opening], height: float) -> list[Slab]:
    """Stack of footprints from z=0 to ``height``; each slab has the openings cut
    that span it entirely. Equal neighbouring footprints are merged.
    ``walls`` must already contain the opening footprints (``Plan.solid_walls``)."""
    zs = {0.0, height}
    for o in openings:
        zs.update((max(0.0, o.z0), min(height, o.z1)))
    levels = sorted(zs)
    slabs: list[Slab] = []
    for za, zb in zip(levels, levels[1:]):
        if zb - za < 1e-6:
            continue
        cuts = [o.cut for o in openings if o.z0 <= za + 1e-9 and o.z1 >= zb - 1e-9]
        geom = walls.difference(union(cuts)) if cuts else walls
        if cuts:
            geom = geom.buffer(-SLIVER).buffer(SLIVER, join_style="mitre")  # drop mm-thin fins
        if slabs and slabs[-1].geom.equals(geom):
            slabs[-1].z1 = zb
        else:
            slabs.append(Slab(za, zb, geom))
    return slabs


def building_footprint(solid: BaseGeometry) -> BaseGeometry:
    """Everything the walls enclose, walls included: the outline of the building with no holes."""
    return union(Polygon(p.exterior) for p in polygons_of(solid))


def facing_sign(op: Opening, footprint: BaseGeometry) -> int:
    """Which way the front of an opening's group looks, as +1/-1 along its across axis ``v``: towards the
    outside of the building when one side of the opening is outside (a window or an entrance door), else
    the side a door leaf swings to, else +1. A replacement model dropped into the group then faces the
    same way as the one it replaces."""
    ux, uy = op.axis
    vx, vy = -uy, ux
    reach = op.thickness / 2.0 + 0.3
    cx, cy = op.center
    ahead = not footprint.covers(Point(cx + vx * reach, cy + vy * reach))
    behind = not footprint.covers(Point(cx - vx * reach, cy - vy * reach))
    if ahead != behind:
        return 1 if ahead else -1
    if op.kind == "door" and op.leaves:
        return int(op.leaves[0].get("swing", 1)) or 1
    return 1


SAMPLE = 0.1  # m: spacing of the points at which the thickness of the perimeter wall is measured
OUTER_BAND = 1.5  # the outer half is looked for within this many wall thicknesses of the outside


def outer_half_of_perimeter_walls(solid: BaseGeometry, max_thickness: float) -> BaseGeometry | None:
    """The outer half of the walls that have the outside on one face: the part of each perimeter wall that lies
    between its middle surface and the outside. ``None`` when the walls enclose no space (no inner face to halve
    from) or no wall touches the outside.

    The thickness is measured at points along each enclosed space's outline (its distance to the building's
    outline, the median of them: corners and openings do not count), and the inner half is the space grown
    by half of it (square corners). Everything within reach of the outside and not in the inner half is the
    outer half. Walls between two rooms are never in it."""
    footprint = building_footprint(solid)
    free = footprint.difference(solid)
    if footprint.is_empty or free.is_empty:
        return None
    outline = footprint.boundary
    inner_half: list[BaseGeometry] = []
    thick = 0.0
    for cell in polygons_of(free):
        distances = []
        for ring in (cell.exterior, *cell.interiors):
            pts = list(ring.coords)
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                n = max(int(math.hypot(bx - ax, by - ay) / SAMPLE), 1)
                for k in range(n):
                    d = outline.distance(Point(ax + (bx - ax) * k / n, ay + (by - ay) * k / n))
                    if 1e-6 < d <= max_thickness:
                        distances.append(d)
        if len(distances) < 3:
            continue  # this space has no perimeter wall of its own
        t = statistics.median(distances)
        thick = max(thick, t)
        inner_half.append(cell.buffer(t / 2.0, join_style="mitre"))
    if not inner_half:
        return None
    inner = union(inner_half)
    shrunk = footprint.buffer(-thick * OUTER_BAND, join_style="mitre")
    band = (footprint.difference(shrunk) if not shrunk.is_empty else footprint).intersection(solid)
    outer = band.difference(inner)
    outer = union(p for p in polygons_of(outer) if p.area > 1e-4)
    return None if outer.is_empty else outer


def build_mesh(plan: Plan, cfg: Config, warnings: list[str]) -> Mesh:
    mesh = Mesh()
    walls = plan.solid_walls
    cutting = [o for o in plan.openings if o.keep]  # a dropped opening is closed with wall
    main = walls if plan.partitions.is_empty else walls.difference(plan.partitions)
    outer = outer_half_of_perimeter_walls(walls, cfg.max_wall_thickness) if cfg.wall_finishes else None
    if outer is not None:
        # Two solids: the half of the perimeter walls that faces the outside, and everything else (the half
        # that faces the rooms and the walls between rooms). Each is closed, with its own top and bottom.
        outer_main = outer.intersection(main)
        mesh.add_extrusion("Muri_esterno", wall_slabs(outer_main, cutting, cfg.wall_height), bottom=True)
        mesh.add_extrusion("Muri_interno", wall_slabs(main.difference(outer_main), cutting, cfg.wall_height),
                           bottom=True)
    else:
        mesh.add_extrusion("Muri", wall_slabs(main, cutting, cfg.wall_height), bottom=True)
    if not plan.partitions.is_empty:
        mesh.add_extrusion("Tramezzi", wall_slabs(plan.partitions, cutting, cfg.wall_height), bottom=True)

    if not plan.columns.is_empty:
        mesh.add_extrusion("Pilastri", [Slab(0.0, cfg.wall_height, plan.columns)], bottom=True)

    if cfg.fixtures == "detailed":
        add_fixtures(mesh, cutting, cfg)
    else:
        for o in cutting:
            if o.glass is not None:
                group = f"Vetri_{o.id}" if cfg.fixtures_per_opening and o.id else "Vetri"
                mesh.add_extrusion(group, [Slab(o.z0, o.z1, o.glass)], bottom=True)

    if plan.roof is not None:
        mesh.add_roof_solid("Tetto", plan.roof, z_base=cfg.wall_height, thickness=cfg.roof_thickness)

    if not plan.skirting.is_empty:
        mesh.add_extrusion("Battiscopa", [Slab(0.0, cfg.skirting_height, plan.skirting)], bottom=False)

    if plan.garden is not None:
        add_garden(mesh, plan.garden, cfg)

    by_room = cfg.floor_thickness > 0 and bool(plan.floors)
    for name, shape in plan.floors if by_room else []:
        mesh.add_extrusion(f"Pavimento_{name}", [Slab(-cfg.floor_thickness, 0.0, shape)])
    if (cfg.floor_thickness > 0 and not by_room) or cfg.ceiling:
        footprint = floor_footprint(walls)
        if footprint.is_empty:
            warnings.append("Pavimento/soffitto non generati: i muri non racchiudono uno spazio chiuso.")
        else:
            if cfg.floor_thickness > 0 and not by_room:
                mesh.add_extrusion("Pavimento", [Slab(-cfg.floor_thickness, 0.0, footprint)])
            if cfg.ceiling:
                mesh.add_extrusion(
                    "Soffitto",
                    [Slab(cfg.wall_height, cfg.wall_height + cfg.ceiling_thickness, footprint)],
                )
    return mesh
