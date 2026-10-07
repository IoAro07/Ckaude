"""Layers the name does not explain: proposals with confidence, applied only on request."""

import ezdxf
import pytest
from builders import PLAN_AREA, T, W, D

from dwg2c4d import Config, convert
from dwg2c4d.cli import main
from dwg2c4d.proposals import apply_proposals, propose_layers
from dwg2c4d.reader import layer_summary


def odd_plan(tmp_path, wall_layer="XX-7", door_layer="YY-3"):
    """A building whose layers are called nothing a program could know: walls, a door with its swing arc,
    plus layers whose names (or content) say what they are."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    for name in (wall_layer, door_layer, "arredo", "ZZ-text", "ZZ-hatch", "Quote"):
        doc.layers.add(name)
    msp = doc.modelspace()
    for pts in ([(0, 0), (W, 0), (W, D), (0, D)], [(T, T), (W - T, T), (W - T, D - T), (T, D - T)]):
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": wall_layer})
    msp.add_lwpolyline([(500, T - 1), (510, T - 1), (510, D - T + 1), (500, D - T + 1)], close=True,
                       dxfattribs={"layer": wall_layer})
    a = {"layer": door_layer}
    mid = T / 2
    msp.add_line((200, 0), (200, T), dxfattribs=a)
    msp.add_line((290, 0), (290, T), dxfattribs=a)
    msp.add_arc((200, mid), 90, 0, 90, dxfattribs=a)
    msp.add_line((200, mid), (200, mid + 90), dxfattribs=a)
    msp.add_line((700, 0), (700, T), dxfattribs=a)
    msp.add_line((790, 0), (790, T), dxfattribs=a)
    msp.add_arc((700, mid), 90, 0, 90, dxfattribs=a)
    msp.add_line((700, mid), (700, mid + 90), dxfattribs=a)
    msp.add_circle((400, 300), 30, dxfattribs={"layer": "arredo"})  # a table
    msp.add_mtext("SALA", dxfattribs={"insert": (250, 300), "layer": "ZZ-text", "char_height": 10})
    path = tmp_path / "odd.dxf"
    doc.saveas(path)
    return path


def proposals(path):
    doc = ezdxf.readfile(path)
    cfg = Config(area=PLAN_AREA)
    rows = layer_summary(doc, cfg)
    return {p.layer: p for p in propose_layers(doc, cfg, rows)}, rows


def test_line_work_that_encloses_rooms_is_proposed_as_walls(tmp_path):
    found, _ = proposals(odd_plan(tmp_path))
    wall = found["XX-7"]
    assert wall.category == "wall" and wall.confidence == "alta" and "2 locali" in wall.reason


def test_symbols_with_swing_arcs_on_the_walls_are_proposed_as_doors(tmp_path):
    found, _ = proposals(odd_plan(tmp_path))
    door = found["YY-3"]
    assert door.category == "door" and door.confidence == "alta" and "2 con arco" in door.reason


def test_names_and_content_tell_the_other_layers(tmp_path):
    found, _ = proposals(odd_plan(tmp_path))
    assert found["arredo"].label.startswith("arredi") and found["arredo"].category is None
    assert found["ZZ-text"].label == "quote e testi"
    assert "ZZ-hatch" not in found  # empty layers are not in the listing at all


def test_nothing_is_applied_unless_asked(tmp_path):
    path = odd_plan(tmp_path)
    with pytest.raises(Exception):  # no layer the name explains: no walls
        convert(path, tmp_path / "o.obj", Config(area=PLAN_AREA))


def test_accepting_the_proposals_converts_the_drawing(tmp_path, capsys):
    path = odd_plan(tmp_path)
    assert main([str(path), "-o", str(tmp_path / "a.obj"), "--area=" + ",".join(map(str, PLAN_AREA)),
                 "--accetta-proposte", "--no-immagini"]) == 0
    out = capsys.readouterr().out
    assert "Layer 'XX-7' usato come muri" in out and "Layer 'YY-3' usato come porte" in out
    assert "Porte / finestre   : 2 / 0" in out


def test_the_listing_shows_proposals_and_how_to_use_them(tmp_path, capsys):
    path = odd_plan(tmp_path)
    assert main([str(path), "--elenca-layer", "--area=" + ",".join(map(str, PLAN_AREA))]) == 0
    out = capsys.readouterr().out
    assert "PROPOSTE" in out and "XX-7" in out and "--accetta-proposte" in out


def test_a_proposal_never_replaces_what_the_name_or_the_user_decided(tmp_path):
    path = odd_plan(tmp_path, wall_layer="MURI")  # recognised by name: not a proposal
    found, rows = proposals(path)
    assert "MURI" not in found
    cfg = Config(area=PLAN_AREA)
    taken = apply_proposals(cfg, list(found.values()), rows)
    assert all(p.category != "wall" for p in taken)  # walls are already known
    cfg2 = Config(area=PLAN_AREA)
    cfg2.layers.overrides["door"] = ["MY-DOORS"]
    assert all(p.category != "door" for p in apply_proposals(cfg2, list(found.values()), rows))


def test_a_second_wall_candidate_is_only_a_guess(tmp_path):
    doc = ezdxf.readfile(odd_plan(tmp_path))
    doc.layers.add("OUTER-9")
    msp = doc.modelspace()
    for pts in ([(1200, 0), (1500, 0), (1500, 100), (1200, 100)], [(1210, 10), (1490, 10), (1490, 90), (1210, 90)]):
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "OUTER-9"})
    doc.saveas(tmp_path / "two.dxf")
    found, _ = proposals(tmp_path / "two.dxf")
    assert found["XX-7"].confidence == "alta" and found["OUTER-9"].confidence == "bassa"
    assert "il candidato migliore e' 'XX-7'" in found["OUTER-9"].reason
