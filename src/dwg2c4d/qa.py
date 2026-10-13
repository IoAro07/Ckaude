"""Checks you can look at: the plan with what was recognised, a 3D preview, a text report."""

from __future__ import annotations

import math
from collections import OrderedDict
from pathlib import Path

from .config import UNIT_TO_METERS
from .geom import polygons_of
from .render import save_views

KIND_COLOR = {"window": "#1f77b4", "door": "#d62728", "passage": "#ff7f0e"}
KIND_NAME = {"window": "finestra", "door": "porta", "passage": "vano"}


# --- text -------------------------------------------------------------------------------------

HEIGHT_SOURCE = {"scritta": "dalla scritta 'h' nei locali", "indicata": "indicata", "predefinita": "predefinita",
                 "prospetto": "dai prospetti: altezza di interpiano o linea di gronda"}


def _elevation_line(ev: dict, label: str) -> str:
    """The line of the report for one elevation matched to the plan (``label``: what comes before the colon)."""
    side = {"south": "sud", "north": "nord"}.get(ev["side"], ev["side"])
    zero = {"porta": "dal fondo della porta", "indicata": "indicata", "quota +0,00": "dal segno di quota",
            "linea di terra": "dalla sommita' della linea di terra", "blocco figura": "dai piedi della figura umana",
            "quota del piano": "dalla quota del piano"}.get(ev["zero_source"], ev["zero_source"])
    found = f", trovato da solo sul layer {ev['found_on']}" if ev.get("found_on") else ""
    if ev.get("view"):  # an elevation that is not in line with the plan: matched by the row of its windows
        found = (f", vista {ev['view']} abbinata alla facciata dal confronto delle aperture "
                 f"(affidabilita' {ev['confidence']}, scarto medio {ev['residual'] * 100:.0f} cm)")
        if not ev.get("north_known", True):
            found += "; il lato e' nel riferimento del disegno (nord = alto), il foglio non lo conferma"
    return (f"  {label} {side:<5}    : {ev['matched']} di {ev['total']} aperture con le quote del "
            f"prospetto (quota zero {zero}{found})")


def _storey_lines(report) -> list[str]:
    """The storeys of a stacked model, above the converted plan: where each stands, what its plan gave. (The lines of the
    converted plan are the ones every report has.)"""
    out = []
    for fl in report.floors[1:]:
        solid = fl.plan.solid_walls
        doors, windows = (sum(o.kind == k for o in fl.openings) for k in ("door", "window"))
        out.append(f"  {f'Piano {fl.index}':<19}: vista {fl.view} ({fl.how}), spostata di ({fl.shift[0]:.2f}, {fl.shift[1]:.2f}) m "
                   f"sulla pianta sotto (i contorni dei muri combaciano per il {fl.iou * 100:.0f} %); pavimento a {fl.level:.2f} m, "
                   f"solaio di {fl.slab * 100:.0f} cm sotto")
        out.append(f"  {f'Piano {fl.index} muri':<19}: {len(polygons_of(solid))} corpi, {solid.area:.1f} m2 in pianta; alti "
                   f"{fl.height:.2f} m ({HEIGHT_SOURCE[fl.height_source]}); porte / finestre {doors} / {windows}; "
                   f"pilastri {len(polygons_of(fl.plan.columns))}")
        out.extend(_elevation_line(ev, f"Prosp. P{fl.index}") for ev in fl.report.elevations)
        out.extend(f"  {f'Locale piano {fl.index}':<19}: {r['name']}  {r['area_m2']:.1f} m2" + ("" if r["named"] else "  (senza nome)")
                   for r in fl.report.rooms)
    return out


