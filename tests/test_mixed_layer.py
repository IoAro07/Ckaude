"""One layer for every opening ("Infissi"): doors and windows told apart by their shape."""

import pytest
from builders import D, PLAN_AREA, T, W, building

from dwg2c4d import Config, convert
from dwg2c4d.config import LayerRules


def plan(tmp_path, layer="Infissi"):
    doc, msp = building()
    for e in list(msp.query("LWPOLYLINE[layer=='FINESTRE']")) + list(msp.query("LWPOLYLINE[layer=='PORTE']")):
        msp.delete_entity(e)
    doc.layers.add(layer)
    a = {"layer": layer}
    mid = T / 2
    # a door: jambs, a leaf and the swing arc (hinge on the left)
    msp.add_line((200, 0), (200, T), dxfattribs=a)
    msp.add_line((290, 0), (290, T), dxfattribs=a)
    msp.add_arc((200, mid), 90, 0, 90, dxfattribs=a)
    msp.add_line((200, mid), (200, mid + 90), dxfattribs=a)
    # a window: three lines along the wall and the jambs, no arc
    for off in (0, mid, T):
        msp.add_line((500, off), (620, off), dxfattribs=a)
    msp.add_line((500, 0), (500, T), dxfattribs=a)
    msp.add_line((620, 0), (620, T), dxfattribs=a)
    path = tmp_path / "m.dxf"
    doc.saveas(path)
    return path


def run(path, tmp_path, **kw):
    return convert(path, tmp_path / "o.obj", Config(area=PLAN_AREA, **kw))


def test_the_layer_is_a_window_layer_by_name():
    assert LayerRules().classify_layer("Infissi") == "window" and LayerRules().is_mixed_openings("Infissi")
    assert LayerRules().is_mixed_openings("12 Serramenti") and not LayerRules().is_mixed_openings("Finestre")


def test_a_symbol_with_a_swing_arc_is_a_door_and_one_without_is_a_window(tmp_path):
    rep = run(plan(tmp_path), tmp_path)
    kinds = {o.id: o.kind for o in rep.openings}
    assert kinds == {"P01": "door", "F01": "window"}
    door = next(o for o in rep.openings if o.kind == "door")
    assert door.src["kind"] == "arco di rotazione" and door.leaves[0]["hinge"] == -1
    assert any("riconosciuta dalla forma" in n for n in door.notes)
    win = next(o for o in rep.openings if o.kind == "window")
    assert win.src["kind"] == "nessun arco di rotazione" and (win.z0, win.z1) == (0.9, pytest.approx(2.2))


def test_the_door_gets_door_height_and_a_leaf(tmp_path):
    rep = run(plan(tmp_path), tmp_path, fixtures="detailed")
    door = next(o for o in rep.openings if o.kind == "door")
    assert (door.z0, door.z1) == (0.0, pytest.approx(2.1))
    assert door.src["leaves"] == "arco"


def test_the_table_says_how_the_kind_was_decided(tmp_path):
    from dwg2c4d.table import read_table

    rows = read_table(run(plan(tmp_path), tmp_path).table_path)
    assert any("kind: arco di rotazione" in r["origine_misure"] for r in rows)


def test_a_door_layer_is_not_second_guessed(tmp_path):
    """A layer named PORTE or FINESTRE keeps its meaning, arc or not."""
    doc, msp = building()
    doc.layers.add("FINESTRE2")
    msp.add_arc((520, T / 2), 60, 0, 90, dxfattribs={"layer": "FINESTRE"})  # a window sash swing
    doc.saveas(tmp_path / "w.dxf")
    rep = run(tmp_path / "w.dxf", tmp_path)
    assert all(o.src.get("kind") is None for o in rep.openings)
    assert sum(o.kind == "window" for o in rep.openings) == 2 and W == 1000 and D == 600


def test_an_explicit_window_layer_override_is_respected(tmp_path):
    cfg = Config(area=PLAN_AREA)
    cfg.layers = LayerRules({"window": ["Infissi"]})
    rep = convert(plan(tmp_path), tmp_path / "o.obj", cfg)
    assert {o.kind for o in rep.openings} == {"window"}
