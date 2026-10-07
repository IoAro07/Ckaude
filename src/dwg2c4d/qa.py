"""Checks you can look at: the plan with what was recognised, a 3D preview, a text report."""

from __future__ import annotations

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
                 "e quota da terra in cm (blu finestre, rosso porte, arancio vani; * = vedi note)", fontsize=9)
    path = Path(path)
    fig.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)
    return path