def summary_lines(report) -> list[str]:
    """The summary printed by the command line and written to the report file."""
    w, h = report.size_m
    out = []
    note = " (dedotte: verifica!)" if report.unit_guessed else ""
    out.append(f"  Unita' del disegno : {report.unit}{note}")
    if report.origin_offset != (0.0, 0.0):
        out.append(f"  Origine            : modello spostato di ({report.origin_offset[0]:.2f}, "
                   f"{report.origin_offset[1]:.2f}) m rispetto al disegno")
    a = getattr(report, "analysis", None)
    if a is not None and len(a.views) > 1:
        from collections import Counter

        from .autodetect import _KIND_IT

        kinds = ", ".join(f"{n} {_KIND_IT.get(k, k)}" for k, n in Counter(v.kind for v in a.views).most_common())
        chosen = f"convertita la vista {a.plan.id}" if a.plan is not None else "nessuna pianta riconosciuta"
        out.append(f"  Foglio             : {len(a.views)} viste ({kinds}); {chosen}")
    if getattr(report, "site", None) is not None:  # a site: buildings from their roofs, no plan
        out.extend(_site_lines(report))
        return out + _tail_lines(report)
    if report.floors:
        base = report.floors[0]
        out.append(f"  Piani              : {len(report.floors)} piani impilati in un modello solo: il piano {base.index} e' la pianta "
                   f"convertita (vista {base.view}, {base.how}), le righe qui sotto che non dicono altro sono sue")
    out.append(f"  Ingombro muri      : {w:.2f} x {h:.2f} m")
    out.append(f"  Muri               : {report.wall_pieces} corpi, {report.wall_area_m2:.1f} m2 in pianta")
    out.append(f"  Porte / finestre   : {report.doors} / {report.windows}")
    if report.passages:
        out.append(f"  Vani senza simbolo : {report.passages} (dedotti dai muri: verifica nella tabella)")
    out.append(f"  Altezza muri       : {report.wall_height:g} m ({HEIGHT_SOURCE[report.wall_height_source]})")
    if report.labels:
        out.append(f"  Scritte            : quote lette per {report.labels} aperture su "
                   f"{len(base_openings(report))}")
    for room in report.rooms:
        height = f", h {room['height']:g} m" if room["height"] else ""
        mark = "" if room["named"] else "  (senza nome)"
        out.append(f"  Locale             : {room['name']}  {room['area_m2']:.1f} m2{height}{mark}")
    for ev in report.elevations:
        out.append(_elevation_line(ev, "Prospetto"))
    if report.roof:
        r = report.roof
        how = {"indicata": "indicata", "scritta": "scritta nel foglio", "prospetto": "dedotta dal prospetto",
               "predefinita": "predefinita"}
        lo, hi = r["pitch_range"]
        slope = f"{r['pitch_deg']:.0f}" if hi - lo < 1.0 else f"{lo:.0f}-{hi:.0f}"
        view = f", dalla vista {r['view']} della copertura (sporto {r['overhang']:.2f} m)" if r.get("view") else ""
        out.append(f"  Tetto              : {r['faces']} falde, pendenza {slope} gradi "
                   f"({how[r['pitch_source']]}), colmo a {r['ridge_height']:.2f} m{view}")
    if report.flat_roof:
        f = report.flat_roof
        parapet = (f", parapetto alto {f['parapet_height']:.2f} m ({'dal prospetto' if f['parapet_source'] == 'prospetto' else 'presunto'})"
                   if f["parapet_height"] is not None else "")
        panels = f", {f['modules']} pannelli fotovoltaici" if f["modules"] else ""
        out.append(f"  Tetto piano        : {f['bodies']} corp{'o' if f['bodies'] == 1 else 'i'}, {f['area_m2']:.0f} m2, lastra di "
                   f"{f['thickness'] * 100:.0f} cm{parapet}{panels}, dalla vista {f['view']} della copertura")
    if report.columns:
        out.append(f"  Pilastri           : {report.columns}")
    out.extend(_storey_lines(report))
    return out + _tail_lines(report)


def _site_lines(report) -> list[str]:
    """The summary of a site: its buildings, their roofs and heights, the doors and windows put on them."""
    site = report.site
    w, h = report.size_m
    out = [f"  Modalita' sito     : {len(site.buildings)} edifici dai tetti della planimetria generale (vista "
           f"{', '.join(str(v) for v in site.views)}), nessuna pianta; ingombro {w:.1f} x {h:.1f} m"]
    for b in site.buildings:
        bw, bh = b.size
        eaves = sorted({round(body.eave_height, 2) for body in b.bodies})
        out.append(f"  {f'Edificio {b.id}':<19}: tetto {bw:.1f} x {bh:.1f} m, {len(b.planes)} falde in {len(b.bodies)} corp"
                   f"{'o' if len(b.bodies) == 1 else 'i'}, gronda a {', '.join(f'{e:g}' for e in eaves)} m; "
                   f"porte / finestre {sum(o.kind == 'door' and o.building == b.id for o in report.openings)} / "
                   f"{sum(o.kind == 'window' and o.building == b.id for o in report.openings)}")
    how = {"indicata": "indicata", "scritta": "scritta nel foglio", "prospetto": "dedotta dal prospetto", "predefinita": "predefinita"}
    out.append(f"  Tetto              : pendenza {site.pitch:.0f} gradi ({how[site.pitch_source]})")
    out.append(f"  Porte / finestre   : {report.doors} / {report.windows}")
    out.append(f"  Muri               : {report.wall_area_m2:.1f} m2 in pianta, vuoti dentro")
    return out


