import ezdxf
import pytest

from dwg2c4d.sample import build_sample


@pytest.fixture
def sample_lines(tmp_path):
    return build_sample(tmp_path / "lines.dxf", "lines")


def make_square_plan(path, side=500.0, thick=30.0, units=5, layer="MURI", ocs_flip=False):
    """Outer + inner closed polylines on a wall layer (a square building)."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = units or 0  # 0 = unspecified (ezdxf's default would be metres)
    doc.layers.add(layer)
    msp = doc.modelspace()
    outer = [(0, 0), (side, 0), (side, side), (0, side)]
    inner = [(thick, thick), (side - thick, thick), (side - thick, side - thick), (thick, side - thick)]
    for pts in (outer, inner):
        if ocs_flip:
            pts = [(-x, y) for x, y in pts]  # OCS x is the mirror of WCS x for extrusion (0,0,-1)
        e = msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})
        if ocs_flip:
            e.dxf.extrusion = (0, 0, -1)
    doc.saveas(path)
    return path
