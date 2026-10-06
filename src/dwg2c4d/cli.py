"""Command line interface (Italian)."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import UNIT_TO_METERS, Config, LayerRules
from .dwgfile import ConversionError, open_drawing
from .pipeline import convert
from .reader import declared_units, layer_summary

MODE_NAMES = {"auto": "auto", "solidi": "solid", "doppia-linea": "faces", "asse": "centerline"}
CATEGORY_IT = {"wall": "muri", "door": "porte", "window": "finestre", "column": "pilastri"}


def _globs(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()]


def _area(text: str) -> tuple[float, float, float, float]:
    try:
        vals = tuple(float(t) for t in text.split(","))
    except ValueError:
        vals = ()
    if len(vals) != 4:
        raise argparse.ArgumentTypeError("usare xmin,ymin,xmax,ymax (numeri separati da virgola)")
    return vals  # type: ignore[return-value]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dwg2c4d",
        description="Trasforma una planimetria 2D (DWG/DXF) in un modello 3D OBJ per Cinema 4D.",
        epilog="Esempio: dwg2c4d pianta.dwg -o pianta.obj --altezza-muri 2.70",
    )
    p.add_argument("input", help="file .dwg o .dxf")
    p.add_argument("-o", "--output", help="file .obj di destinazione (default: accanto all'input)")
    p.add_argument("--config", help="file JSON con le impostazioni (le opzioni da riga di comando prevalgono)")
    p.add_argument("--elenca-layer", action="store_true",
                   help="mostra i layer del disegno e come vengono classificati, poi esce")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    g = p.add_argument_group("layer (nomi separati da virgola, jolly * ammessi)")
    g.add_argument("--muri")
    g.add_argument("--porte")
    g.add_argument("--finestre")
    g.add_argument("--pilastri")
    g.add_argument("--includi-nascosti", action="store_true", help="usa anche layer spenti/congelati")
    g.add_argument("--muri-da-blocchi", action="store_true",
                   help="leggi come muri anche i blocchi inseriti su un layer di muri "
                        "(di solito sono arredi e vengono ignorati)")

    g = p.add_argument_group("altezze e spessori (metri)")
    g.add_argument("--altezza-muri", type=float)
    g.add_argument("--altezza-porte", type=float)
    g.add_argument("--davanzale", type=float, help="quota del davanzale delle finestre")
    g.add_argument("--altezza-finestre", type=float)
    g.add_argument("--spessore-muro", type=float,
                   help="spessore dei muri disegnati con una sola linea (asse)")
    g.add_argument("--spessore-max", type=float,
                   help="spessore massimo di un muro a doppia linea (default 0.60)")
    g.add_argument("--modalita-muri", choices=sorted(MODE_NAMES),
                   help="come interpretare i muri: auto (default), solidi (polilinee chiuse/campiture), "
                        "doppia-linea, asse")

    g = p.add_argument_group("elementi aggiuntivi")
    g.add_argument("--no-pavimento", action="store_true")
    g.add_argument("--spessore-pavimento", type=float)
    g.add_argument("--soffitto", action="store_true", help="aggiunge un solaio sopra i muri")
    g.add_argument("--no-vetri", action="store_true", help="non crea i vetri delle finestre")

    g = p.add_argument_group("disegno e output")
    g.add_argument("--unita", choices=sorted(UNIT_TO_METERS),
                   help="unita' del disegno (default: lette dal file)")
    g.add_argument("--unita-output", choices=["m", "cm", "mm"], help="unita' dell'OBJ (default m)")
    g.add_argument("--area", type=_area, metavar="XMIN,YMIN,XMAX,YMAX",
                   help="usa solo questa zona del disegno (coordinate del disegno)")
    g.add_argument("--specchia", action="store_true",
                   help="specchia la pianta (se in Cinema 4D risulta capovolta)")
    g.add_argument("--converter", help="percorso di ODAFileConverter o dwg2dxf")
    return p


def config_from_args(args: argparse.Namespace) -> Config:
    cfg = Config.from_json(args.config) if args.config else Config()
    overrides = dict(cfg.layers.overrides)
    for cat, value in (("wall", args.muri), ("door", args.porte),
                       ("window", args.finestre), ("column", args.pilastri)):
        if value:
            overrides[cat] = _globs(value)
    cfg.layers = LayerRules(overrides)

    simple = {
        "wall_height": args.altezza_muri, "door_height": args.altezza_porte,
        "window_sill": args.davanzale, "window_height": args.altezza_finestre,
        "wall_thickness": args.spessore_muro, "max_wall_thickness": args.spessore_max,
        "floor_thickness": args.spessore_pavimento, "units": args.unita,
        "out_units": args.unita_output, "area": args.area, "converter": args.converter,
    }
    for name, value in simple.items():
        if value is not None:
            setattr(cfg, name, value)
    if args.modalita_muri:
        cfg.wall_mode = MODE_NAMES[args.modalita_muri]
    if args.no_pavimento:
        cfg.floor_thickness = 0.0
    if args.soffitto:
        cfg.ceiling = True
    if args.no_vetri:
        cfg.glass = False
    if args.includi_nascosti:
        cfg.include_hidden = True
    if args.muri_da_blocchi:
        cfg.walls_from_blocks = True
    if args.specchia:
        cfg.mirror = True
    return cfg


def _print_layers(rows: list[dict], units: str | None) -> None:
    print(f"Unita' dichiarate nel file: {units or 'nessuna'} "
          "(se le misure non tornano, forzale con --unita)\n")
    width = max([len(r["layer"]) for r in rows] + [5])
    print(f"{'LAYER'.ljust(width)}  USO COME   CONTENUTO")
    for r in rows:
        cat = CATEGORY_IT.get(r["category"], "-")
        what = ", ".join(f"{n} {k}" for k, n in sorted(r["entities"].items()))
        if r["blocks"]:
            what += f"  [blocchi: {', '.join(r['blocks'][:4])}{'...' if len(r['blocks']) > 4 else ''}]"
        flag = "  (nascosto)" if r["hidden"] else ""
        print(f"{r['layer'].ljust(width)}  {cat.ljust(9)}  {what}{flag}")
        if r["bounds"]:
            x0, y0, x1, y1 = r["bounds"]
            print(f"{' ' * width}  {' ' * 9}    zona: x {x0:.0f}..{x1:.0f}   y {y0:.0f}..{y1:.0f}")
    print("\nSe un layer e' classificato male, indicalo a mano, es.: --muri \"A-MURI*,TRAMEZZI\"")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = config_from_args(args)
        cfg.validate()
        if args.elenca_layer:
            doc = open_drawing(args.input, cfg.converter)
            _print_layers(layer_summary(doc, cfg), declared_units(doc))
            return 0
        report = convert(args.input, args.output, cfg)
    except (ConversionError, ValueError, OSError) as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 1

    w, h = report.size_m
    print(f"Creato {report.output}  (+ {report.output.with_suffix('.mtl').name})")
    note = " (dedotte: verifica!)" if report.unit_guessed else ""
    print(f"  Unita' del disegno : {report.unit}{note}")
    print(f"  Ingombro muri      : {w:.2f} x {h:.2f} m")
    print(f"  Muri               : {report.wall_pieces} corpi, {report.wall_area_m2:.1f} m2 in pianta")
    print(f"  Porte / finestre   : {report.doors} / {report.windows}")
    if report.columns:
        print(f"  Pilastri           : {report.columns}")
    print(f"  Facce / oggetti    : {report.faces} / {', '.join(report.groups)}")
    for msg in report.warnings:
        print(f"  ATTENZIONE: {msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