def _tail_lines(report) -> list[str]:
    """The lines every summary ends with: the garden, the terrain, the faces and the warnings."""
    out: list[str] = []
    if report.garden is not None:
        from .garden import summary

        out.append(f"  Giardino           : {summary(report.garden)}")
        heights = {}
        for o in report.garden.objects:
            heights.setdefault(o.kind, set()).add((o.height_src, round(o.height, 2)))
        for kind, label in (("tree", "alberi"), ("hedge", "siepi"), ("shrub", "cespugli"), ("furniture", "arredi")):
            if kind in heights:
                srcs = sorted(heights[kind])
                text = ", ".join(f"{h:g} m ({how})" for how, h in srcs[:3])
                out.append(f"    altezza {label:<9}: {text}")
    if report.lot is not None:
        from .lotfence import summary as lot_summary

        out.append(f"  Recinzione del lotto: {lot_summary(report.lot)}")
    if getattr(report, "site_surfaces", None) is not None:
        from .sitesurfaces import summary as site_summary

        if report.site_surfaces.surfaces:
            out.append(f"  Superfici del sito : {site_summary(report.site_surfaces)}")
        if report.site_surfaces.walls:
            from .sitewalls import summary as walls_summary

            out.append(f"  Muri del sito      : {walls_summary(report.site_surfaces.walls)}")
    if getattr(report, "stairs", None):
        from .sitesteps import summary as stairs_summary

        out.append(f"  Gradini            : {stairs_summary(report.stairs)}")
    if report.terrain:
        t = report.terrain
        out.append(f"  Terreno            : {t['levels']} curve di livello (layer {t['layer']}, ogni {t['step']:g} m, da {t['low']:.1f} "
                   f"a {t['high']:.1f} m), {t['size'][0]:.0f} x {t['size'][1]:.0f} m, spessore {t['thickness']:g} m; pavimento "
                   f"finito a {t['floor']:.2f} m ({t['floor_source']})")
    out.append(f"  Facce / oggetti    : {report.faces} / {', '.join(report.groups)}")
    for msg in report.warnings:
        out.append(f"  ATTENZIONE: {msg}")
    return out


def base_openings(report) -> list:
    """The openings of the converted plan (the lowest storey of a stack, else all of them)."""
    return [o for o in report.openings if not report.floors or o.storey == report.floors[0].index]


def opening_notes(report) -> "OrderedDict[str, list[str]]":
    """{note: [ids]}: what the program was unsure about, grouped so a repeated remark reads once."""
    notes: "OrderedDict[str, list[str]]" = OrderedDict()
    for o in sorted(report.openings, key=lambda o: (o.storey or 0, o.id)):
        for n in o.notes:
            notes.setdefault(n, []).append(o.key)
    return notes


def write_report(report, path: str | Path, created: list[str]) -> Path:
    from . import __version__

    lines = [f"PLANIMETRIA -> 3D   (dwg2c4d {__version__})", "=" * 60, ""]
    lines += [f"Creato {name}" for name in created] + [""]
    lines += summary_lines(report)
    lines += ["", "APERTURE", "-" * 60]
    for o in sorted(report.openings, key=lambda o: (o.storey or 0, o.id)):
        off = "" if o.keep else "  [eliminata dalla tabella]"
        lines.append(f"  {o.key}  {KIND_NAME[o.kind]:<8} {o.width * 100:5.0f} x {(o.z1 - o.z0) * 100:3.0f} cm, "
                     f"da {o.z0 * 100:3.0f} cm   {o.label}{off}")
    notes = opening_notes(report)
    if notes:
        lines += ["", "DA CONTROLLARE (note sulle aperture)", "-" * 60]
        lines += [f"  {', '.join(ids)}: {n}" for n, ids in notes.items()]
    false = getattr(report, "false_walls", None)
    if false is not None and not false.empty:
        lines += ["", "MURI MESSI DA PARTE (non estrusi, o bassi: controlla)", "-" * 60]
        lines += false.listing(UNIT_TO_METERS[report.unit], report.unit)
    path = Path(path)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- pictures ---------------------------------------------------------------------------------

