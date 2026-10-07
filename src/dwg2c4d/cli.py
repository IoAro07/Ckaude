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


def _box5(text: str) -> tuple[float, ...]:
    try:
        vals = tuple(float(t) for t in text.split(","))
    except ValueError:
        vals = ()
    if len(vals) not in (4, 5):
        raise argparse.ArgumentTypeError(
            "usare xmin,ymin,xmax,ymax[,quota_Y_pavimento] (numeri separati da virgola)")
    return vals


def _pair(text: str) -> tuple[float, float]:
    try:
        vals = tuple(float(t) for t in text.split(","))
    except ValueError:
        vals = ()
    if len(vals) != 2:
        raise argparse.ArgumentTypeError("usare dx,dy (due numeri separati da virgola)")
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

    g = p.add_argument_group("prospetti e tetto")
    g.add_argument("--prospetto", type=_box5, action="append", metavar="XMIN,YMIN,XMAX,YMAX[,QUOTA_Y]",
                   help="zona di un prospetto (ripetibile, uno per facciata): da li' si leggono le quote "
                        "di porte e finestre. Va disegnato sotto (facciata sud) o sopra (nord) la pianta, "
                        "con le stesse coordinate X. La quota zero e' il fondo della porta; per forzarla "
                        "indica come quinto valore la Y del pavimento finito")
    g.add_argument("--tetto", action="store_true",
                   help="costruisci il tetto dalla pianta del tetto (layer Tetto/Roof/Copertura): "
                        "contorno + colmi/displuvi")
    g.add_argument("--layer-tetto", help="layer della pianta del tetto (virgole, * jolly)")
    g.add_argument("--area-tetto", type=_area, metavar="XMIN,YMIN,XMAX,YMAX",
                   help="dove e' disegnata la pianta del tetto (default: tutto il disegno)")
    g.add_argument("--pendenza", type=float, metavar="GRADI",
                   help="pendenza delle falde; se manca viene dedotta dai colmi del prospetto, "
                        "altrimenti 25 gradi")
    g.add_argument("--spessore-tetto", type=float, help="spessore del tetto (default 0.15 m)")
    g.add_argument("--sposta-tetto", type=_pair, metavar="DX,DY",
                   help="spostamento della pianta del tetto sulla pianta, in unita' del disegno "
                        "(default: centrata sui muri)")

    g = p.add_argument_group("elementi aggiuntivi")
    g.add_argument("--no-pavimento", action="store_true")
    g.add_argument("--spessore-pavimento", type=float)
    g.add_argument("--soffitto", action="store_true", help="aggiunge un solaio sopra i muri")
    g.add_argument("--no-vetri", action="store_true", help="non crea i vetri delle finestre")
    g.add_argument("--infissi", choices=["dettagliati", "semplici"], default="dettagliati",
                   help="dettagliati (default): imbotto, cornice, telai, ante, maniglie, toppe; "
                        "semplici: solo una lastra di vetro per finestra")

    g = p.add_argument_group("disegno e output")
    g.add_argument("--unita", choices=sorted(UNIT_TO_METERS),
                   help="unita' del disegno (default: lette dal file)")
    g.add_argument("--unita-output", choices=["m", "cm", "mm"], help="unita' dell'OBJ (default m)")
    g.add_argument("--area", type=_area, metavar="XMIN,YMIN,XMAX,YMAX",
                   help="usa solo questa zona del disegno (coordinate del disegno)")
    g.add_argument("--origine", choices=["centro", "minimo", "disegno"], default="centro",
                   help="dove mettere lo zero del modello: al centro dei muri (default), nell'angolo "
                        "minimo, oppure alle coordinate del disegno (di solito molto lontane dall'origine)")
    g.add_argument("--json-c4d", action="store_true",
                   help="scrivi anche NOME_model.json, da importare con uno script in Cinema 4D "
                        "(oggetti nativi, un materiale per tipo, anche Corona)")
    g.add_argument("--no-vani", action="store_true",
                   help="non dedurre i vani senza simbolo (due testate di muro allineate con un vuoto in mezzo)")
    g.add_argument("--vano-max", type=float, metavar="M",
                   help="larghezza massima di un vano dedotto dai muri, in metri (default 2)")
    g.add_argument("--no-scritte", action="store_true",
                   help="non leggere i testi del disegno (quote delle aperture, nomi e altezze dei locali)")
    g.add_argument("--no-testi-esplosi", action="store_true",
                   help="non cercare testi trasformati in linee (lettere disegnate con le linee)")
    g.add_argument("--layer-testi", metavar="LAYER", type=_globs,
                   help="layer in cui cercare testi esplosi, oltre allo 0 e a quelli chiamati quote/testi/... "
                        "(virgole, * jolly)")
    g.add_argument("--raggio-scritte", type=float, metavar="M",
                   help="distanza massima tra un'apertura e la sua quota scritta, in metri (default 1,2)")
    g.add_argument("--tabella", metavar="FILE.csv",
                   help="applica la tabella delle aperture corretta a mano (colonne MODIFICA_*), "
                        "scritta da una conversione precedente come NOME_aperture.csv")
    g.add_argument("--no-tabella", action="store_true",
                   help="non scrive NOME_aperture.csv")
    g.add_argument("--specchia", action="store_true",
                   help="specchia la pianta (se in Cinema 4D risulta capovolta)")
    g.add_argument("--converter", help="percorso di ODAFileConverter o dwg2dxf")
    return p


