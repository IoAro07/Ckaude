"""The garden table: every plant and piece of garden furniture with what was read and where it came from, and a
way to correct it by hand, as for the openings (``table``).

``NAME_giardino.csv`` (``;`` separated, centimetres, comma as decimal point): one row for each hedge, tree, shrub
and piece of furniture. Fill the ``MODIFICA_*`` columns in Excel and run again with
``--tabella-giardino NAME_giardino.csv``; only the filled cells count. A size from the table beats the one of the
elevation, which beats the default.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

from .config import Config
from .garden import KIND_NAMES, NAME_KINDS, Garden, GardenObject, default_height, place_on_ground
from .table import YES_NO_FALSE, _num, _parse, read_table

COLUMNS = [
    "id", "tipo", "blocco", "layer", "x", "y", "rotazione", "lunghezza", "larghezza", "altezza",
    "origine_altezza", "note",
    "MODIFICA_tipo", "MODIFICA_lunghezza", "MODIFICA_larghezza", "MODIFICA_altezza", "MODIFICA_tieni",
]
KIND_SYNONYMS = {"albero": "tree", "alberi": "tree", "siepe": "hedge", "siepi": "hedge", "cespuglio": "shrub",
                 "cespugli": "shrub", "arbusto": "shrub", "arredo": "furniture", "arredi": "furniture",
                 "altro": "furniture"}


def write_garden_table(path: str | Path, garden: Garden, unit_scale: float) -> Path:
    """``unit_scale``: metres per drawing unit, to give x/y in the coordinates of the CAD file."""
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(COLUMNS)
        for o in sorted(garden.objects, key=lambda o: o.id):
            w.writerow([
                o.id, KIND_NAMES[o.kind], o.block, o.layer, _num(o.cx / unit_scale), _num(o.cy / unit_scale),
                _num(math.degrees(o.angle)), _num(o.length * 100), _num(o.width * 100), _num(o.height * 100),
                o.height_src, " | ".join(o.notes), "", "", "", "", "",
            ])
    return path


def apply_garden_table(garden: Garden, rows: list[dict[str, str]], cfg: Config, warnings: list[str]) -> int:
    """Apply the MODIFICA_* cells. Returns how many objects were changed."""
    by_id = {o.id: o for o in garden.objects}
    changed = 0
    for row in rows:
        oid = (row.get("id") or "").strip()
        edits = {k[len("MODIFICA_"):]: (v or "").strip() for k, v in row.items()
                 if k and k.startswith("MODIFICA_") and (v or "").strip()}
        if not oid or not edits:
            continue
        o = by_id.get(oid)
        if o is None:
            warnings.append(f"Tabella del giardino: l'id {oid} non esiste (le sigle cambiano se cambia il disegno): "
                            "riga ignorata.")
            continue
        try:
            _apply_row(o, edits, cfg)
        except ValueError as exc:
            warnings.append(f"Tabella del giardino, {oid}: {exc}")
            continue
        changed += 1
    place_on_ground(garden.objects, garden.surfaces, cfg)
    return changed


def _size(text: str, what: str, low: float, high: float) -> float:
    value = _parse(text) / 100.0
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{what} non valida (da {low * 100:g} a {high * 100:g} cm)")
    return value


def _apply_row(o: GardenObject, edits: dict[str, str], cfg: Config) -> None:
    if "tieni" in edits and edits["tieni"].lower() in YES_NO_FALSE:
        o.keep = False
        o.notes.append("eliminato dalla tabella")
        return
    if "tipo" in edits:
        kind = KIND_SYNONYMS.get(edits["tipo"].lower()) or NAME_KINDS.get(edits["tipo"].lower())
        if kind is None:
            raise ValueError(f"tipo '{edits['tipo']}' sconosciuto (albero, siepe, cespuglio, arredo)")
        if kind != o.kind:
            o.kind = kind
            if "altezza" not in edits:
                o.height, o.height_src = default_height(kind, o.block, cfg), "predefinita"
    if "lunghezza" in edits:
        o.length = _size(edits["lunghezza"], "lunghezza", 0.05, 100.0)
    if "larghezza" in edits:
        o.width = _size(edits["larghezza"], "larghezza", 0.05, 100.0)
    if "altezza" in edits:
        o.height, o.height_src = _size(edits["altezza"], "altezza", 0.05, 30.0), "tabella"


def read_garden_table(path: str | Path) -> list[dict[str, str]]:
    return read_table(path)
