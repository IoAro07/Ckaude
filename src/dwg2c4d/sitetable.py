"""The table of the site: every surface, curb, wall and stair of the site with what was read, and a way to correct it by hand, as for the
openings (``table``) and the garden (``garden_table``).

``NAME_sito.csv`` (``;`` separated, centimetres, comma as decimal point): one row for each object of the site (``Sito_Prato_01``...).
Fill the ``MODIFICA_*`` columns in Excel and run again with ``--tabella-sito NAME_sito.csv``; only the filled cells count:

* ``MODIFICA_tipo``: what a surface is made of (strada, pavimentazione, prato, acqua, terreno), or whether a strip is a wall or a curb
  (muro, cordolo); the object takes the name and the material of the new kind (and its level or its height, unless given).
* ``MODIFICA_quota``: the top of a surface above the paving round the house, in cm (negative: below it).
* ``MODIFICA_altezza``: the height of a wall or a curb, in cm.
* ``MODIFICA_alzata``: the height of one riser of a stair, in cm.
* ``MODIFICA_tieni``: ``no`` leaves the object out.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .config import Config
from .sitebands import BandStair
from .sitesteps import Staircase
from .sitesurfaces import KIND_NAMES, KIND_ORDER, TOP, SiteSurfaces
from .sitewalls import KIND_NAMES as WALL_NAMES
from .sitewalls import SiteWall
from .mesh import SITE_PREFIX
from .table import YES_NO_FALSE, _num, _parse, read_table

COLUMNS = ["id", "oggetto", "tipo", "layer", "area_m2", "lunghezza_m", "quota_cm", "altezza_cm", "alzata_cm", "perche",
           "MODIFICA_tipo", "MODIFICA_quota", "MODIFICA_altezza", "MODIFICA_alzata", "MODIFICA_tieni"]
KIND_SYNONYMS = {"strada": "strada", "strade": "strada", "pavimentazione": "pavimentazione", "pavimento": "pavimentazione",
                 "prato": "prato", "erba": "prato", "acqua": "acqua", "terreno": "terreno", "terra": "terreno"}
WALL_SYNONYMS = {"muro": "muro", "muretto": "muro", "contenimento": "muro", "cordolo": "cordolo", "cordoli": "cordolo"}
LEVEL_RANGE = (-3.0, 3.0)  # m: a top this far above or below the paving is believed
HEIGHT_RANGE = (0.02, 6.0)  # m: a wall or a curb of this height is believed
RISE_RANGE = (0.05, 0.40)  # m: a riser of this height is believed


def write_site_table(path: str | Path, site: SiteSurfaces | None, stairs: list[Staircase]) -> Path:
    """The table of the site: the surfaces, the curbs and walls, the stairs (``site`` None: only the stairs)."""
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(COLUMNS)
        blank = ["", "", "", "", ""]
        for s in (site.surfaces if site is not None else []):
            w.writerow([s.id, "superficie", KIND_NAMES[s.kind].lower(), s.layer, _num(s.geom.area), "", _num(s.top * 100), "", "",
                        f"{s.source}: {s.why}", *blank])
        for k in (site.walls if site is not None else []):
            w.writerow([k.id, WALL_NAMES[k.kind].lower(), WALL_NAMES[k.kind].lower(), k.layer, "", _num(k.axis.length), "",
                        _num(k.height * 100), "", f"spessore {k.thickness * 100:.0f} cm: {k.why}; altezza {k.height_source}", *blank])
        for st in stairs:
            w.writerow([st.id, "gradini", "gradini", "", "", _num(st.footprint.length / 2.0), "", "", _num(st.rise * 100),
                        f"{st.count} gradini, sale verso {st.toward}; alzata {st.rise_source}", *blank])
        for b in (site.stairs if site is not None else []):
            w.writerow([b.id, "gradinata", "gradinata", "", _num(b.geom.area), "", "", "", _num(b.rise * 100),
                        f"{b.count} gradini a campitura, sale verso {b.toward}; alzata predefinita", *blank])
    return path


def _value(text: str, what: str, low: float, high: float) -> float:
    value = _parse(text) / 100.0
    if not low <= value <= high:
        raise ValueError(f"{what} non valida (da {low * 100:g} a {high * 100:g} cm)")
    return value


def _renumber(site: SiteSurfaces) -> None:
    """The names of the surfaces and the walls after a change of kind: Sito_<Kind>_NN, numbered in the order they had."""
    count: dict[str, int] = {}
    for s in sorted(site.surfaces, key=lambda s: (KIND_ORDER.index(s.kind), s.id)):
        count[s.kind] = count.get(s.kind, 0) + 1
        s.id = f"{SITE_PREFIX}{KIND_NAMES[s.kind]}_{count[s.kind]:02d}"
    count = {}
    for w in sorted(site.walls, key=lambda w: (w.kind != "muro", w.id)):
        count[w.kind] = count.get(w.kind, 0) + 1
        w.id = f"{SITE_PREFIX}{WALL_NAMES[w.kind]}_{count[w.kind]:02d}"


def _edit(obj, edits: dict[str, str], cfg: Config) -> None:
    """One row of the table applied to the object it names."""
    if edits.get("tieni", "").lower() in YES_NO_FALSE:
        obj.keep = False
        return
    if isinstance(obj, (Staircase, BandStair)):
        if "alzata" in edits:
            obj.rise = _value(edits["alzata"], "alzata", *RISE_RANGE)
            if isinstance(obj, Staircase):
                obj.rise_source = "tabella"
        return
    if isinstance(obj, SiteWall):
        if "tipo" in edits:
            kind = WALL_SYNONYMS.get(edits["tipo"].lower())
            if kind is None:
                raise ValueError(f"tipo '{edits['tipo']}' sconosciuto per un muro (muro, cordolo)")
            if kind != obj.kind:
                obj.kind = kind
                obj.height, obj.height_source = (cfg.low_wall_height if kind == "muro" else cfg.curb_height), "predefinita"
        if "altezza" in edits:
            obj.height, obj.height_source = _value(edits["altezza"], "altezza", *HEIGHT_RANGE), "tabella"
        return
    if "tipo" in edits:
        kind = KIND_SYNONYMS.get(edits["tipo"].lower())
        if kind is None:
            raise ValueError(f"tipo '{edits['tipo']}' sconosciuto per una superficie (strada, pavimentazione, prato, acqua, terreno)")
        obj.kind, obj.top = kind, TOP[kind]
    if "quota" in edits:
        obj.top = _value(edits["quota"], "quota", *LEVEL_RANGE)


def apply_site_table(site: SiteSurfaces | None, stairs: list[Staircase], rows: list[dict[str, str]], cfg: Config,
                     warnings: list[str]) -> int:
    """Apply the MODIFICA_* cells: returns how many objects were changed."""
    by_id = {o.id: o for o in [*(site.surfaces if site else []), *(site.walls if site else []), *stairs,
                              *(site.stairs if site else [])]}
    changed = 0
    for row in rows:
        oid = (row.get("id") or "").strip()
        edits = {k[len("MODIFICA_"):]: (v or "").strip() for k, v in row.items()
                 if k and k.startswith("MODIFICA_") and (v or "").strip()}
        if not oid or not edits:
            continue
        obj = by_id.get(oid)
        if obj is None:
            warnings.append(f"Tabella del sito: l'id {oid} non esiste (le sigle cambiano se cambia il disegno): riga ignorata.")
            continue
        try:
            _edit(obj, edits, cfg)
        except ValueError as exc:
            warnings.append(f"Tabella del sito, {oid}: {exc}")
            continue
        changed += 1
    if site is not None:
        _renumber(site)
    return changed


def read_site_table(path: str | Path) -> list[dict[str, str]]:
    return read_table(path)
