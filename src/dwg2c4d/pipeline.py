"""End-to-end conversion: DWG/DXF in, OBJ (+MTL) out."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path

from shapely.geometry.base import BaseGeometry

from .config import UNIT_TO_METERS, Config
from .elevation import apply_elevation, read_elevation
from .export import to_model_dict, write_json
from .dwgfile import ConversionError, open_drawing
from .geom import polygons_of
from .model import Plan, build_mesh
from .objwriter import write_obj
from .openings import assign_ids, build_openings
from .reader import read_items
from .roof import build_roof, ridge_hints_from
from .walls import build_columns, build_walls
from .table import apply_table, read_table, write_table
from .labels import Room, apply_labels, find_rooms, text_scale, wall_height_from_rooms
from .texts import read_words
from .passages import find_passages


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
    passages: int
    columns: int
    faces: int
    groups: list[str]
    warnings: list[str] = field(default_factory=list)
    origin_offset: tuple[float, float] = (0.0, 0.0)  # metres added to the drawing coordinates
    json_path: Path | None = None
    table_path: Path | None = None  # the openings table written next to the model
    rooms: list[dict] = field(default_factory=list)  # name, area_m2, height (m or None)
    labels: int = 0  # openings whose size/sill was read from a written text
    wall_height: float = 0.0
    wall_height_source: str = "predefinita"  # predefinita | indicata | scritta
    openings: list = field(default_factory=list)  # the Opening objects, with ids
    mesh: object | None = None  # the final Mesh (for previews); not part of the printed summary
    elevations: list[dict] = field(default_factory=list)  # side, matched, total, zero source
    roof: dict | None = None  # pitch_deg, pitch_source, ridge_height, faces


def _extent(geom: BaseGeometry) -> tuple[float, float]:
    x0, y0, x1, y1 = geom.bounds
    return x1 - x0, y1 - y0


def _better_unit(span_m: float, declared: str) -> str | None:
    """A unit other than the declared one for which a building of ``span_m`` (read with the
    declared unit) would measure between 3 and 300 m; the one closest to a typical size wins."""
    best, best_gap = None, math.inf
    for unit, scale in UNIT_TO_METERS.items():
        if unit in ("in", "ft") or unit == declared:
            continue
        size = span_m * scale / UNIT_TO_METERS[declared]
        if 3.0 <= size <= 300.0:
            gap = abs(math.log(size / 15.0))
            if gap < best_gap:
                best, best_gap = unit, gap
    return best


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
    if not (3.0 <= span <= 300.0) and cfg.units is None and not result.unit_guessed:
        alt = _better_unit(span, result.unit)
        if alt:
            fixed = convert(input_path, output_path, replace(cfg, units=alt))
            fixed.warnings.insert(0, (
                f"Il file dichiara '{result.unit}' ma cosi' l'edificio misurerebbe {span:.2f} m: "
                f"le misure sono compatibili con '{alt}', che ho usato. Forza l'unita' con --unita se non va bene."))
            return fixed
    if not (3.0 <= span <= 300.0):
        warnings.append(
            f"Dimensioni insolite per un edificio: {_extent(walls)[0]:.2f} x {_extent(walls)[1]:.2f} m "
            f"(unita' del disegno lette: {result.unit}). Se non tornano, forza l'unita' con --unita "
            "(mm, cm, m)."
        )
    plan = Plan(walls=walls, columns=columns, openings=openings, merge_tolerance=cfg.merge_tolerance)
    solid = plan.solid_walls
    if cfg.passages:
        passages = find_passages(solid, cfg, openings)
        if passages:
            openings.extend(passages)
            assign_ids(openings)
            plan.__dict__.pop("solid_walls", None)  # the lintels over the passages close the footprint
            solid = plan.solid_walls

    words, rooms = [], []
    text_unit = 0.01
    wall_height_source = "indicata" if not cfg.wall_height_auto else "predefinita"
    if cfg.texts:
        words = read_words(doc, cfg, result.unit_scale)
        text_unit = text_scale(words)
        rooms = find_rooms(solid, words, text_unit, warnings)
        written = wall_height_from_rooms(rooms, warnings)
        if written is not None and cfg.wall_height_auto:
            cfg = replace(cfg, wall_height=written, wall_height_auto=False)
            wall_height_source = "scritta"
            for o in openings:
                o.z1 = min(o.z1, cfg.wall_height)

    elevations = []
    elevation_report: list[dict] = []
    for i, spec in enumerate(cfg.elevations):
        er = read_items(doc, cfg, area=tuple(spec[:4]), ignore_veto=True, keep_other=True,
                        unit=result.unit)
        ev = read_elevation(er.items, spec, result.unit_scale, solid.bounds, warnings, i)
        if ev is None:
            continue
        elevations.append(ev)
        matched, total = apply_elevation(ev, openings, solid, cfg, warnings)
        elevation_report.append({"side": ev.side, "matched": matched, "total": total,
                                 "zero_source": ev.zero_source})

    labels = apply_labels(openings, words, cfg, text_unit, solid.bounds, warnings) if words else 0

    if cfg.table_in:
        rows = read_table(cfg.table_in)
        changed = apply_table(openings, rows, cfg, warnings)
        if changed:
            plan.__dict__.pop("solid_walls", None)  # widths/kinds changed: the filled walls too
            solid = plan.solid_walls

    roof_report = None
    if cfg.roof:
        rr = read_items(doc, cfg, area=cfg.roof_area, unit=result.unit)
        roof = build_roof(rr.items, cfg, result.unit_scale, solid.bounds,
                          ridge_hints_from(elevations, cfg.wall_height), warnings)
        if roof is not None:
            plan.roof = roof
            roof_report = {"pitch_deg": roof.pitch_deg, "pitch_range": roof.pitch_range, "pitch_source": roof.pitch_source,
                           "ridge_height": roof.ridge_height + cfg.wall_height, "faces": roof.faces,
                           "ridges_from_elevation": roof.ridges_from_elevation}

    mesh = build_mesh(plan, cfg, warnings)
    offset = (0.0, 0.0)
    if cfg.origin != "drawing":
        x0, y0, x1, y1 = solid.bounds
        offset = (-(x0 + x1) / 2, -(y0 + y1) / 2) if cfg.origin == "center" else (-x0, -y0)
        mesh.translate_xy(*offset)
    write_obj(mesh, output_path, cfg.out_units, cfg.mirror)
    json_path = None
    if cfg.c4d_json:
        json_path = write_json(to_model_dict(mesh, output_path.stem, offset),
                               output_path.with_name(output_path.stem + "_model.json"))

    table_path = None
    if cfg.write_table and openings:
        table_path = output_path.with_name(output_path.stem + "_aperture.csv")
        if cfg.table_in and Path(cfg.table_in).resolve() == table_path.resolve():
            table_path = output_path.with_name(output_path.stem + "_aperture_nuova.csv")  # never overwrite the input
        write_table(table_path, openings, result.unit_scale)

    return ConversionReport(
        output=output_path,
        unit=result.unit,
        unit_guessed=result.unit_guessed,
        size_m=_extent(solid),
        wall_area_m2=solid.area,
        wall_pieces=len(polygons_of(solid)),
        doors=sum(o.kind == "door" for o in openings),
        passages=sum(o.kind == "passage" for o in openings),
        windows=sum(o.kind == "window" for o in openings),
        columns=len(polygons_of(columns)),
        faces=mesh.face_count,
        groups=list(mesh.groups),
        warnings=warnings,
        elevations=elevation_report,
        roof=roof_report,
        origin_offset=offset,
        json_path=json_path,
        table_path=table_path,
        rooms=[{"name": r.name, "area_m2": r.area, "height": r.height, "named": r.named} for r in rooms],
        labels=labels,
        wall_height=cfg.wall_height,
        wall_height_source=wall_height_source,
        mesh=mesh,
        openings=openings,
    )
