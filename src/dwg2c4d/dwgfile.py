"""Open a DWG or DXF file as an ezdxf document.

ezdxf reads DXF only. DWG is converted first with, in order of preference:
1. the converter given with ``--converter`` (ODAFileConverter or LibreDWG ``dwg2dxf``),
2. ODA File Converter found by ezdxf's ``odafc`` add-on,
3. LibreDWG ``dwg2dxf`` found on PATH.
"""

from __future__ import annotations

import glob
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import ezdxf
from ezdxf.document import Drawing


class ConversionError(RuntimeError):
    """User-facing error (message is in Italian)."""


INSTALL_HELP = """\
Impossibile leggere il file DWG: nessun convertitore trovato.
Scegli una di queste strade:
  1. Installa ODA File Converter (gratuito): https://www.opendesign.com/guestfiles/oda_file_converter
     poi rilancia, oppure indica il percorso con --converter.
  2. Installa LibreDWG (comando 'dwg2dxf').
  3. Apri il DWG nel tuo CAD (AutoCAD, BricsCAD, progeCAD, LibreCAD...), usa
     'Salva con nome' -> DXF (meglio versione 2013/2018) e passa il file .dxf."""


def _is_dwg(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(2) == b"AC"
    except OSError:
        return False


def _read_dxf(path: Path) -> Drawing:
    try:
        return ezdxf.readfile(str(path))
    except ezdxf.DXFStructureError:
        from ezdxf import recover

        doc, _auditor = recover.readfile(str(path))
        return doc


def _run_dwg2dxf(exe: str, dwg: Path) -> Drawing:
    with tempfile.TemporaryDirectory(prefix="dwg2c4d_") as tmp:
        out = Path(tmp) / (dwg.stem + ".dxf")
        proc = subprocess.run(
            [exe, "-y", "-o", str(out), str(dwg)],
            capture_output=True, text=True, timeout=600,
        )
        if not out.exists() or out.stat().st_size == 0:
            raise ConversionError(
                f"dwg2dxf non ha prodotto un DXF (codice {proc.returncode}).\n"
                f"{(proc.stderr or proc.stdout).strip()[-500:]}"
            )
        return _read_dxf(out)


def _oda_candidates() -> list[str]:
    """Where ODA File Converter usually lives (it is not always on PATH)."""
    found = []
    on_path = shutil.which("ODAFileConverter")
    if on_path:
        found.append(on_path)
    if sys.platform.startswith("win"):
        for var in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(var)
            if base:
                pattern = os.path.join(base, "ODA", "ODAFileConverter*", "ODAFileConverter.exe")
                found += sorted(glob.glob(pattern), reverse=True)  # newest version first
    elif sys.platform == "darwin":
        found.append("/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter")
    return [c for c in found if os.path.isfile(c)]


def _run_oda(exe: str, dwg: Path) -> Drawing:
    from ezdxf.addons import odafc

    # ezdxf reads the converter location from its options, not from module attributes.
    ezdxf.options.set("odafc-addon", "win_exec_path", exe)
    ezdxf.options.set("odafc-addon", "unix_exec_path", exe)
    try:
        return odafc.readfile(str(dwg))
    except odafc.ODAFCError as exc:
        raise ConversionError(f"ODA File Converter ha fallito: {exc}") from exc


def open_drawing(path: str | Path, converter: str | None = None) -> Drawing:
    # ezdxf warns once per object it cannot copy (a drawing with proxy objects: hundreds of thousands of lines)
    logging.getLogger("ezdxf").setLevel(logging.ERROR)
    path = Path(path)
    if not path.is_file():
        raise ConversionError(f"File non trovato: {path}")
    suffix = path.suffix.lower()
    if suffix == ".dxf":
        return _read_dxf(path)
    if suffix != ".dwg" and not _is_dwg(path):
        raise ConversionError(f"Formato non supportato: {path.suffix or '(nessuna estensione)'} (usa .dwg o .dxf)")

    if converter:
        name = Path(converter).name.lower()
        if "dwg2dxf" in name:
            return _run_dwg2dxf(converter, path)
        return _run_oda(converter, path)

    candidates = _oda_candidates()
    if candidates:
        return _run_oda(candidates[0], path)
    exe = shutil.which("dwg2dxf")
    if exe:
        return _run_dwg2dxf(exe, path)
    raise ConversionError(INSTALL_HELP)
