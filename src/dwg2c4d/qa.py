"""Checks you can look at: the plan with what was recognised, a 3D preview, a text report."""

from __future__ import annotations

import math
from collections import OrderedDict
from pathlib import Path

from .render import save_views

KIND_COLOR = {"window": "#1f77b4", "door": "#d62728", "passage": "#ff7f0e"}
KIND_NAME = {"window": "finestra", "door": "porta", "passage": "vano"}


# --- text -------------------------------------------------------------------------------------

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
    out.append(f"  Ingombro muri      : {w:.2f} x {h:.2f} m")
    out.append(f"  Muri               : {report.wall_pieces} corpi, {report.wall_area_m2:.1f} m2 in pianta")
    out.append(f"  Porte / finestre   : {report.doors} / {report.windows}")
    if report.passages:
        out.append(f"  Vani senza simbolo : {report.passages} (dedotti dai muri: verifica nella tabella)")
    src = {"scritta": "dalla scritta 'h' nei locali", "indicata": "indicata", "predefinita": "predefinita"}
    out.append(f"  Altezza muri       : {report.wall_height:g} m ({src[report.wall_height_source]})")
    if report.labels:
        out.append(f"  Scritte            : quote lette per {report.labels} aperture su "
                   f"{len(report.openings)}")
    for room in report.rooms:
        height = f", h {room['height']:g} m" if room["height"] else ""
        mark = "" if room["named"] else "  (senza nome)"
        out.append(f"  Locale             : {room['name']}  {room['area_m2']:.1f} m2{height}{mark}")
    for ev in report.elevations:
        side = {"south": "sud", "north": "nord"}[ev["side"]]
        zero = "dal fondo della porta" if ev["zero_source"] == "porta" else "indicata"
        found = f", trovato da solo sul layer {ev['found_on']}" if ev.get("found_on") else ""
        out.append(f"  Prospetto {side:<5}    : {ev['matched']} di {ev['total']} aperture con le quote del "
                   f"prospetto (quota zero {zero}{found})")
    if report.roof:
        r = report.roof
        how = {"indicata": "indicata", "prospetto": "dedotta dal prospetto", "predefinita": "predefinita"}
        lo, hi = r["pitch_range"]
        slope = f"{r['pitch_deg']:.0f}" if hi - lo < 1.0 else f"{lo:.0f}-{hi:.0f}"
        out.append(f"  Tetto              : {r['faces']} falde, pendenza {slope} gradi "
                   f"({how[r['pitch_source']]}), colmo a {r['ridge_height']:.2f} m")
    if report.columns:
        out.append(f"  Pilastri           : {report.columns}")
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
    out.append(f"  Facce / oggetti    : {report.faces} / {', '.join(report.groups)}")
    for msg in report.warnings:
        out.append(f"  ATTENZIONE: {msg}")
    return out


def opening_notes(report) -> "OrderedDict[str, list[str]]":
    """{note: [ids]}: what the program was unsure about, grouped so a repeated remark reads once."""
    notes: "OrderedDict[str, list[str]]" = OrderedDict()
    for o in sorted(report.openings, key=lambda o: o.id):
        for n in o.notes:
            notes.setdefault(n, []).append(o.id)
    return notes


def write_report(report, path: str | Path, created: list[str]) -> Path:
    from . import __version__

    lines = [f"PLANIMETRIA -> 3D   (dwg2c4d {__version__})", "=" * 60, ""]
    lines += [f"Creato {name}" for name in created] + [""]
    lines += summary_lines(report)
    lines += ["", "APERTURE", "-" * 60]
    for o in sorted(report.openings, key=lambda o: o.id):
        off = "" if o.keep else "  [eliminata dalla tabella]"
        lines.append(f"  {o.id}  {KIND_NAME[o.kind]:<8} {o.width * 100:5.0f} x {(o.z1 - o.z0) * 100:3.0f} cm, "
                     f"da {o.z0 * 100:3.0f} cm   {o.label}{off}")
    notes = opening_notes(report)
    if notes:
        lines += ["", "DA CONTROLLARE (note sulle aperture)", "-" * 60]
        lines += [f"  {', '.join(ids)}: {n}" for n, ids in notes.items()]
    path = Path(path)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- pictures ---------------------------------------------------------------------------------

def preview_3d(mesh, path: str | Path) -> Path:
    """Three views of the model (needs only numpy)."""
    return save_views(mesh, path, views=((-58, 38), (-128, 38), (-90, 86)), size=(620, 470),
                      hide=("Soffitto",))


def plan_overlay(report, path: str | Path) -> Path:
    """The plan seen from above: walls, rooms with names and heights, every opening with its id,
    size and sill. Needs matplotlib."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path as MplPath
    from shapely.geometry.polygon import orient

    from .geom import polygons_of

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
    if not plan.skirting.is_empty:
        draw(plan.skirting, fc="#c040c0", ec="#c040c0", lw=0.3, zorder=4)
    for o in report.openings:
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
    ax.grid(alpha=0.15)
    ax.set_title("Controllo: muri (grigio), tramezzi (grigio chiaro), locali, aperture con sigla, larghezza×altezza "
                 "e quota da terra in cm (blu finestre, rosso porte, arancio vani; * = vedi note)"
                 + ("; giardino: pavimentazione, prato, acqua, siepi/alberi/cespugli/arredi con sigla"
                    if garden is not None else ""), fontsize=9)
    path = Path(path)
    fig.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)
    return path
