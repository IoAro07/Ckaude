"""End-to-end conversion: DWG/DXF in, OBJ (+MTL) out."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from .autodetect import _KIND_IT, analyze, apply_analysis, other_view_boxes, site_plan_asked, write_views_image
from .config import UNIT_TO_METERS, Config
from .elevation import apply_elevation, find_elevation_zones, is_elevation_layer, read_elevation
from .export import to_model_dict, write_json
from .dwgfile import ConversionError, open_drawing
from .geom import union, polygons_of
from .mesh import Mesh
from .model import OTHER_FLOOR, Plan, build_mesh, building_footprint, facing_sign, floor_footprint
from .stacking import (DEFAULT_SLAB, Floor, Stack, consensus_levels, consensus_slabs, find_storeys, fit_openings, moved, outline,
                       place_storeys, register, stack_mesh, stacking_on, storey_of_view)
from .objwriter import write_obj
from .glazing import ribbon_windows
from .thinwalls import THICKNESS, single_line_walls
from .openings import assign_ids, build_openings
from .reader import layer_used, storeys, read_items
from .roof import FlatRoof, Roof, build_flat_roof, build_roof, ridge_hints_from
from .roofview import flat_roof_notes, roof_from_view, view_roof_notes, written_pitch
from .falsewalls import FalseWalls, lower_far_walls, separate_prims
from .walls import build_columns, build_wall_layers, clean_footprint
from .table import apply_table, read_table, rows_of_storey, write_table
from .labels import Room, apply_labels, find_rooms, text_scale, wall_height_from_rooms
from .lowheights import written_heights
from .texts import read_texts, read_words
from .passages import find_passages, wall_gaps
from .elevmatch import agreed_height, match_known, match_views, storey_stated
from .garden import build_garden, place_on_terrain
from .garden_table import apply_garden_table, read_garden_table, write_garden_table
from .lotfence import detect_lot_fence, finish_lot_fence, without_fence_layers
from .floors import extend_to_outer_faces, floor_left_over, layer_floors, name_floors, room_floors, skirting_items, skirting_strips, split_partitions
from .qa import plan_overlay, preview_3d, write_report
from .mesh import storey_tag
from .terrain import build_terrain
from .terrainmesh import add_terrain
from .sitemode import Site, build_site
from .sitesurfaces import SiteSurfaces, build_site_surfaces


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
    wall_height_source: str = "predefinita"  # predefinita | indicata | scritta | prospetto
    plan: object | None = None  # the Plan (walls, partitions, floors...): for the check picture
    false_walls: object | None = None  # the falsewalls.FalseWalls: furniture left out, low walls, lone columns
    room_objects: list = field(default_factory=list)  # the Room objects (polygons)
    preview_path: Path | None = None
    overlay_path: Path | None = None
    report_path: Path | None = None
    images_note: str = ""  # why a picture was not made
    openings: list = field(default_factory=list)  # the Opening objects, with ids
    mesh: object | None = None  # the final Mesh (for previews); not part of the printed summary
    elevations: list[dict] = field(default_factory=list)  # side, matched, total, zero source
    roof: dict | None = None  # pitch_deg, pitch_source, ridge_height, faces
    flat_roof: dict | None = None  # area_m2, bodies, thickness, parapet_height
    garden: object | None = None  # the Garden: ground, pools, plants, furniture
    lot: object | None = None  # the lotfence.LotFence: wall, pillars, railing and wire fence round the lot
    terrain: dict | None = None  # the terrain from the contour lines: layer, levels, step, floor level, size...
    site_surfaces: SiteSurfaces | None = None  # the ground round the building read from the site plan (sitesurfaces): paving, lawn, road...
    garden_table_path: Path | None = None
    site: Site | None = None  # the buildings of a site, read from the roofs of its site plan (sitemode): a sheet with no plan of a building
    analysis: object | None = None  # the autodetect.Analysis: unit, views, the plan chosen
    views_path: Path | None = None
    floors: list[Floor] = field(default_factory=list)  # the storeys of a stacked model, the converted plan first (none: one plan)
    cfg: Config | None = None  # the Config in use after the analysis (an upper storey of a stack: its caller builds the mesh with it)
    overlay_paths: list[Path] = field(default_factory=list)  # the control pictures of the upper storeys


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


SURE_PAIRS = 3  # an elevation that pairs this many symbols with openings of the top storey is sure enough of its facade to give its eaves
STOREY_OFF = dict(garden=False, roof=False, roof_auto=False, images=False, c4d_json=False, write_table=False,
                  write_garden_table=False, garden_table_in=None, area=None, roof_area=None, roof_offset=None)  # what an upper storey leaves to the base
GARDEN_SKIPPED_KINDS = ("elevation", "section", "roof", "detail")  # views whose contents are never garden, even with --area
PREVIEW_MARGIN = 15.0  # m: the 3D preview shows the terrain this far round the building
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


def _read_walls(items: list, cfg: Config, warnings: list[str]) -> tuple[BaseGeometry, dict[str, BaseGeometry], FalseWalls]:
    """(the walls, the footprint of each wall layer, what was taken out of them). Furniture, stairs, curbs and lot lines on
    the layers the program chose are not walls (see falsewalls); --muri and --modalita-muri say what is a wall, then nothing
    is told apart, and the partition layers the names vouch for are never touched."""
    if cfg.separate_walls and cfg.walls_guessed and cfg.wall_mode == "auto":
        vouched = frozenset(it.layer for it in items if it.category == "wall" and cfg.layers.is_partition(it.layer))
        own: list[str] = []
        kept, false = separate_prims(items, vouched)
        layer_walls = build_wall_layers(kept, cfg, own)
        walls = clean_footprint(union(layer_walls.values()), cfg.merge_tolerance)
        keep = union(g for k, g in layer_walls.items() if k in vouched)
        site = cfg.analysis is not None and site_plan_asked(cfg.analysis, cfg)  # a site plan the user asked for
        walls = clean_footprint(lower_far_walls(walls, items, false, keep, site), cfg.merge_tolerance)
        if not walls.is_empty:  # a drawing of nothing but furniture is read as it is: no wall at all is no model
            warnings.extend(own)
            warnings.extend(false.notes(cfg.low_wall_height, cfg.curb_height))
            return walls, layer_walls, false
    layer_walls = build_wall_layers(items, cfg, warnings)
    return clean_footprint(union(layer_walls.values()), cfg.merge_tolerance), layer_walls, FalseWalls()


def _build_roof(doc, cfg: Config, result, solid: BaseGeometry, elevations: list,
                warnings: list[str]) -> tuple[Roof | None, FlatRoof | None, dict | None, dict | None]:
    """(the pitched roof, the flat roof, their lines of the report). From the layers the names call roof (or --layer-tetto):
    they always win. When they give none (no such layer, or only the lines of a tile pattern) and the sheet draws a roof
    plan as a view of its own, from that view, cleaned and laid over the plan (roofview): never when the user said where
    the roof is (--layer-tetto, --area-tetto, --sposta-tetto)."""
    if not (cfg.roof or cfg.roof_auto):
        return None, None, None, None
    own: list[str] = []
    roof, flat, view, overhang = None, None, None, None
    rr = read_items(doc, cfg, area=cfg.roof_area, unit=result.unit)
    items = rr.items if cfg.roof else [it for it in rr.items if it.category == "roof" and not is_elevation_layer(it.layer)]
    if items or cfg.roof:  # nothing asked, nothing drawn: no roof and no remark
        roof = build_roof(items, cfg, result.unit_scale, solid.bounds, ridge_hints_from(elevations, cfg.wall_height), own)
    stated = "roof" in cfg.layers.overrides or cfg.roof_area is not None or cfg.roof_offset is not None
    if roof is None and not stated and cfg.analysis is not None and not site_plan_asked(cfg.analysis, cfg):
        try:
            found = roof_from_view(doc, cfg, cfg.analysis, union([building_footprint(solid), floor_footprint(solid)]), result.unit,
                                   result.unit_scale, own)
        except Exception as exc:  # the roof plan of the sheet is a help: whatever goes wrong in it is never the reason a conversion fails
            found = None
            own.append(f"Tetto dalla vista della copertura non costruito: {type(exc).__name__}: {exc}. Il resto della conversione e' "
                       "completo; per il tetto indica i layer con --layer-tetto.")
        if found is not None:
            pitch = written_pitch(cfg.analysis)
            if found.items:
                roof = build_roof(found.items, replace(cfg, roof_offset=(0.0, 0.0)), result.unit_scale, solid.bounds,
                                  ridge_hints_from(elevations, cfg.wall_height), own, pitch[0] if pitch else None)
            flat = build_flat_roof(found.flats, elevations, cfg.wall_height, cfg.roof_thickness)
            if roof is not None or flat is not None:
                view, overhang = found.view, found.overhang
                own = [n for n in own if not n.startswith(("Tetto: le linee del tetto non formano", "Tetto: nessuna linea trovata"))]
                own.extend(view_roof_notes(found, roof, pitch, elevations, cfg.wall_height))
                if flat is not None:
                    own.extend(flat_roof_notes(flat))
    warnings.extend(own)
    report = None if roof is None else {
        "pitch_deg": roof.pitch_deg, "pitch_range": roof.pitch_range, "pitch_source": roof.pitch_source,
        "ridge_height": roof.ridge_height + cfg.wall_height, "faces": roof.faces,
        "ridges_from_elevation": roof.ridges_from_elevation, "view": view, "overhang": overhang}
    flat_report = None if flat is None else {
        "area_m2": flat.slab.area, "bodies": flat.bodies, "thickness": flat.thickness, "view": view,
        "parapet_height": flat.parapet_height if flat.parapet is not None else None, "parapet_source": flat.parapet_source,
        "modules": len(flat.modules)}
    return roof, flat, report, flat_report


def _counts(solid: BaseGeometry, openings: list, columns: BaseGeometry) -> dict:
    """What the report says of a plan: its extent, its walls, its doors and windows, its columns."""
    return dict(size_m=_extent(solid), wall_area_m2=solid.area, wall_pieces=len(polygons_of(solid)),
                doors=sum(o.kind == "door" for o in openings), passages=sum(o.kind == "passage" for o in openings),
                windows=sum(o.kind == "window" for o in openings), columns=len(polygons_of(columns)))


def _room_rows(rooms: list[Room]) -> list[dict]:
    return [{"name": r.name, "area_m2": r.area, "height": r.height, "named": r.named} for r in rooms]


def _known_elevations(doc, up: "ConversionReport", view, cfg: Config, known: list[dict], offset: tuple[float, float]) -> list[dict]:
    """The elevations the plan below matched that the conversion of this storey did not: placed on its plan like on the one below
    (elevmatch.match_known), so a row of two or three windows an upper floor shows is not lost for lack of evidence."""
    matched = {e["view"] for e in up.elevations}
    try:
        return match_known(doc, up.cfg, replace(cfg.analysis, plan=view), up.openings, up.plan.solid_walls, up.unit,
                           UNIT_TO_METERS[up.unit], [e for e in known if e["view"] not in matched], offset, up.warnings)
    except Exception as exc:  # the elevations drawn elsewhere are a help: never the reason a conversion fails
        up.warnings.append(f"Prospetti del piano sotto non riportati su questo piano ({type(exc).__name__}: {exc}).")
        return []


def _stack_storeys(doc, input_path: Path, output_path: Path, cfg: Config, stack: Stack, plan: Plan, openings: list,
                   elevation_report: list[dict], height_source: str, warnings: list[str]) -> list[Floor]:
    """The storeys of the stack, lowest first (none: nothing above the converted plan could be stacked, and the warnings say
    why). Each storey above is converted with the usual machinery (``Config.part``: from the user's own options, with the
    analysis of the sheet pointed at its view), laid over the one below by its outline (stacking.register), and given its
    height and level (stacking.place_storeys). One that fails or does not register ends the stack: the ones above it would hang
    in the air."""
    base = Floor(stack.base_index, cfg.analysis.plan.id, stack.base_how, plan, cfg, openings, height_source=height_source)
    floors = [base]
    below, below_walls = outline(plan.solid_walls), plan.solid_walls
    seen = {v.id: v.matched for v in cfg.analysis.views}
    for cand in stack.candidates:
        part_cfg = replace(cfg.user, view=cand.view.id, part=True, storey=cand.index, stacking="no", elevations=[], **STOREY_OFF)
        label = f"Piano {cand.index} (vista {cand.view.id}, {cand.how})"
        try:
            up = convert(input_path, output_path, part_cfg, _doc=doc, _analysis=replace(cfg.analysis, plan=cand.view, notes=[]))
            reg, why = register(outline(up.plan.solid_walls), below, cfg.stacking == "yes", up.plan.solid_walls, below_walls)
        except Exception as exc:  # the other floors are a help: whatever goes wrong in them is never the reason a conversion fails
            reg, why = None, f"{type(exc).__name__}: {exc}"
        if reg is None:
            warnings.append(f"{label} non impilato: {why}. Il modello arriva fino al piano {floors[-1].index}."
                            + ("" if cfg.stacking == "yes" else " Se sono piani dello stesso edificio puoi imporlo con --piani."))
            break
        for o in up.openings:
            o.storey, o.shift = cand.index, reg.shift
        up.elevations.extend(_known_elevations(doc, up, cand.view, cfg, [e for e in elevation_report if e.get("normal")], reg.shift))
        floors.append(Floor(cand.index, cand.view.id, cand.how, up.plan, up.cfg, up.openings, reg.shift, reg.iou,
                            height_source=up.wall_height_source, report=up))
        cand.view.storey = cand.index  # the picture of the sheet shows which views are floors of the model
        warnings.extend(f"Piano {cand.index}: {w}" for w in up.warnings if w not in warnings)
        below, below_walls = moved(outline(up.plan.solid_walls), reg.shift), moved(up.plan.solid_walls, reg.shift)
        for v in cfg.analysis.views:  # the elevations the base matched say so; what this storey matched is told after it
            if v.matched != seen[v.id]:
                v.matched = f"{seen[v.id]}; piano {cand.index}: {v.matched}" if seen[v.id] else f"piano {cand.index}: {v.matched}"
                seen[v.id] = v.matched
    if len(floors) == 1:
        return []
    for o in openings:
        o.storey = base.index
    top = floors[-1]
    if top.height_source == "predefinita" and cfg.user.wall_height_auto:  # the roof stands on it: the elevations know where its walls end
        agreed = agreed_height([(e["confidence"] == "alta" or e["matched"] >= SURE_PAIRS, e["storey_height"])
                                for e in top.report.elevations if e.get("storey_height")])
        if agreed is not None:
            top.height, top.height_source = agreed, "prospetto"
    notes = place_storeys(floors, consensus_levels([e["levels"] for e in elevation_report if e.get("levels")]),
                          cfg.slab_thickness or DEFAULT_SLAB, not cfg.user.wall_height_auto,
                          [] if cfg.slab_thickness else consensus_slabs([e["slabs"] for e in elevation_report if e.get("slabs")]))
    fit_openings(floors)
    warnings.extend(notes)
    return floors


def _site_surfaces(doc, cfg: Config, result, solid: BaseGeometry, garden, lot, warnings: list[str]) -> SiteSurfaces | None:
    """The surfaces of the site plan that draws a copy of the plan (sitesurfaces), or None. What the garden and the fence of the lot
    already cover is left to them. It is a help: whatever goes wrong in it is never the reason a conversion fails."""
    if not cfg.site_surfaces or cfg.analysis is None or cfg.analysis.plan is None or site_plan_asked(cfg.analysis, cfg):
        return None
    taken = union([*(garden.surfaces.values() if garden is not None else []), *(garden.shells if garden is not None else []),
                   lot.footprint if lot is not None else Polygon()])
    try:
        return build_site_surfaces(doc, cfg, cfg.analysis, cfg.analysis.plan, result.unit_scale,
                                   union([building_footprint(solid), floor_footprint(solid)]), taken, warnings)
    except Exception as exc:
        warnings.append(f"Superfici del sito non costruite: {type(exc).__name__}: {exc}. Il resto della conversione e' completo; "
                        "per escluderle usa --no-superfici-sito.")
        return None


def _build_terrain(doc, cfg: Config, result, solid: BaseGeometry, columns: BaseGeometry, garden,
                   warnings: list[str]):
    """The terrain from the contour lines of the sheet (terrain.py), or None. None as well when the plan converted is a site
    plan the user asked for (--vista on it): all its linework would be taken for a building and flattened, unless --terreno.
    It is a help: whatever goes wrong in it is never the reason a conversion fails."""
    if not cfg.terrain and cfg.analysis is not None and site_plan_asked(cfg.analysis, cfg):
        return None
    footprint = building_footprint(solid)
    parts = union([footprint, floor_footprint(solid), columns])
    pools = union(garden.shells) if garden is not None else Polygon()
    try:
        ground = build_terrain(doc, cfg, cfg.analysis, result.unit_scale, footprint, parts, pools, warnings)
        if ground is not None:
            add_terrain(Mesh(), ground)  # the mesh must work out before the walls are built to stand on it
    except Exception as exc:
        warnings.append(f"Terreno non costruito: {type(exc).__name__}: {exc}. Il resto della conversione e' completo; per "
                        "escluderlo usa --no-terreno.")
        return None
    if ground is not None and garden is not None:
        place_on_terrain(garden.objects, ground)
    return ground


def _convert_site(doc, output_path: Path, cfg: Config) -> ConversionReport | None:
    """The conversion of a sheet that draws no plan of a building but the roofs of its buildings in a site plan (sitemode):
    volumes and roofs, written as any other conversion. None when the site plan draws no roofs."""
    analysis = cfg.analysis
    warnings = [n for n in cfg.analysis_notes if "non ho capito quale sia la pianta" not in n]  # (there is none, and none is needed)
    site = build_site(doc, cfg, analysis, warnings)
    if site is None:
        return None
    mesh = site.mesh
    x0, y0, x1, y1 = site.bounds
    offset = (0.0, 0.0)
    if cfg.origin != "drawing":
        offset = (-(x0 + x1) / 2, -(y0 + y1) / 2) if cfg.origin == "center" else (-x0, -y0)
        mesh.translate_xy(*offset)
    write_obj(mesh, output_path, cfg.out_units, cfg.mirror)
    json_path = None
    if cfg.c4d_json:
        json_path = write_json(to_model_dict(mesh, output_path.stem, offset, site.openings), output_path.with_name(output_path.stem + "_model.json"))
    walls = site.plan.solid_walls
    report = ConversionReport(
        output=output_path, unit=analysis.unit.unit, unit_guessed=analysis.unit.declared is None,
        size_m=(x1 - x0, y1 - y0), wall_area_m2=walls.area, wall_pieces=len(site.buildings),
        doors=sum(o.kind == "door" for o in site.openings), windows=sum(o.kind == "window" for o in site.openings),
        passages=0, columns=0, faces=mesh.face_count, groups=list(mesh.groups), warnings=warnings, origin_offset=offset,
        json_path=json_path, wall_height=max(b.eave_height or 0.0 for s in site.buildings for b in s.bodies),
        wall_height_source="predefinita" if any(b.eave_source == "predefinita" for s in site.buildings for b in s.bodies) else "prospetto",
        plan=site.plan, mesh=mesh, openings=site.openings, site=site, analysis=analysis, cfg=cfg)
    created = [output_path.name, output_path.with_suffix(".mtl").name] + ([json_path.name] if json_path else [])
    if cfg.images:
        try:
            report.views_path = write_views_image(analysis, output_path.with_name(output_path.stem + "_viste.png"))
            created.append(report.views_path.name)
        except Exception as exc:  # a picture must never stop the conversion
            report.images_note = f"immagine delle viste non creata: {exc}"
        focus = (x0 + offset[0] - PREVIEW_MARGIN, x1 + offset[0] + PREVIEW_MARGIN, y0 + offset[1] - PREVIEW_MARGIN,
                 y1 + offset[1] + PREVIEW_MARGIN)
        report.preview_path = preview_3d(mesh, output_path.with_name(output_path.stem + "_anteprima_3d.png"), focus)
        created.append(report.preview_path.name)
    report.report_path = write_report(report, output_path.with_name(output_path.stem + "_report.txt"), created + [output_path.stem + "_report.txt"])
    return report


def convert(input_path: str | Path, output_path: str | Path | None = None,
            cfg: Config | None = None, _doc=None, _analysis=None) -> ConversionReport:
    """``_doc``: the drawing already opened (a second pass over the same file does not read it again). ``_analysis``: the
    analysis of the sheet already made (an upper storey of a stack is converted with the one of the base, its plan changed)."""
    cfg = cfg or Config()
    cfg.validate()
    input_path = Path(input_path)
    output_path = Path(output_path) if output_path else input_path.with_suffix(".obj")

    doc = _doc if _doc is not None else open_drawing(input_path, cfg.converter)
    analysis_failed = ""
    no_plan = False
    if cfg.auto:  # the sheet first: the unit, which view is the plan, which layers are the walls
        try:
            analysis = _analysis if _analysis is not None else analyze(doc, lambda layer: layer_used(doc, cfg, layer), cfg.view, cfg.units)
            user = cfg
            cfg = replace(apply_analysis(doc, cfg, analysis)[0], user=user)
            no_plan = analysis.plan is None and cfg.area is None and not any(
                v.kind in ("plan", "?") for v in analysis.views)
        except Exception as exc:  # the analysis is a help: when it fails the layer names and the options decide
            analysis_failed = f"Analisi del foglio non riuscita ({type(exc).__name__}: {exc}): uso i nomi dei layer."
            cfg = replace(cfg, auto=False, analysis_notes=[analysis_failed])
    if cfg.analysis is not None and cfg.site != "no" and (cfg.site == "yes" or no_plan):
        built = _convert_site(doc, output_path, cfg)  # a site plan that draws the roofs of its buildings: the buildings from those
        if built is not None:
            return built
        if cfg.site == "yes":
            raise ConversionError("Modalita' sito richiesta (--sito) ma la planimetria generale non disegna nessun tetto "
                                  "(campiture di coppi o di tegole): non ho trovato edifici da costruire.")
    if no_plan and len(analysis.views) > 1:  # prospetti, sezioni, una planimetria generale: nessuna pianta di edificio
        picture = ""
        if cfg.images:
            try:
                picture = f" Guarda {write_views_image(analysis, output_path.with_name(output_path.stem + '_viste.png')).name}."
            except Exception:  # a picture must never hide the message
                pass
        kinds = ", ".join(f"{v.id} ({_KIND_IT.get(v.kind, v.kind)})" for v in analysis.views)
        raise ConversionError(
            f"Il foglio ha {len(analysis.views)} viste ma nessuna e' la pianta di un edificio: {kinds}.{picture} Se la pianta "
            "e' dentro la planimetria generale indica la sua zona con --area x0,y0,x1,y1 (o --vista N per scegliere "
            "una vista); per leggere il disegno cosi' com'e' usa --no-analisi.")
    found = storeys(doc)
    if cfg.floor is not None and found and cfg.floor not in found:
        raise ConversionError(f"Il piano {cfg.floor} non esiste: i layer nominano i piani "
                              f"{', '.join(map(str, found))}.")
    stack = find_storeys(cfg.analysis) if stacking_on(cfg, cfg.analysis, len(found) > 1) else None  # the other floors of the sheet
    if stack is not None and stack.candidates:
        cfg = replace(cfg, storey=stack.base_index)  # the elevations are read for this storey: the views said which it is
    elif cfg.analysis is not None and cfg.analysis.plan is not None and not storey_stated(" ".join(cfg.analysis.plan.titles)):
        index, _ = storey_of_view(cfg.analysis.views, cfg.analysis.plan)  # a plan titled through a copy of it (--vista N)
        cfg = replace(cfg, storey=index)
    result = read_items(doc, cfg)
    warnings = list(cfg.analysis_notes) + list(result.warnings) + (stack.notes if stack is not None else [])
    if len(found) > 1 and cfg.floor is None:
        warnings.append(f"I layer nominano {len(found)} piani ({', '.join(f'P{n}' for n in found)}): ho letto il "
                        f"piano {found[0]}. Per un altro usa --piano N.")

    lot = None
    if cfg.fences and not cfg.part:  # the fence of the lot (an upper storey of a stack has none of its own): its layers leave the walls
        try:
            lot = detect_lot_fence(doc, cfg, result.unit_scale, [*(tuple(spec[:4]) for spec in cfg.elevations),
                                                                  *(other_view_boxes(cfg.analysis) if cfg.analysis is not None else [])])
        except Exception as exc:  # a help: whatever goes wrong in it is never the reason a conversion fails
            warnings.append(f"Recinzione del lotto non cercata: {type(exc).__name__}: {exc}. Il resto della conversione e' completo; "
                            "per escluderla usa --no-recinzioni.")
        result.items, taken = without_fence_layers(result.items, lot, cfg)
        if taken:
            warnings.append(f"Recinzione del lotto: i layer {', '.join(taken)} (nomi da muretto o recinzione) non sono letti come muri della "
                            "casa. Per leggerli come muri usa --no-recinzioni (o indicali con --muri).")

    walls, layer_walls, false = _read_walls(result.items, cfg, warnings)
    if walls.is_empty:
        found = sorted({it.layer for it in result.items})
        raise ConversionError(
            "Nessun muro riconosciuto. Controlla i nomi dei layer con --elenca-layer e "
            "indicali con --muri \"NOME1,NOME2\"."
            + (f" Layer riconosciuti come porte/finestre/pilastri: {', '.join(found)}." if found else "")
        )
    columns = union([build_columns(result.items, cfg), false.columns])
    if not columns.is_empty:
        columns = columns.difference(walls)
    if cfg.pair_layers:  # walls drawn in pairs of lines: a single line that closes a space the others leave open is a thin wall
        thin, thin_lines, closed = single_line_walls(result.items, walls, cfg.pair_layers, cfg.max_wall_thickness)
        if thin_lines:
            walls = clean_footprint(union([walls, thin]), cfg.merge_tolerance)
            warnings.append(f"{len(thin_lines)} linee singole lunghe in tutto {sum(l.length for l in thin_lines):.1f} m (senza una linea "
                            f"parallela vicina: di solito non sono muri) sono state trattate come muri sottili di "
                            f"{THICKNESS * 100:.0f} cm perche' chiudono {closed:.0f} m2 di locali che nessun altro muro chiude: "
                            "controlla nella pianta di controllo e, se non sono muri, escludile con --muri.")
    ribbon: list = []
    if cfg.shape_openings and len(result.thin):  # windows drawn as thin glazing strips along the outside wall
        ribbon, cleared = ribbon_windows(result.thin, walls, result.items, cfg)
        if ribbon:
            walls = clean_footprint(walls.difference(cleared), cfg.merge_tolerance)
    openings = build_openings(result.items, walls, cfg, warnings)
    if ribbon:
        openings.extend(ribbon)
        assign_ids(openings)
        warnings.append(f"{len(ribbon)} finestre a nastro dedotte dalle strisce sottili del vetro (linee parallele a meno "
                        "di 6 cm l'una dall'altra nel muro esterno): controlla nella tabella delle aperture.")

    span = max(_extent(walls))
    if not (3.0 <= span <= 300.0) and cfg.units is None and not result.unit_guessed:
        alt = _better_unit(span, result.unit)
        if alt:
            fixed = convert(input_path, output_path, replace(cfg, units=alt), _doc=doc)
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
    plan = Plan(walls=walls, columns=columns, openings=openings, merge_tolerance=cfg.merge_tolerance, low=false.low)
    solid = plan.solid_walls
    groups = _wall_groups(solid, openings)
    if groups:
        real = [g for g in groups if g["openings"] > 0]
        area = None
        if (cfg.area is None or cfg.area_auto) and not cfg.groups_cut and 0 < len(real) < len(groups):
            # Only some groups have doors/windows: the others are a roof plan, a section... drawn on the same
            # layer. Read the drawing again cropped to the groups that are plans.
            margin = 1.0
            x0 = min(g["bounds"][0] for g in real) - margin
            y0 = min(g["bounds"][1] for g in real) - margin
            x1 = max(g["bounds"][2] for g in real) + margin
            y1 = max(g["bounds"][3] for g in real) + margin
            area = tuple(v / result.unit_scale for v in (x0, y0, x1, y1))
            if cfg.area is not None and all(abs(p - q) < 1e-6 for p, q in zip(cfg.area, area)):
                area = None  # this window is the one in use: a group without openings inside it stays
        if area is not None:
            dropped = [g for g in groups if g["openings"] == 0]
            skip = [tuple((v + d) / result.unit_scale for v, d in zip(g["bounds"], (-0.5, -0.5, 0.5, 0.5)))
                    for g in dropped]  # a roof plan or a section is not the garden either
            fixed = convert(input_path, output_path,
                            replace(cfg, area=area, area_auto=True, groups_cut=True,
                                    garden_exclude=[*cfg.garden_exclude, *skip]),
                            _doc=doc)
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
        gaps = wall_gaps(solid, cfg)
        if gaps:  # a partition with its middle not drawn: offered, never closed
            where = "; ".join(f"{gap:.1f} m in x={gx / result.unit_scale:.0f}, y={gy / result.unit_scale:.0f}" for gap, (gx, gy) in gaps[:5])
            many = len(gaps) > 1
            warnings.append(f"{len(gaps)} {'coppie' if many else 'coppia'} di spezzoni di muro allineati {'stanno' if many else 'sta'} in "
                            f"uno stesso locale a una distanza maggiore di un vano ({where}{', ...' if len(gaps) > 5 else ''}): "
                            "se e' un muro unico a cui manca la parte di mezzo, completalo nel disegno; non l'ho chiuso io.")

    words = read_words(doc, cfg, result.unit_scale) if cfg.texts else []
    if cfg.texts and cfg.low_heights_auto and not plan.low.is_empty:  # "Hmuro= 1,00 ml." next to a curb or a low wall
        plan.low, given, notes = written_heights(plan.low, read_texts(doc, cfg, result.unit_scale))
        plan.low_written = [(body, height) for body, height, _ in given]
        warnings.extend(notes)
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
    garden_zones = [tuple(spec[:4]) for spec in cfg.elevations]  # what is an elevation, for the garden to leave out
    if not specs and cfg.elevations_auto and not cfg.part:  # (the elevations drawn in line with the plan below are not those of an upper one)
        everything = read_items(doc, cfg, area=None, ignore_veto=True, keep_other=True, unit=result.unit)
        found_zones = find_elevation_zones(everything.items, solid.bounds, result.unit_scale, cores=True)
        specs = [(zone, layers) for layers, zone, _ in found_zones]
        garden_zones = [core for _, _, core in found_zones]
    read_zones: list[tuple[float, float, float, float]] = []  # the zones that gave an elevation, read in line with the plan
    for i, (spec, found_on) in enumerate(specs):
        er = read_items(doc, cfg, area=tuple(spec[:4]), ignore_veto=True, keep_other=True,
                        unit=result.unit)
        ev = read_elevation(er.items, spec, result.unit_scale, solid.bounds, warnings, i)
        if ev is None:
            continue
        read_zones.append(tuple(spec[:4]))
        elevations.append(ev)
        matched, total = apply_elevation(ev, openings, solid, cfg, warnings)
        elevation_report.append({"side": ev.side, "matched": matched, "total": total,
                                 "zero_source": ev.zero_source, **({"found_on": found_on} if found_on else {})})

    if cfg.analysis is not None and cfg.elevations_auto and not cfg.elevations:
        try:
            more, more_report, height = match_views(doc, cfg, cfg.analysis, openings, solid, result.unit,
                                                    result.unit_scale, read_zones, warnings)
        except Exception as exc:  # the elevations drawn elsewhere are a help: never the reason a conversion fails
            more, more_report, height = [], [], None
            warnings.append(f"Prospetti non allineati alla pianta non usati ({type(exc).__name__}: {exc}).")
        elevations.extend(more)
        elevation_report.extend(more_report)
        if height is not None:  # the windows of the elevations reach higher than the walls in use: the elevation knows
            cfg = replace(cfg, wall_height=height, wall_height_auto=False)
            wall_height_source = "prospetto"
            for o in openings:  # what an elevation in line with the plan cut at the old height can now reach higher
                if o.from_elevation and o.z1_drawn is not None and o.z1_drawn > o.z1:
                    o.z1 = min(height, o.z1_drawn)

    labels = apply_labels(openings, words, cfg, text_unit, solid.bounds, warnings) if words else 0

    if cfg.table_in:
        rows = rows_of_storey(read_table(cfg.table_in), cfg, warnings)
        changed = apply_table(openings, rows, cfg, warnings, solid, result.unit_scale)
        if changed:
            plan.__dict__.pop("solid_walls", None)  # widths/kinds changed: the filled walls too
            solid = plan.solid_walls
            rooms = find_rooms(solid, words, text_unit, [])

    part_layers = {k: g for k, g in layer_walls.items() if cfg.layers.is_partition(k)}
    if cfg.partitions_apart and part_layers and len(part_layers) < len(layer_walls):
        main = union(g for k, g in layer_walls.items() if k not in part_layers)
        plan.partitions = split_partitions(solid, main, union(part_layers.values()), openings)
    if cfg.floors_by_room and cfg.floor_thickness > 0:
        regions = layer_floors(result.items)
        if regions:
            plan.floors = name_floors(regions, rooms)
        elif rooms and (len(rooms) > 1 or rooms[0].named):
            plan.floors = floor_left_over(solid, room_floors(rooms, openings))  # what no room covers: one more floor
        elif floor_footprint(solid).is_empty:  # no closed room at all: the building's outline, gaps of a few metres bridged
            plan.floors = floor_left_over(solid, [])
        rest = dict(plan.floors).get(OTHER_FLOOR)
        if rest is not None:
            warnings.append(f"Pavimento: {rest.area:.0f} m2 dell'edificio non stanno in nessun locale chiuso dai muri (spazi "
                            "aperti, locali dai muri incompleti): ho steso un pavimento unico, 'Pavimento', sull'impronta dei "
                            "muri (varchi fino a 4 m ponticellati). Controlla.")
        if cfg.floors_to_outer_face:
            plan.floors = extend_to_outer_faces(plan.floors, solid, cfg.max_wall_thickness, plan.partitions)
    if skirting_items(result.items):
        plan.skirting = skirting_strips(result.items, solid, openings, cfg)

    footprint = building_footprint(solid)
    for o in openings:
        o.face_sign = facing_sign(o, footprint)
    if cfg.part:  # an upper storey of a stack: the conversion that stacks it sets its height, builds its mesh, writes the files
        return ConversionReport(
            output=output_path, unit=result.unit, unit_guessed=result.unit_guessed, **_counts(solid, openings, columns), faces=0,
            groups=[], warnings=warnings, elevations=elevation_report, rooms=_room_rows(rooms), labels=labels,
            wall_height=cfg.wall_height, wall_height_source=wall_height_source, openings=openings, plan=plan, false_walls=false,
            room_objects=rooms, analysis=cfg.analysis, cfg=cfg)
    roof_cfg, roof_solid = cfg, solid  # the roof stands on the walls of the top storey
    floors: list[Floor] = []
    if stack is not None and stack.candidates:
        floors = _stack_storeys(doc, input_path, output_path, cfg, stack, plan, openings, elevation_report, wall_height_source,
                                warnings)
        if floors:
            cfg, wall_height_source = floors[0].cfg, floors[0].height_source  # its walls now end under the slab of the next
            top = floors[-1]
            roof_cfg = replace(cfg, wall_height=top.level + top.height)  # the eaves, above the floor the elevations start from
            roof_solid = moved(top.plan.solid_walls, top.shift)
    plan.roof, plan.flat_roof, roof_report, flat_roof_report = _build_roof(doc, roof_cfg, result, roof_solid, elevations, warnings)

    if lot is not None:
        lot = finish_lot_fence(lot, doc, cfg, result.unit, footprint, read_zones, warnings,
                               [v.bbox for v in cfg.analysis.views if v.kind == "elevation"] if cfg.analysis is not None else None)
    plan.lot = lot
    garden = None
    if cfg.garden:
        # the walls may have a gap (a doorway wider than a passage): what the floor bridges is inside as well
        outside_of = union([footprint, floor_footprint(solid)])
        # what the sheet draws apart from the plan (another storey, the roof, sections...) is not its garden; where the
        # user says where the plan is (--area), only the views that are surely no plan are left out
        others = other_view_boxes(cfg.analysis, None if cfg.area is None or cfg.area_auto else GARDEN_SKIPPED_KINDS) \
            if cfg.analysis is not None else []
        try:
            garden = build_garden(doc, replace(cfg, garden_exclude=[*cfg.garden_exclude, *others]),
                                  result.unit_scale, outside_of, garden_zones, warnings, lot.footprint if lot else None)
        except Exception as exc:  # the garden is a help: whatever goes wrong in it is never the reason a conversion fails
            garden = None
            warnings.append(f"Giardino non costruito: {type(exc).__name__}: {exc}. Il resto della conversione e' "
                            "completo; per escludere il giardino usa --no-giardino, per limitarlo --area-giardino.")
        if cfg.garden_table_in:
            if garden is None:
                warnings.append("Tabella del giardino indicata, ma non ho trovato nessun giardino a cui applicarla.")
            elif not apply_garden_table(garden, read_garden_table(cfg.garden_table_in), cfg, warnings):
                warnings.append("Tabella del giardino: nessuna riga con celle MODIFICA_* compilate da applicare.")
        plan.garden = garden
    plan.site_surfaces = _site_surfaces(doc, cfg, result, solid, garden, lot, warnings)
    if cfg.terrain or cfg.terrain_auto:
        plan.terrain = _build_terrain(doc, cfg, result, solid, columns, garden, warnings)
    mesh = build_mesh(plan, cfg, warnings, roof=not floors)
    garden = plan.garden  # (None when its mesh could not be built)
    if floors:
        mesh = stack_mesh(floors, mesh, plan, roof_cfg, warnings)
    every_opening = [o for fl in floors for o in fl.openings] if floors else openings
    offset = (0.0, 0.0)
    if cfg.origin != "drawing":
        x0, y0, x1, y1 = solid.bounds
        offset = (-(x0 + x1) / 2, -(y0 + y1) / 2) if cfg.origin == "center" else (-x0, -y0)
        mesh.translate_xy(*offset)
    write_obj(mesh, output_path, cfg.out_units, cfg.mirror)
    json_path = None
    if cfg.c4d_json:
        json_path = write_json(to_model_dict(mesh, output_path.stem, offset, every_opening,
                                             garden.objects if garden else None, plan.terrain is not None),
                               output_path.with_name(output_path.stem + "_model.json"))

    table_path = None
    if cfg.write_table and every_opening:
        table_path = output_path.with_name(output_path.stem + "_aperture.csv")
        if cfg.table_in and Path(cfg.table_in).resolve() == table_path.resolve():
            table_path = output_path.with_name(output_path.stem + "_aperture_nuova.csv")  # never overwrite the input
        write_table(table_path, every_opening, result.unit_scale, stacked=bool(floors))

    garden_table_path = None
    if cfg.write_garden_table and garden is not None and garden.objects:
        garden_table_path = output_path.with_name(output_path.stem + "_giardino.csv")
        if cfg.garden_table_in and Path(cfg.garden_table_in).resolve() == garden_table_path.resolve():
            garden_table_path = output_path.with_name(output_path.stem + "_giardino_nuova.csv")  # never overwrite the input
        write_garden_table(garden_table_path, garden, result.unit_scale)

    report = ConversionReport(
        output=output_path,
        unit=result.unit,
        unit_guessed=result.unit_guessed,
        **_counts(solid, openings, columns),
        faces=mesh.face_count,
        groups=list(mesh.groups),
        warnings=warnings,
        elevations=elevation_report,
        roof=roof_report,
        flat_roof=flat_roof_report,
        origin_offset=offset,
        json_path=json_path,
        table_path=table_path,
        rooms=_room_rows(rooms),
        labels=labels,
        wall_height=cfg.wall_height,
        wall_height_source=wall_height_source,
        mesh=mesh,
        openings=every_opening,
        plan=plan,
        false_walls=false,
        room_objects=rooms,
        garden=garden,
        lot=plan.lot,
        terrain=plan.terrain.info if plan.terrain is not None else None,
        site_surfaces=plan.site_surfaces,
        garden_table_path=garden_table_path,
        analysis=cfg.analysis,
        floors=floors,
        cfg=cfg,
    )
    created = [output_path.name, output_path.with_suffix(".mtl").name]
    created += [p.name for p in (json_path, table_path, garden_table_path) if p]
    if cfg.images and cfg.analysis is not None and len(cfg.analysis.views) > 1:
        try:
            report.views_path = write_views_image(cfg.analysis, output_path.with_name(output_path.stem + "_viste.png"))
            created.append(report.views_path.name)
        except Exception as exc:  # a picture must never stop the conversion
            report.images_note = f"immagine delle viste non creata: {exc}"
    if cfg.images:
        x0, y0, x1, y1 = solid.bounds  # (a terrain is shown only round the house)
        focus = (x0 + offset[0] - PREVIEW_MARGIN, x1 + offset[0] + PREVIEW_MARGIN, y0 + offset[1] - PREVIEW_MARGIN,
                 y1 + offset[1] + PREVIEW_MARGIN) if plan.terrain is not None else None
        report.preview_path = preview_3d(mesh, output_path.with_name(output_path.stem + "_anteprima_3d.png"), focus)
        created.append(report.preview_path.name)
        try:
            report.overlay_path = plan_overlay(report, output_path.with_name(output_path.stem + "_controllo_pianta.png"),
                                               openings)
            created.append(report.overlay_path.name)
            for fl in floors[1:]:  # a picture to check for every storey above
                path = plan_overlay(fl.report, output_path.with_name(f"{output_path.stem}_controllo_pianta_{storey_tag(fl.index)}.png"))
                report.overlay_paths.append(path)
                created.append(path.name)
        except ImportError:
            report.images_note = "pianta di controllo non creata: serve matplotlib (pip install matplotlib)"
        except Exception as exc:  # a picture must never stop the conversion
            report.images_note = f"pianta di controllo non creata: {exc}"
    report.report_path = write_report(report, output_path.with_name(output_path.stem + "_report.txt"),
                                      created + [output_path.stem + "_report.txt"])
    return report