def preview_3d(mesh, path: str | Path, focus: tuple[float, float, float, float] | None = None) -> Path:
    """Three views of the model (needs only numpy). ``focus`` (xmin, xmax, ymin, ymax, metres): only what lies there is
    drawn (a terrain is far bigger than the house on it)."""
    return save_views(mesh, path, views=((-58, 38), (-128, 38), (-90, 86)), size=(620, 470),
                      hide=("Soffitto",), clip=focus)


def plan_overlay(report, path: str | Path, openings: list | None = None) -> Path:
    """The plan seen from above: walls, rooms with names and heights, every opening with its id,
    size and sill. Needs matplotlib. ``openings``: those to draw (default: all of the report's; a stacked model's report
    holds the openings of every storey, the plan is of one)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import PathPatch, Rectangle
    from matplotlib.path import Path as MplPath
    from shapely.geometry.polygon import orient

    from .geom import polygons_of, union

    plan = report.plan
    fig, ax = plt.subplots(figsize=(16, 10))

    def draw(geom, **style):
        for poly in polygons_of(geom):
            poly = orient(poly)  # exterior counter-clockwise, holes clockwise: holes stay empty
            rings = [poly.exterior, *poly.interiors]
            path = MplPath.make_compound_path(*(MplPath(list(r.coords), closed=True) for r in rings))
            ax.add_patch(PathPatch(path, **style))

    garden = report.garden
    if garden is not None:  # the ground and the plants under the building: the garden as it was understood
        ground = {"paving": "#c9c9cc", "edge": "#7c7c82", "lawn": "#b5d3a4", "soil": "#c8b79b"}
        for kind, geom in garden.surfaces.items():
            draw(geom, fc=ground[kind], ec="#999", lw=0.3, zorder=0.2)
        for pool in garden.pools:
            draw(pool, fc="#9cc9e8", ec="#3b7fb0", lw=0.6, zorder=0.4)
        for g in garden.objects:
            colour = {"tree": "#2f7a3a", "shrub": "#4c9a45", "hedge": "#1f5f1f", "furniture": "#d08a2a"}[g.kind]
            ux, uy = g.axis
            if g.kind in ("tree", "shrub"):
                from matplotlib.patches import Ellipse

                ax.add_patch(Ellipse((g.cx, g.cy), g.length, g.width, angle=math.degrees(g.angle), fc=colour,
                                     ec=colour, alpha=0.55 if g.keep else 0.15, zorder=0.6))
            else:
                from .geom import oriented_rect

                draw(oriented_rect(g.cx, g.cy, ux, uy, g.length, -g.width / 2, g.width / 2), fc=colour, ec=colour,
                     alpha=0.7 if g.keep else 0.15, zorder=0.6)
            ax.text(g.cx, g.cy, g.id, ha="center", va="center", fontsize=5.5, color="#10300f", zorder=0.8)
    for room in report.room_objects:
        draw(room.polygon, fc="#f4efe3", ec="#d8cdb0", lw=0.5, zorder=1)
        c = room.polygon.representative_point()
        height = f"\nh {room.height * 100:g}" if room.height else ""
        ax.text(c.x, c.y, f"{room.name}{height}", ha="center", va="center", fontsize=8, color="#7a6a45",
                zorder=2)
    walls = plan.solid_walls
    if not plan.partitions.is_empty:
        draw(walls.difference(plan.partitions), fc="#5d5d5d", ec="#222", lw=0.5, zorder=3)
        draw(plan.partitions, fc="#9a9a9a", ec="#444", lw=0.5, zorder=3)
    else:
        draw(walls, fc="#5d5d5d", ec="#222", lw=0.5, zorder=3)
    draw(plan.columns, fc="#3a3a3a", ec="#111", lw=0.4, zorder=3)
    draw(union([plan.low, *(body for body, _ in plan.low_written)]), fc="#7fc8c8", ec="#2a7f7f", lw=0.5, zorder=3)  # curbs and low walls: built low, apart from the walls
    lot = getattr(report, "lot", None)
    if lot is not None:  # the fence of the lot: the wall, the pillars, the railing (dark line), the wire fences (dotted), the gates
        draw(lot.walls, fc="#c9a46a", ec="#7a5a2a", lw=0.5, zorder=3)
        draw(union(lot.pillars), fc="#7a5a2a", ec="#4a3515", lw=0.4, zorder=3.2)
        for r in lot.railings:
            ax.plot(*r.xy, color="#111", lw=1.0, zorder=3.4)
        for w in lot.wires:
            ax.plot(*w.xy, color="#7a5a2a", lw=1.0, ls=":", zorder=3.4)
        for g in lot.gates:
            ax.text(g.x, g.y, "cancello", ha="center", va="center", fontsize=6, color="#7a5a2a", zorder=3.6)
    false = getattr(report, "false_walls", None)
    for o in (*false.furniture, *false.stairs) if false is not None else ():  # left out: where they were
        colour = "#2ca02c" if o.kind == "arredo" else "#d98c00"
        ax.add_patch(Rectangle((o.x - o.width / 2, o.y - o.depth / 2), o.width, o.depth, fill=False, ec=colour, lw=0.9,
                               ls="--", zorder=3))
    if not plan.skirting.is_empty:
        draw(plan.skirting, fc="#c040c0", ec="#c040c0", lw=0.3, zorder=4)
    for o in report.openings if openings is None else openings:
        color = KIND_COLOR[o.kind]
        alpha = 0.9 if o.keep else 0.25
        draw(o.cut, fc=color, ec=color, lw=0.8, zorder=5, alpha=alpha)
        ux, uy = o.axis
        nx, ny = -uy, ux
        side = 1 if (o.center[0] - walls.centroid.x) * nx + (o.center[1] - walls.centroid.y) * ny >= 0 else -1
        off = side * (o.thickness / 2 + 0.28)
        mark = "*" if o.notes else ""
        text = f"{o.id}{mark}\n{o.width * 100:.0f}×{(o.z1 - o.z0) * 100:.0f}\nda {o.z0 * 100:.0f}"
        ax.text(o.center[0] + nx * off, o.center[1] + ny * off, text, ha="center", va="center", fontsize=7,
                color=color, zorder=6, bbox={"fc": "white", "ec": color, "lw": 0.5, "alpha": 0.9, "pad": 1.0})
    ax.set_aspect("equal")
    ax.autoscale()
    ax.margins(0.06)
    terrain = plan.terrain
    if terrain is not None:  # the ground under the plan: contour lines every metre and the flat platform dashed, drawn within the plan
        import numpy as np

        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        ny, nx = terrain.shape
        gx, gy = np.meshgrid(terrain.x0 + np.arange(nx + 1) * terrain.step, terrain.y0 + np.arange(ny + 1) * terrain.step)
        ax.contour(gx, gy, terrain.z, levels=np.arange(math.floor(terrain.z.min()), math.ceil(terrain.z.max()) + 1.0),
                   colors="#b08850", linewidths=0.5, zorder=0.1)
        for poly in polygons_of(terrain.platform):
            ax.plot(*poly.exterior.xy, "--", color="#8a6a3a", lw=0.8, zorder=0.15)
        ax.set_xlim(xlim)
        ax.set_ylim(ylim)
    ax.grid(alpha=0.15)
    storey = f"Piano {report.cfg.storey} (vista {report.analysis.plan.id}). " if report.cfg is not None and report.cfg.part else ""
    ax.set_title(storey + "Controllo: muri (grigio), tramezzi (grigio chiaro), muretti e cordoli (verde acqua), locali, aperture con "
                 "sigla, larghezza×altezza e quota da terra in cm (blu finestre, rosso porte, arancio vani; * = vedi note); "
                 "tratteggiati: arredi (verde) e scale (arancio) non estrusi"
                 + ("; giardino: pavimentazione, prato, acqua, siepi/alberi/cespugli/arredi con sigla"
                    if garden is not None else "")
                 + ("; terreno: curve di livello ogni metro (marrone) e piattaforma piana (tratteggio)"
                    if terrain is not None else "")
                 + ("; recinzione del lotto: muretto e pilastri (marrone), ringhiera (linea nera), rete (puntini), cancelli"
                    if lot is not None else ""), fontsize=9)
    path = Path(path)
    fig.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)
    return path