def config_from_args(args: argparse.Namespace) -> Config:
    cfg = Config.from_json(args.config) if args.config else Config()
    overrides = dict(cfg.layers.overrides)
    for cat, value in (("wall", args.muri), ("door", args.porte), ("window", args.finestre),
                       ("column", args.pilastri), ("roof", args.layer_tetto)):
        if value:
            overrides[cat] = _globs(value)
    cfg.layers = LayerRules(overrides)

    simple = {
        "wall_height": args.altezza_muri, "door_height": args.altezza_porte,
        "window_sill": args.davanzale, "window_height": args.altezza_finestre,
        "wall_thickness": args.spessore_muro, "max_wall_thickness": args.spessore_max,
        "floor_thickness": args.spessore_pavimento, "units": args.unita,
        "out_units": args.unita_output, "area": args.area, "converter": args.converter,
        "roof_area": args.area_tetto, "roof_pitch": args.pendenza,
        "roof_thickness": args.spessore_tetto, "roof_offset": args.sposta_tetto,
    }
    for name, value in simple.items():
        if value is not None:
            setattr(cfg, name, value)
    if args.modalita_muri:
        cfg.wall_mode = MODE_NAMES[args.modalita_muri]
    if args.prospetto:
        cfg.elevations = list(args.prospetto)
    if args.tetto or args.layer_tetto:
        cfg.roof = True
    if args.no_pavimento:
        cfg.floor_thickness = 0.0
    if args.soffitto:
        cfg.ceiling = True
    if args.no_vetri:
        cfg.glass = False
    cfg.fixtures = "detailed" if args.infissi == "dettagliati" else "simple"
    if args.includi_nascosti:
        cfg.include_hidden = True
    if args.muri_da_blocchi:
        cfg.walls_from_blocks = True
    if args.specchia:
        cfg.mirror = True
    cfg.origin = {"centro": "center", "minimo": "min", "disegno": "drawing"}[args.origine]
    if args.json_c4d:
        cfg.c4d_json = True
    if args.no_vani:
        cfg.passages = False
    if args.vano_max is not None:
        cfg.passage_max = args.vano_max
    if args.no_scritte:
        cfg.texts = False
    if args.no_testi_esplosi:
        cfg.vector_text = False
    if args.layer_testi:
        cfg.text_layers = list(args.layer_testi)
    if args.raggio_scritte is not None:
        cfg.label_radius = args.raggio_scritte
    if args.altezza_muri is not None:
        cfg.wall_height_auto = False
    if args.tabella:
        cfg.table_in = args.tabella
    if args.no_tabella:
        cfg.write_table = False
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
    if report.json_path:
        print(f"Creato {report.json_path}  (per lo script di importazione di Cinema 4D)")
    if report.table_path:
        print(f"Creato {report.table_path}  (tabella delle aperture: correggi le colonne MODIFICA_* "
              f"e rilancia con --tabella)")
    note = " (dedotte: verifica!)" if report.unit_guessed else ""
    print(f"  Unita' del disegno : {report.unit}{note}")
    if report.origin_offset != (0.0, 0.0):
        print(f"  Origine            : modello spostato di ({report.origin_offset[0]:.2f}, "
              f"{report.origin_offset[1]:.2f}) m rispetto al disegno")
    print(f"  Ingombro muri      : {w:.2f} x {h:.2f} m")
    print(f"  Muri               : {report.wall_pieces} corpi, {report.wall_area_m2:.1f} m2 in pianta")
    print(f"  Porte / finestre   : {report.doors} / {report.windows}")
    if report.passages:
        print(f"  Vani senza simbolo : {report.passages} (dedotti dai muri: verifica nella tabella)")
    src = {"scritta": "dalla scritta 'h' nei locali", "indicata": "indicata", "predefinita": "predefinita"}
    print(f"  Altezza muri       : {report.wall_height:g} m ({src[report.wall_height_source]})")
    if report.labels:
        print(f"  Scritte            : quote lette per {report.labels} aperture su {report.doors + report.windows}")
    for room in report.rooms:
        h = f", h {room['height']:g} m" if room["height"] else ""
        mark = "" if room["named"] else "  (senza nome)"
        print(f"  Locale             : {room['name']}  {room['area_m2']:.1f} m2{h}{mark}")
    for ev in report.elevations:
        side = {"south": "sud", "north": "nord"}[ev["side"]]
        src = "dal fondo della porta" if ev["zero_source"] == "porta" else "indicata"
        print(f"  Prospetto {side:<5}    : {ev['matched']} di {ev['total']} aperture con le quote del prospetto "
              f"(quota zero {src})")
    if report.roof:
        r = report.roof
        src = {"indicata": "indicata", "prospetto": "dedotta dal prospetto",
               "predefinita": "predefinita"}[r["pitch_source"]]
        lo, hi = r["pitch_range"]
        slope = f"{r['pitch_deg']:.0f}" if hi - lo < 1.0 else f"{lo:.0f}-{hi:.0f}"
        print(f"  Tetto              : {r['faces']} falde, pendenza {slope} gradi ({src}), "
              f"colmo a {r['ridge_height']:.2f} m")
    if report.columns:
        print(f"  Pilastri           : {report.columns}")
    print(f"  Facce / oggetti    : {report.faces} / {', '.join(report.groups)}")
    for msg in report.warnings:
        print(f"  ATTENZIONE: {msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
