"""Crea una pianta DXF di prova: python examples/make_sample.py [stile] [file.dxf]

Stili: lines (default), jambs, polylines, hatch, centerline.
Poi: dwg2c4d esempio.dxf -o esempio.obj
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dwg2c4d.sample import build_sample  # noqa: E402

style = sys.argv[1] if len(sys.argv) > 1 else "lines"
out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).with_name("esempio.dxf")
print("Creato", build_sample(out, style))
