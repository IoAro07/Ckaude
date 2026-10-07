"""What could the layers that were not recognised by name be? A proposal with its confidence and why.

Nothing here is applied unless asked for (``--accetta-proposte``): the program shows what it would
think and lets you decide. Names tell some layers at once (furniture, text, hatch...); for line
work the hypothesis is tried for real: a layer that builds into walls enclosing rooms is probably the
walls; a layer whose symbols sit on those walls and carry swing arcs is probably the doors.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .config import Config, LayerRules

GARDEN_LABELS = {"water": "giardino: acqua / piscina", "lawn": "giardino: prato", "paving": "giardino: pavimentazione esterna",
                 "plants": "giardino: vegetazione (alberi, siepi, cespugli)", "furniture": "giardino: arredi esterni",
                 "garden": "giardino / esterno"}
NAME_HINTS = (
    (("arred", "furnit", "mobil", "sanit", "bagno_"), "arredi (blocchi e linee di mobili)"),
    (("quot", "dimens", "testi", "text", "annot", "scritt", "note"), "quote e testi"),
    (("retin", "hatch", "campit", "tratteg"), "retini e campiture"),
    (("verde", "landscap", "giardin", "vegeta"), "verde"),
    (("prospett", "sezion", "section", "elevat"), "prospetti / sezioni"),
    (("luci", "prese", "interrutt", "elettr", "impiant", "light", "elec"), "impianti"),
    (("immagin", "riferiment", "image", "xref"), "immagini di riferimento"),
)
MIN_WALL_AREA = 2.0  # m2
WALL_THICKNESS = (0.04, 0.8)  # m: average thickness of what the layer builds


@dataclass
class Proposal:
    layer: str
    category: str | None  # a CATEGORIES entry, or None when it is something that is not part of the plan
    label: str  # what it seems to be, in words
    confidence: str  # alta | media | bassa
    reason: str


def _name_hint(layer: str) -> str | None:
    low = layer.lower()
    for tokens, label in NAME_HINTS:
        if any(t in low for t in tokens):
            return label
    return None


def _only(counts: dict[str, int], kinds: tuple[str, ...]) -> bool:
    return bool(counts) and all(k in kinds for k in counts)


def _try_walls(doc, cfg: Config, layer: str):
    """Build ``layer`` as the walls: (footprint, config used, how the unit was read)."""
    from .pipeline import _better_unit, _extent
    from .reader import read_items
    from .walls import build_walls

    trial = replace(cfg, layers=LayerRules({"wall": [layer]}))
    res = read_items(doc, trial)
    walls = build_walls([it for it in res.items if it.layer == layer], trial, [])
    note = ""
    if not walls.is_empty:
        span = max(_extent(walls))
        if not (3.0 <= span <= 300.0) and trial.units is None and not res.unit_guessed:
            alt = _better_unit(span, res.unit)
            if alt:
                trial = replace(trial, units=alt)
                res = read_items(doc, trial)
                walls = build_walls([it for it in res.items if it.layer == layer], trial, [])
                note = f" (misure lette in {alt})"
    return walls, trial, note


def propose_layers(doc, cfg: Config, rows: list[dict]) -> list[Proposal]:
    """Proposals for the rows whose category is None. ``rows``: ``layer_summary`` output."""
    from .geom import polygons_of
    from .openings import build_openings
    from .reader import read_items
    from .walls import build_walls

    out: list[Proposal] = []
    unknown = [r for r in rows if r["category"] is None]
    drawable = ("LINE", "LWPOLYLINE", "ARC", "POLYLINE", "CIRCLE", "SPLINE", "ELLIPSE")
    best: tuple[str, Config, int, float] | None = None  # strongest wall hypothesis: layer, config, rooms, area
    undecided: list[dict] = []

    for row in unknown:
        layer, counts = row["layer"], row["entities"]
        hint = GARDEN_LABELS.get(row.get("garden") or "") or _name_hint(layer)
        if hint:
            out.append(Proposal(layer, None, hint, "alta", "dal nome del layer"))
        elif _only(counts, ("TEXT", "MTEXT", "DIMENSION", "LEADER", "MULTILEADER", "ATTRIB")):
            out.append(Proposal(layer, None, "quote e testi", "alta", "contiene solo testi/quote"))
        elif _only(counts, ("HATCH", "SOLID")):
            out.append(Proposal(layer, None, "retini e campiture", "media", "contiene solo campiture"))
        elif _only(counts, ("INSERT",)):
            out.append(Proposal(layer, None, "blocchi (arredi o simboli)", "media",
                                f"solo blocchi: {', '.join(row['blocks'][:3])}"))
        elif sum(n for k, n in counts.items() if k in drawable) >= 2:
            undecided.append(row)

    for row in undecided:  # line work: try it as walls
        layer = row["layer"]
        try:
            walls, trial, note = _try_walls(doc, cfg, layer)
        except Exception:
            continue
        if walls.is_empty or walls.area < MIN_WALL_AREA:
            continue
        thickness = 2.0 * walls.area / walls.length if walls.length else 0.0
        if not WALL_THICKNESS[0] <= thickness <= WALL_THICKNESS[1]:
            continue
        rooms = sum(len(p.interiors) for p in polygons_of(walls))
        if rooms >= 1:
            out.append(Proposal(layer, "wall", "muri", "alta" if rooms >= 2 else "media",
                                f"le linee formano muri spessi {thickness * 100:.0f} cm che chiudono "
                                f"{rooms} locali{note}"))
            if best is None or (rooms, walls.area) > (best[2], best[3]):
                best = (layer, trial, rooms, walls.area)
        else:
            out.append(Proposal(layer, "wall", "muri (o contorni)", "bassa",
                                f"forma strisce spesse {thickness * 100:.0f} cm ma senza chiudere locali{note}"))

    for i, p in enumerate(out):  # one walls layer is the walls; the other candidates are only a guess
        if p.category == "wall" and best is not None and p.layer != best[0] and p.confidence != "bassa":
            out[i] = replace(p, confidence="bassa", reason=f"{p.reason}; il candidato migliore e' '{best[0]}'")

    # line work that is not walls: symbols on the walls (swing arcs = doors, otherwise windows)
    base = best[1] if best else cfg
    has_walls = best is not None or any(r["category"] == "wall" for r in rows)
    if has_walls:
        wall_override = {"wall": [best[0]]} if best else {}
        for row in undecided:
            layer = row["layer"]
            if any(p.layer == layer for p in out if p.category == "wall"):
                continue  # already read as walls
            trial = replace(base, layers=LayerRules({**wall_override, "door": [layer]}))
            try:
                res = read_items(doc, trial)
                walls = build_walls(res.items, trial, [])
                ops = build_openings(res.items, walls, trial, []) if not walls.is_empty else []
            except Exception:
                continue
            doors = sum(1 for o in ops if o.src.get("leaves") == "arco")
            if doors:
                out.append(Proposal(layer, "door", "porte", "alta" if doors >= 2 else "media",
                                    f"{len(ops)} simboli sui muri, {doors} con arco di rotazione"))
            elif ops:
                out.append(Proposal(layer, "window", "forse finestre", "bassa",
                                    f"{len(ops)} simboli sui muri, senza archi di rotazione"))
    return out


def apply_proposals(cfg: Config, proposals: list[Proposal], rows: list[dict],
                    minimum: str = "media") -> list[Proposal]:
    """Put the proposals at or above ``minimum`` confidence into ``cfg.layers``. A category that the layer
    names (or the user) already settled is left alone: a proposal never replaces a recognised layer."""
    order = {"bassa": 0, "media": 1, "alta": 2}
    known = {r["category"] for r in rows if r["category"]}
    taken: list[Proposal] = []
    for p in proposals:
        if p.category is None or order[p.confidence] < order[minimum]:
            continue
        if p.category in cfg.layers.overrides or p.category in known:
            continue
        cfg.layers.overrides.setdefault(p.category, []).append(p.layer)
        taken.append(p)
    return taken
