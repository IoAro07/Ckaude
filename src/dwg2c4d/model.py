"""Assemble the 3D mesh from the 2D plan elements."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from .config import Config
from .geom import polygons_of, union
from .fixtures import add_fixtures
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


def build_mesh(plan: Plan, cfg: Config, warnings: list[str]) -> Mesh:
    mesh = Mesh()
    walls = plan.solid_walls
    cutting = [o for o in plan.openings if o.keep]  # a dropped opening is closed with wall
    if plan.partitions.is_empty:
        mesh.add_extrusion("Muri", wall_slabs(walls, cutting, cfg.wall_height), bottom=False)
    else:
        mesh.add_extrusion("Muri", wall_slabs(walls.difference(plan.partitions), cutting, cfg.wall_height),
                           bottom=False)
        mesh.add_extrusion("Tramezzi", wall_slabs(plan.partitions, cutting, cfg.wall_height), bottom=False)

    if not plan.columns.is_empty:
        mesh.add_extrusion("Pilastri", [Slab(0.0, cfg.wall_height, plan.columns)], bottom=False)

    if cfg.fixtures == "detailed":
        add_fixtures(mesh, cutting, cfg)
    else:
        for o in cutting:
            if o.glass is not None:
                mesh.add_extrusion("Vetri", [Slab(o.z0, o.z1, o.glass)], bottom=True)

    if plan.roof is not None:
        mesh.add_roof_solid("Tetto", plan.roof, z_base=cfg.wall_height, thickness=cfg.roof_thickness)

    if not plan.skirting.is_empty:
        mesh.add_extrusion("Battiscopa", [Slab(0.0, cfg.skirting_height, plan.skirting)], bottom=False)

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
