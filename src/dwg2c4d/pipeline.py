"""End-to-end conversion: DWG/DXF in, OBJ (+MTL) out."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path

from shapely.geometry.base import BaseGeometry

from .config import UNIT_TO_METERS, Config
from .elevation import apply_elevation, find_elevation_zones, is_elevation_layer, read_elevation
from .export import to_model_dict, write_json
from .dwgfile import ConversionError, open_drawing
from .geom import union, polygons_of
from .model import Plan, build_mesh
from .objwriter import write_obj
from .openings import assign_ids, build_openings
from .reader import storeys, read_items
from .roof import build_roof, ridge_hints_from
from .walls import build_columns, build_wall_layers, clean_footprint
from .table import apply_table, read_table, write_table
from .labels import Room, apply_labels, find_rooms, text_scale, wall_height_from_rooms
from .texts import read_words
from .passages import find_passages
from .floors import layer_floors, name_floors, room_floors, skirting_items, skirting_strips, split_partitions
from .qa import plan_overlay, preview_3d, write_report


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
    plan: object | None = None  # the Plan (walls, partitions, floors...): for the check picture
    room_objects: list = field(default_factory=list)  # the Room objects (polygons)
    preview_path: Path | None = None
    overlay_path: Path | None = None
    report_path: Path | None = None
    images_note: str = ""  # why a picture was not made
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


GROUP_GAP = 3.0  # m: wall pieces closer than this are one building
MIN_GROUP_AREA = 1.0  # m2 of wall: smaller pieces are not worth a remark


def _wall_groups(solid: BaseGeometry, openings: list) -> list[dict]:
    """The walls as groups far apart: [{"area", "bounds", "openings"}], biggest first. A roof plan, a section
    or a second building drawn on the same layer is a group of its own; only the real plan has doors and
    windows on its walls."""
    pieces = polygons_of(solid)
    if len(pieces) < 2:
        return []
    groups = []
    for cluster in polygons_of(union(p.buffer(GROUP_GAP / 2.0) for p in pieces)):
        mine = [p for p in pieces if cluster.intersects(p)]
        area = sum(p.area for p in mine)
        if area < MIN_GROUP_AREA:
            continue
        shape = union(mine)
        groups.append({"area": area, "bounds": shape.bounds,
                       "openings": sum(1 for o in openings if shape.buffer(0.3).intersects(o.fill))})
    groups.sort(key=lambda g: -g["area"])
    return groups if len(groups) >= 2 else []


def _describe_groups(groups: list[dict], unit_scale: float) -> str:
    lines = []
    for n, g in enumerate(groups, 1):
        x0, y0, x1, y1 = g["bounds"]
        box = ",".join(f"{v / unit_scale:.0f}" for v in (x0 - 0.5, y0 - 0.5, x1 + 0.5, y1 + 0.5))
        lines.append(f"{n}) {x1 - x0:.1f} x {y1 - y0:.1f} m, {g['area']:.1f} m2 di muri, {g['openings']} "
                     f"porte/finestre: --area={box}")
    return "; ".join(lines)


def convert(input_path: str | Path, output_path: str | Path | None = None,
            cfg: Config | None = None) -> ConversionReport:
    cfg = cfg or Config()
    cfg.validate()
    input_path = Path(input_path)
    output_path = Path(output_path) if output_path else input_path.with_suffix(".obj")

    doc = open_drawing(input_path, cfg.converter)
    found = storeys(doc)
    if cfg.floor is not None and found and cfg.floor not in found:
        raise ConversionError(f"Il piano {cfg.floor} non esiste: i layer nominano i piani "
                              f"{', '.join(map(str, found))}.")
    result = read_items(doc, cfg)
    warnings = list(result.warnings)
    if len(found) > 1 and cfg.floor is None:
        warnings.append(f"I layer nominano {len(found)} piani ({', '.join(f'P{n}' for n in found)}): ho letto il "
                        f"piano {found[0]}. Per un altro usa --piano N.")

    layer_walls = build_wall_layers(result.items, cfg, warnings)
    walls = clean_footprint(union(layer_walls.values()), cfg.merge_tolerance)
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
    groups = _wall_groups(solid, openings)
    if groups:
        real = [g for g in groups if g["openings"] > 0]
        if cfg.area is None and 0 < len(real) < len(groups):
            # Only some groups have doors/windows: the others are a roof plan, a section... drawn on the same
            # layer. Read the drawing again cropped to the groups that are plans.
            margin = 1.0
            x0 = min(g["bounds"][0] for g in real) - margin
            y0 = min(g["bounds"][1] for g in real) - margin
            x1 = max(g["bounds"][2] for g in real) + margin
            y1 = max(g["bounds"][3] for g in real) + margin
            area = tuple(v / result.unit_scale for v in (x0, y0, x1, y1))
            dropped = [g for g in groups if g["openings"] == 0]
            fixed = convert(input_path, output_path, replace(cfg, area=area))
            fixed.warnings.insert(0, (
                f"Ho escluso {len(dropped)} gruppo/i di muri senza porte ne' finestre (di solito la pianta del tetto "
                f"o una sezione disegnate sullo stesso layer): {_describe_groups(dropped, result.unit_scale)}. "
                "Per includerli indica tu un'--area piu' grande."))
            return fixed
        note = " (c'e' gia' --area, ma include piu' di una zona)" if cfg.area is not None else ""
        warnings.append(f"I muri formano {len(groups)} gruppi distanti{note}. Se solo uno e' la pianta (gli altri "
                        "sono tetto, sezioni, un altro edificio disegnati sullo stesso layer) convertilo da solo "
                        f"con la sua area: {_describe_groups(groups, result.unit_scale)}")
    if cfg.passages:
        passages = find_passages(solid, cfg, openings)
        if passages:
            openings.extend(passages)
            assign_ids(openings)
            plan.__dict__.pop("solid_walls", None)  # the lintels over the passages close the footprint
            solid = plan.solid_walls

    words = read_words(doc, cfg, result.unit_scale) if cfg.texts else []
    text_unit = text_scale(words)
    rooms = find_rooms(solid, words, text_unit, warnings)
    wall_height_source = "indicata" if not cfg.wall_height_auto else "predefinita"
    written = wall_height_from_rooms(rooms, warnings) if cfg.texts else None
    if written is not None and cfg.wall_height_auto:
        cfg = replace(cfg, wall_height=written, wall_height_auto=False)
        wall_height_source = "scritta"
        for o in openings:
            o.z1 = min(o.z1, cfg.wall_height)

    elevations = []
    elevation_report: list[dict] = []
    specs = [(spec, "") for spec in cfg.elevations]
    if not specs and cfg.elevations_auto:
        everything = read_items(doc, cfg, area=None, ignore_veto=True, keep_other=True, unit=result.unit)
        specs = [(zone, layers) for layers, zone in find_elevation_zones(everything.items, solid.bounds,
                                                                      result.unit_scale)]
    for i, (spec, found_on) in enumerate(specs):
        er = read_items(doc, cfg, area=tuple(spec[:4]), ignore_veto=True, keep_other=True,
                        unit=result.unit)
        ev = read_elevation(er.items, spec, result.unit_scale, solid.bounds, warnings, i)
        if ev is None:
            continue
        elevations.append(ev)
        matched, total = apply_elevation(ev, openings, solid, cfg, warnings)
        elevation_report.append({"side": ev.side, "matched": matched, "total": total,
                                 "zero_source": ev.zero_source, **({"found_on": found_on} if found_on else {})})

    labels = apply_labels(openings, words, cfg, text_unit, solid.bounds, warnings) if words else 0

    if cfg.table_in:
        rows = read_table(cfg.table_in)
        changed = apply_table(openings, rows, cfg, warnings)
        if changed:
            plan.__dict__.pop("solid_walls", None)  # widths/kinds changed: the filled walls too
            solid = plan.solid_walls
            rooms = find_rooms(solid, words, text_unit, [])

    if cfg.floors_by_room and cfg.floor_thickness > 0:
        regions = layer_floors(result.items)
        if regions:
            plan.floors = name_floors(regions, rooms)
        elif rooms and (len(rooms) > 1 or rooms[0].named):
            plan.floors = room_floors(rooms, openings)
    if skirting_items(result.items):
        plan.skirting = skirting_strips(result.items, solid, openings, cfg)
    part_layers = {k: g for k, g in layer_walls.items() if cfg.layers.is_partition(k)}
    if cfg.partitions_apart and part_layers and len(part_layers) < len(layer_walls):
        main = union(g for k, g in layer_walls.items() if k not in part_layers)
        plan.partitions = split_partitions(solid, main, union(part_layers.values()), openings)

    roof_report = None
    roof_items = None
    if cfg.roof or cfg.roof_auto:
        rr = read_items(doc, cfg, area=cfg.roof_area, unit=result.unit)
        roof_items = rr.items if cfg.roof else [it for it in rr.items if it.category == "roof"
                                               and not is_elevation_layer(it.layer)]
        if not cfg.roof and not roof_items:
            roof_items = None  # nothing asked, nothing drawn: no roof and no remark
    if roof_items is not None:
        roof = build_roof(roof_items, cfg, result.unit_scale, solid.bounds,
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

    report = ConversionReport(
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
        plan=plan,
        room_objects=rooms,
    )
    created = [output_path.name, output_path.with_suffix(".mtl").name]
    created += [p.name for p in (json_path, table_path) if p]
    if cfg.images:
        report.preview_path = preview_3d(mesh, output_path.with_name(output_path.stem + "_anteprima_3d.png"))
        created.append(report.preview_path.name)
        try:
            report.overlay_path = plan_overlay(report, output_path.with_name(output_path.stem + "_controllo_pianta.png"))
            created.append(report.overlay_path.name)
        except ImportError:
            report.images_note = "pianta di controllo non creata: serve matplotlib (pip install matplotlib)"
        except Exception as exc:  # a picture must never stop the conversion
            report.images_note = f"pianta di controllo non creata: {exc}"
    report.report_path = write_report(report, output_path.with_name(output_path.stem + "_report.txt"),
                                      created + [output_path.stem + "_report.txt"])
    return report
