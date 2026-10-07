"""Layers named by storey (P1_Muri, pianta2...): one storey at a time."""

import ezdxf
import pytest
from builders import PLAN_AREA, T

from dwg2c4d import Config, convert
from dwg2c4d.cli import main
from dwg2c4d.config import floor_of
from dwg2c4d.dwgfile import ConversionError
from helpers import Obj


def two_storeys(tmp_path, names=("P1_Muri", "P2_Muri"), shared_door=True):
    """Storey 1: 10 x 6 m; storey 2: 6 x 4 m, drawn on top of each other with different layers."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    for n in (*names, "PORTE"):
        doc.layers.add(n)
    msp = doc.modelspace()
    for layer, (w, d) in zip(names, ((1000, 600), (600, 400))):
        for pts in ([(0, 0), (w, 0), (w, d), (0, d)], [(T, T), (w - T, T), (w - T, d - T), (T, d - T)]):
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})
    if shared_door:
        msp.add_lwpolyline([(200, 0), (290, 0), (290, T), (200, T)], close=True, dxfattribs={"layer": "PORTE"})
    path = tmp_path / "s.dxf"
    doc.saveas(path)
    return path


def run(path, tmp_path, **kw):
    return convert(path, tmp_path / "o.obj", Config(area=PLAN_AREA, **kw))


@pytest.mark.parametrize("name,floor", [("P1_Muri", 1), ("pianta2", 2), ("Piano 3 muri", 3), ("Floor_1-walls", 1),
                                        ("12 P2 Infissi", 2), ("PT_muri", None), ("PORTE", None), ("Prospetto1", None),
                                        ("P10_x", 10), ("pippo2", None)])
def test_the_storey_comes_from_the_layer_name(name, floor):
    assert floor_of(name) == floor


def test_the_lowest_storey_is_read_by_default_and_the_others_are_announced(tmp_path):
    rep = run(two_storeys(tmp_path), tmp_path)
    assert rep.size_m == pytest.approx((10.0, 6.0))
    assert any("2 piani (P1, P2)" in w and "piano 1" in w for w in rep.warnings)


def test_piano_chooses_the_storey(tmp_path):
    rep = run(two_storeys(tmp_path), tmp_path, floor=2)
    assert rep.size_m == pytest.approx((6.0, 4.0)) and not any("piani" in w for w in rep.warnings)
    assert Obj(rep.output).bbox("Muri")[1][0] == pytest.approx(6.0)


def test_layers_without_a_number_belong_to_every_storey(tmp_path):
    path = two_storeys(tmp_path)
    assert run(path, tmp_path, floor=1).doors == 1 and run(path, tmp_path, floor=2).doors == 1


def test_a_missing_storey_is_a_clear_error(tmp_path):
    with pytest.raises(ConversionError, match="Il piano 5 non esiste.*1, 2"):
        run(two_storeys(tmp_path), tmp_path, floor=5)


def test_a_drawing_without_storey_names_is_untouched(tmp_path):
    from builders import building

    doc, _ = building()
    doc.saveas(tmp_path / "n.dxf")
    rep = run(tmp_path / "n.dxf", tmp_path, floor=3)  # nothing to choose: ignored
    assert rep.size_m == pytest.approx((10.0, 6.0))


def test_command_line_names_the_output_after_the_storey_and_lists_storeys(tmp_path, capsys):
    path = two_storeys(tmp_path)
    area = "--area=" + ",".join(map(str, PLAN_AREA))
    assert main([str(path), area, "--piano", "2", "--no-immagini"]) == 0
    assert (tmp_path / "s_p2.obj").exists()
    assert main([str(path), "--elenca-layer"]) == 0
    assert "Piani riconosciuti dai nomi dei layer: P1, P2" in capsys.readouterr().out
