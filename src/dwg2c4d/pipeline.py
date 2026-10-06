"""End-to-end conversion: DWG/DXF in, OBJ (+MTL) out."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from shapely.geometry.base import BaseGeometry

from .config import Config
from .dwgfile import ConversionError, open_drawing
from .geom import polygons_of
from .model import Plan, build_mesh
from .objwriter import write_obj
from .openings import build_openings
from .reader import read_items
from .walls import build_columns, build_walls


@dataclass
class ConversionReport:
    output: Path
    unit: str
    unit_guessed: bool
    size_m: tuple[float, float]  # plan extents (x, y), metres
    wall_area_m2: float
    wall_pieces: int
    doors: int
    windows: int
    columns: int
    faces: int
    groups: list[str]
    warnings: list[str] = field(default_factory=list)


def _extent(geom: BaseGeometry) -> tuple[float, float]:
    x0, y0, x1, y1 = geom.bounds
    return x1 - x0, y1 - y0


def convert(input_path: str | Path, output_path: str | Path | None = None,
            cfg: Config | None = None) -> ConversionReport:
    cfg = cfg or Config()
    cfg.validate()
    input_path = Path(input_path)
    output_path = Path(output_path) if output_path else input_path.with_suffix(".obj")

    doc = open_drawing(input_path, cfg.converter)
    result = read_items(doc, cfg)
    warnings = list(result.warnings)

    walls = build_walls(result.items, cfg, warnings)
    if walls.is_empty:
        found = sorted({it.layer for it in result.items})
        raise ConversionError(
            "Nessun muro riconosciuto. Controlla i nomi dei layer con --elenca-layer e "
            "indicali con --muri \"NOME1,NOME2\"."
            + (f" Layer riconosciuti come porte/finestre/pilastri: {', '.join(found)}." if found else "")
        )
    columns = build_columns(result.items, cfg)
    if not columns.is_empty:
        columns = columns.difference(walls)
    openings = build_openings(result.items, walls, cfg, warnings)

    span = max(_extent(walls))
    if not (3.0 <= span <= 300.0):
        warnings.append(
            f"Dimensioni insolite per un edificio: {_extent(walls)[0]:.2f} x {_extent(walls)[1]:.2f} m "
            f"(unita' del disegno lette: {result.unit}). Se non tornano, forza l'unita' con --unita "
            "(mm, cm, m)."
        )
    plan = Plan(walls=walls, columns=columns, openings=openings, merge_tolerance=cfg.merge_tolerance)
    solid = plan.solid_walls
    mesh = build_mesh(plan, cfg, warnings)
    write_obj(mesh, output_path, cfg.out_units, cfg.mirror)

    return ConversionReport(
        output=output_path,
        unit=result.unit,
        unit_guessed=result.unit_guessed,
        size_m=_extent(solid),
        wall_area_m2=solid.area,
        wall_pieces=len(polygons_of(solid)),
        doors=sum(o.kind == "door" for o in openings),
        windows=sum(o.kind == "window" for o in openings),
        columns=len(polygons_of(columns)),
        faces=mesh.face_count,
        groups=list(mesh.groups),
        warnings=warnings,
    )
