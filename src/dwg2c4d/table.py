"""The openings table: every door/window with what was read and where it came from, and a way
to correct it by hand.

Write it (``NAME_aperture.csv``, ``;`` separated, centimetres), fix the ``MODIFICA_*`` columns in
Excel, run again with ``--tabella NAME_aperture.csv``. Only the cells you fill are changed.
Priority of sources for an opening's size: table > written dimension > elevation > default.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

from .config import Config
from .openings import Opening

COLUMNS = [
    "id", "tipo", "x", "y", "larghezza", "spessore_muro", "davanzale", "altezza", "ante", "cerniera",
    "origine_misure", "origine_ante", "scritta", "note",
    "MODIFICA_tipo", "MODIFICA_larghezza", "MODIFICA_davanzale", "MODIFICA_altezza", "MODIFICA_ante",
    "MODIFICA_cerniera", "MODIFICA_tieni",
]
KIND_NAME = {"window": "finestra", "door": "porta", "passage": "vano"}
NAME_KIND = {"finestra": "window", "f": "window", "porta": "door", "p": "door", "vano": "passage", "v": "passage",
             "passaggio": "passage"}
YES_NO_FALSE = {"no", "n", "0", "false", "falso", "elimina"}


def _num(v: float, digits: int = 1) -> str:
    """cm with a decimal comma, no trailing zeros (Excel in Italian reads it as a number)."""
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return (s or "0").replace(".", ",")


def _parse(text: str) -> float:
    return float(text.strip().replace(",", ".").replace("cm", ""))


def _side_name(o: Opening, hinge: int) -> str:
    """Compass name of the hinge side: walls running along x: ovest/est; along y: sud/nord."""
    ux, uy = o.axis
    if abs(ux) >= abs(uy):
        return "ovest" if hinge < 0 else "est"
    return "sud" if hinge < 0 else "nord"


def _hinge_from_text(o: Opening, text: str) -> int | None:
    t = text.strip().lower()
    if t in ("sinistra", "sx", "inizio", "-1"):
        return -1
    if t in ("destra", "dx", "fine", "1", "+1"):
        return 1
    for name, h in (("ovest", -1), ("est", 1), ("sud", -1), ("nord", 1)):
        if t == name:
            ux, uy = o.axis
            along_x = abs(ux) >= abs(uy)
            if name in ("ovest", "est") and along_x:
                return h
            if name in ("sud", "nord") and not along_x:
                return h
            return None  # a compass side that does not exist on this wall
    return None


def _hinge_cell(o: Opening) -> str:
    if o.kind == "door":
        if len(o.leaves) >= 2:
            return "doppia"
        return _side_name(o, o.leaves[0]["hinge"] if o.leaves else -1)
    if o.kind == "window" and o.sashes == 1 and o.hinge:
        return _side_name(o, o.hinge)
    return ""


def write_table(path: str | Path, openings: list[Opening], unit_scale: float) -> Path:
    """``unit_scale``: metres per drawing unit, to give x/y in the coordinates of the CAD file."""
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(COLUMNS)
        for o in sorted(openings, key=lambda o: o.id):
            src = o.src
            origin = ", ".join(f"{k}: {src[k]}" for k in ("width", "height", "sill", "kind") if k in src)
            ante = len(o.leaves) if o.kind == "door" else (o.sashes if o.kind == "window" else "")
            w.writerow([
                o.id, KIND_NAME[o.kind], _num(o.center[0] / unit_scale), _num(o.center[1] / unit_scale),
                _num(o.width * 100), _num(o.thickness * 100), _num(o.z0 * 100), _num((o.z1 - o.z0) * 100),
                ante, _hinge_cell(o), origin, src.get("leaves") or src.get("sashes") or "",
                o.label, " | ".join(o.notes), "", "", "", "", "", "", "",
            ])
    return path


def read_table(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as fh:
        header = fh.readline()  # column names hold neither: the header tells the separator
        fh.seek(0)
        delimiter = ";" if header.count(";") >= header.count(",") else ","
        return list(csv.DictReader(fh, delimiter=delimiter))


def apply_table(openings: list[Opening], rows: list[dict[str, str]], cfg: Config,
                warnings: list[str]) -> int:
    """Apply the MODIFICA_* cells. Returns how many openings were changed."""
    by_id = {o.id: o for o in openings}
    changed = 0
    for row in rows:
        oid = (row.get("id") or "").strip()
        edits = {k[len("MODIFICA_"):]: (v or "").strip() for k, v in row.items()
                 if k and k.startswith("MODIFICA_") and (v or "").strip()}
        if not oid or not edits:
            continue
        o = by_id.get(oid)
        if o is None:
            warnings.append(f"Tabella: l'id {oid} non esiste (le sigle cambiano se cambiano le opzioni): riga ignorata.")
            continue
        try:
            _apply_row(o, edits, cfg, warnings)
        except ValueError as exc:
            warnings.append(f"Tabella, {oid}: {exc}")
            continue
        changed += 1
    return changed


def _apply_row(o: Opening, edits: dict[str, str], cfg: Config, warnings: list[str]) -> None:
    if "tieni" in edits:
        if edits["tieni"].lower() in YES_NO_FALSE:
            o.keep = False
            o.notes.append("eliminata dalla tabella: il vano e' chiuso con muro")
            return
    sill_given = "davanzale" in edits
    height_given = "altezza" in edits
    if "tipo" in edits:
        kind = NAME_KIND.get(edits["tipo"].lower())
        if kind is None:
            raise ValueError(f"tipo '{edits['tipo']}' sconosciuto (finestra, porta, vano)")
        if kind != o.kind:
            o.kind = kind
            if not (sill_given or height_given):
                o.z0 = 0.0 if kind != "window" else cfg.window_sill
                o.z1 = min(cfg.wall_height, cfg.door_height if kind != "window" else cfg.window_sill + cfg.window_height)
            if kind == "door" and not o.leaves:
                o.leaves = [{"hinge": -1, "width": o.width, "swing": 1}]
                o.hinge = -1
            o.src["kind"] = "tabella"
            o.rebuild(cfg)  # the glass pane exists only for windows
    if "larghezza" in edits:
        width = _parse(edits["larghezza"]) / 100.0
        if width < 0.2:
            raise ValueError("larghezza troppo piccola (minimo 20 cm)")
        o.width = width
        o.src["width"] = "tabella"
        o.rebuild(cfg)
        o.dividers = [d for d in o.dividers if abs(d) < width / 2 - 0.12]
    if sill_given:
        o.z0 = max(0.0, _parse(edits["davanzale"]) / 100.0)
        o.src["sill"] = "tabella"
        if not height_given:
            o.z1 = max(o.z1, o.z0 + 0.2)
    if height_given:
        o.z1 = o.z0 + _parse(edits["altezza"]) / 100.0
        o.src["height"] = "tabella"
    if o.z1 > cfg.wall_height + 1e-6:
        o.notes.append(f"architrave {o.z1:.2f} m oltre l'altezza dei muri {cfg.wall_height:.2f} m: ridotta")
        o.z1 = cfg.wall_height
    if o.z1 - o.z0 < 0.2:
        raise ValueError("altezza troppo piccola (minimo 20 cm)")
    if "ante" in edits:
        n = int(round(_parse(edits["ante"])))
        if n < 1 or n > 8:
            raise ValueError("ante: da 1 a 8")
        if o.kind == "window":
            o.dividers = [(-o.width / 2 + k * o.width / n) for k in range(1, n)]
        elif o.kind == "door":
            if n >= 2:
                half = o.width / 2
                o.leaves = [{"hinge": -1, "width": half, "swing": 1}, {"hinge": 1, "width": half, "swing": 1}]
            else:
                o.leaves = [{"hinge": o.leaves[0]["hinge"] if o.leaves else -1, "width": o.width, "swing": 1}]
        o.src["leaves" if o.kind == "door" else "sashes"] = "tabella"
    if "cerniera" in edits:
        text = edits["cerniera"]
        if text.lower() == "doppia" and o.kind == "door":
            half = o.width / 2
            o.leaves = [{"hinge": -1, "width": half, "swing": 1}, {"hinge": 1, "width": half, "swing": 1}]
        else:
            hinge = _hinge_from_text(o, text)
            if hinge is None:
                raise ValueError(f"cerniera '{text}' non valida per questo muro (sinistra, destra, "
                                 f"{'ovest, est' if abs(o.axis[0]) >= abs(o.axis[1]) else 'sud, nord'}, doppia)")
            if o.kind == "door":
                width = o.width if len(o.leaves) < 2 else o.width / 2
                o.leaves = [{"hinge": hinge, "width": width, "swing": o.leaves[0]["swing"] if o.leaves else 1}]
            o.hinge = hinge
        o.src["leaves" if o.kind == "door" else "sashes"] = "tabella"
    if not math.isfinite(o.z0 + o.z1 + o.width):
        raise ValueError("valori non validi")
