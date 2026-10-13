"""The roof plan of a sheet, drawn as a view of its own, becomes the roof of the converted plan: whole conversions of
synthetic sheets (centimetres)."""

import math

import ezdxf
import pytest
from ezdxf.math import Matrix44

from dwg2c4d import Config, convert
from dwg2c4d.config import LayerRules
from helpers import Obj
from test_views import add_plan, add_title, new_sheet

OVER = 60  # cm: the eaves


def add_hip_roof(msp, w=1400, d=900, layer="0", pitch_note=None, junk=True):
    """The roof plan of ``add_plan`` (a hip roof, ``OVER`` of eaves) in the frame of the plan, with what else a roof view
    holds: the wall line under the eaves, tile lines, a panel array, a skylight, a dimension and a section line."""
    x0, y0, x1, y1 = -OVER, -OVER, w + OVER, d + OVER
    half = (y1 - y0) / 2
    ym = (y0 + y1) / 2
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": layer})
    rx0, rx1 = x0 + half, x1 - half
    msp.add_line((rx0, ym), (rx1, ym), dxfattribs={"layer": layer})
    for corner, end in (((x0, y0), (rx0, ym)), ((x0, y1), (rx0, ym)), ((x1, y0), (rx1, ym)), ((x1, y1), (rx1, ym))):
        msp.add_line(corner, end, dxfattribs={"layer": layer})
    if pitch_note:
        msp.add_text(pitch_note, height=12, dxfattribs={"layer": "TESTI", "insert": (x0 + 40, y0 - 40)})
    if not junk:
        return
    msp.add_lwpolyline([(0, 0), (w, 0), (w, d), (0, d)], close=True, dxfattribs={"layer": layer})  # the wall under the eaves
    for k in range(150):  # tiles drawn with lines
        msp.add_line((x0 + 10 + k * 9, y0 + 100), (x0 + 16 + k * 9, y0 + 130), dxfattribs={"layer": "TRATTEGGIO"})
    msp.add_lwpolyline([(300, 150), (450, 150), (450, 300), (300, 300)], close=True, dxfattribs={"layer": layer})  # a skylight
    msp.add_lwpolyline([(rx0 + 50, ym + 60), (rx0 + 350, ym + 60), (rx0 + 350, ym + 300), (rx0 + 50, ym + 300)], close=True,
                       dxfattribs={"layer": layer})  # a panel array (7 m2) in a plane of the roof
    msp.add_line((x0 - 200, y0 - 100), (x1 + 200, y0 - 100), dxfattribs={"layer": "QUOTE"})  # a dimension line
    msp.add_line((w / 2, y0 - 300), (w / 2, y1 + 300), dxfattribs={"layer": "sezioni"})  # a section line through the roof


def roof_sheet(path, turns=1, shift=(3200, 500), roof=add_hip_roof, **kw):
    """A plan 14 x 9 m and, far from it and turned ``turns`` quarters, the plan of its roof with a title."""
    doc, msp = new_sheet()
    for name in ("TESTI", "TRATTEGGIO", "QUOTE", "sezioni"):
        doc.layers.add(name)
    add_plan(msp, 0, 0)
    add_title(msp, "PIANTA PIANO TERRA", 100, -150)
    before = len(msp)
    roof(msp, **kw)
    add_title(msp, "PIANTA COPERTURA", 100, -250)
    move = Matrix44.chain(Matrix44.z_rotate(math.radians(90 * turns)), Matrix44.translate(shift[0], shift[1], 0))
    for e in list(msp)[before:]:
        e.transform(move)
    doc.saveas(path)
    return path


def converted(tmp_path, cfg=None, **kw):
    path = roof_sheet(tmp_path / "t.dxf", **kw)
    report = convert(path, tmp_path / "t.obj", cfg or Config(auto=True, roof_auto=True, images=False))
    return report, Obj(tmp_path / "t.obj")


@pytest.mark.parametrize("turns", [0, 1, 2, 3])
def test_the_roof_plan_of_the_sheet_becomes_the_roof_over_the_walls(tmp_path, turns):
    report, obj = converted(tmp_path, turns=turns)
    assert report.roof is not None and report.roof["view"] is not None and report.roof["faces"] == 4
    assert any("vista della copertura" in w for w in report.warnings)
    (wx0, _, wz0), (wx1, _, _) = obj.bbox("Muri")  # the plan is 14 x 9 m (the walls' far side is where the doors swing)
    (rx0, _, rz0), (rx1, ry1, rz1) = obj.bbox("Tetto")
    over = OVER / 100
    assert (rx0, rx1, rz0, rz1) == pytest.approx((wx0 - over, wx1 + over, wz0 - over, wz0 + 9.0 + over), abs=0.1)
    assert ry1 > report.wall_height


def test_the_pitch_written_in_the_sheet_is_the_pitch_of_the_roof(tmp_path):
    report, _ = converted(tmp_path, pitch_note="Pendenza 25 %")
    assert report.roof["pitch_source"] == "scritta" and report.roof["pitch_deg"] == pytest.approx(14.04, abs=0.05)
    assert report.roof["ridge_height"] == pytest.approx(report.wall_height + 5.1 * 0.25, abs=0.05)  # half span 5.1 m


def test_without_a_written_pitch_the_default_is_used_and_said(tmp_path):
    report, _ = converted(tmp_path)
    assert report.roof["pitch_source"] == "predefinita"
    assert any("nessuna pendenza scritta" in w for w in report.warnings)


def test_an_explicit_pitch_beats_the_written_one(tmp_path):
    cfg = Config(auto=True, roof_auto=True, images=False, roof_pitch=30.0)
    report, _ = converted(tmp_path, cfg, pitch_note="Pendenza 25 %")
    assert report.roof["pitch_source"] == "indicata" and report.roof["pitch_deg"] == pytest.approx(30.0, abs=0.05)


def test_no_roof_is_built_when_the_user_says_no(tmp_path):
    report, obj = converted(tmp_path, Config(auto=True, roof_auto=False, images=False))
    assert report.roof is None and "Tetto" not in obj.groups


def test_a_roof_layer_always_wins_over_the_roof_view(tmp_path):
    path = roof_sheet(tmp_path / "t.dxf", junk=False)
    doc = ezdxf.readfile(path)
    doc.layers.add("Tetto")
    add_hip_roof(doc.modelspace(), layer="Tetto", junk=False)  # drawn over the plan: a roof of the layer
    doc.saveas(path)
    report = convert(path, tmp_path / "t.obj", Config(auto=True, roof_auto=True, images=False))
    assert report.roof is not None and report.roof["view"] is None
    assert not any("vista della copertura" in w for w in report.warnings)


def test_a_layer_the_user_names_is_the_only_place_the_roof_is_looked_for(tmp_path):
    cfg = Config(auto=True, roof_auto=True, images=False, layers=LayerRules({"roof": ["NESSUNO"]}))
    report, obj = converted(tmp_path, cfg)
    assert report.roof is None and "Tetto" not in obj.groups


def test_a_roof_plan_that_fits_no_building_is_not_placed_and_the_report_says_so(tmp_path):
    def strip(msp, **kw):
        msp.add_lwpolyline([(0, 0), (4000, 0), (4000, 250), (0, 250)], close=True)  # 40 x 2.5 m: no building of the sheet

    report, obj = converted(tmp_path, roof=strip)
    assert report.roof is None and "Tetto" not in obj.groups
    assert any("non combacia" in w for w in report.warnings)


def add_flat_roof(msp, w=1400, d=900, layer="0", parapet=True, **kw):
    """The plan of a flat roof over ``add_plan``: the outline (``OVER`` of eaves) and, when ``parapet``, the inner face of a
    25 cm parapet; a skylight and the tile lines of nothing else: a flat roof has no ridge."""
    x0, y0, x1, y1 = -OVER, -OVER, w + OVER, d + OVER
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": layer})
    if parapet:
        msp.add_lwpolyline([(x0 + 25, y0 + 25), (x1 - 25, y0 + 25), (x1 - 25, y1 - 25), (x0 + 25, y1 - 25)], close=True,
                           dxfattribs={"layer": layer})
    msp.add_lwpolyline([(300, 150), (450, 150), (450, 300), (300, 300)], close=True, dxfattribs={"layer": layer})  # a skylight


@pytest.mark.parametrize("turns", [0, 1, 3])
def test_a_roof_drawn_with_no_ridge_becomes_a_flat_slab_on_the_walls(tmp_path, turns):
    report, obj = converted(tmp_path, roof=add_flat_roof, turns=turns, parapet=False)
    assert report.roof is None and report.flat_roof is not None and report.flat_roof["parapet_height"] is None
    assert "Copertura" in obj.groups and "Tetto" not in obj.groups
    (rx0, ry0, rz0), (rx1, ry1, rz1) = obj.bbox("Copertura")
    (wx0, _, wz0), (wx1, _, _) = obj.bbox("Muri")
    assert (ry0, ry1) == pytest.approx((report.wall_height, report.wall_height + 0.2), abs=0.01)
    assert (rx0, rx1, rz0, rz1) == pytest.approx((wx0 - OVER / 100, wx1 + OVER / 100, wz0 - OVER / 100, wz0 + 9.0 + OVER / 100), abs=0.1)
    assert any("Tetto piano" in w for w in report.warnings)


def test_the_double_line_along_the_edge_of_a_flat_roof_is_a_parapet_in_the_model(tmp_path):
    report, obj = converted(tmp_path, roof=add_flat_roof, parapet=True)
    assert report.flat_roof["parapet_height"] == pytest.approx(0.5) and report.flat_roof["parapet_source"] == "presunta"
    (_, ry0, _), (_, ry1, _) = obj.bbox("Copertura")
    assert ry1 == pytest.approx(report.wall_height + 0.2 + 0.5, abs=0.01)
    assert any("bordo rialzato" in w for w in report.warnings)


def test_a_roof_with_ridges_makes_no_flat_slab(tmp_path):
    report, obj = converted(tmp_path)
    assert report.flat_roof is None and "Copertura" not in obj.groups


def test_a_failure_in_the_roof_view_never_stops_the_conversion(tmp_path, monkeypatch):
    import dwg2c4d.pipeline as pipeline

    def boom(*args, **kwargs):
        raise ValueError("rotto")

    monkeypatch.setattr(pipeline, "roof_from_view", boom)
    report, obj = converted(tmp_path)
    assert report.roof is None and any("non costruito: ValueError" in w for w in report.warnings)
    assert "Muri" in obj.groups


def test_a_roof_asked_for_with_no_roof_layer_is_taken_from_the_roof_view_without_complaints_about_layers(tmp_path):
    report, _ = converted(tmp_path, Config(auto=True, roof=True, roof_auto=True, images=False))
    assert report.roof is not None and report.roof["view"] is not None
    assert not any("nessuna linea trovata" in w for w in report.warnings)


def add_offcentre_gable(msp, w=1400, d=900, layer="0", **kw):
    """A gable roof whose ridge is not in the middle: the outline fits the plan turned by half a turn as well, the ridge
    would not be where it is drawn."""
    x0, y0, x1, y1 = -OVER, -OVER, w + OVER, d + OVER
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": layer})
    ridge = y0 + 0.3 * (y1 - y0)
    msp.add_line((x0, ridge), (x1, ridge), dxfattribs={"layer": layer})


def test_a_roof_whose_ridge_would_change_place_if_the_outline_is_turned_by_half_a_turn_says_so(tmp_path):
    report, _ = converted(tmp_path, roof=add_offcentre_gable, turns=0)
    assert report.roof is not None
    assert any("quasi altrettanto bene" in w and "180 gradi" in w and "non ruotata" in w for w in report.warnings)


def test_a_symmetric_roof_is_not_said_to_fit_in_two_places(tmp_path):
    report, _ = converted(tmp_path)  # a hip roof: turned by half a turn it is the same roof
    assert report.roof is not None and not any("quasi altrettanto bene" in w for w in report.warnings)


def add_hip_and_flat(msp, w=1400, d=900, layer="0", **kw):
    """The roof of ``add_plan`` in two bodies 20 cm apart: a hip roof over the west half (a ridge along y) and a flat roof
    (an outline and nothing else) over the east half."""
    x0, y0, y1, mid = -OVER, -OVER, d + OVER, w // 2
    x1 = mid - 10
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": layer})
    half = (x1 - x0) / 2
    xm = (x0 + x1) / 2
    msp.add_line((xm, y0 + half), (xm, y1 - half), dxfattribs={"layer": layer})
    for corner, end in (((x0, y0), (xm, y0 + half)), ((x1, y0), (xm, y0 + half)), ((x0, y1), (xm, y1 - half)),
                        ((x1, y1), (xm, y1 - half))):
        msp.add_line(corner, end, dxfattribs={"layer": layer})
    msp.add_lwpolyline([(mid + 10, y0), (w + OVER, y0), (w + OVER, y1), (mid + 10, y1)], close=True, dxfattribs={"layer": layer})


def test_a_roof_view_with_a_hip_roof_and_a_flat_roof_makes_both(tmp_path):
    report, obj = converted(tmp_path, roof=add_hip_and_flat, turns=1)
    assert report.roof is not None and report.roof["faces"] == 4
    assert report.flat_roof is not None and report.flat_roof["bodies"] == 1
    assert "Tetto" in obj.groups and "Copertura" in obj.groups
    (_, _, _), (tx1, _, _) = obj.bbox("Tetto")
    (cx0, _, _), (_, _, _) = obj.bbox("Copertura")
    assert tx1 < cx0 + 0.5  # the hip roof is on one side of the flat one: they do not overlap


def test_a_roof_view_with_no_closed_outline_says_so_and_builds_nothing(tmp_path):
    def open_outline(msp, **kw):
        msp.add_lwpolyline([(0, 900), (0, 0), (1500, 0), (1500, 900)])  # three sides of a roof, and tiles to say what it is
        for k in range(4):
            hatch = msp.add_hatch()
            hatch.dxf.pattern_name = "COPPI01"
            hatch.dxf.solid_fill = 0
            hatch.paths.add_polyline_path([(100 + k * 300, 100), (350 + k * 300, 100), (350 + k * 300, 400), (100 + k * 300, 400)])

    report, obj = converted(tmp_path, roof=open_outline)
    assert report.roof is None and report.flat_roof is None and "Tetto" not in obj.groups
    assert any("nessun contorno chiuso" in w for w in report.warnings)


def test_the_flat_roof_is_an_object_of_the_c4d_json_with_its_own_material(tmp_path):
    import json

    converted(tmp_path, Config(auto=True, roof_auto=True, images=False, c4d_json=True), roof=add_flat_roof, parapet=False)
    model = json.loads((tmp_path / "t_model.json").read_text(encoding="utf-8"))
    (slab,) = [o for o in model["objects"] if o["name"] == "Copertura"]
    assert slab["material"] == "flat_roof" and "flat_roof" in model["materials"] and slab["group"] == "Tetto"


def add_panels(msp, columns=4, rows=4, pattern="ANSI33", size=(110, 170), layer="0", x0=300, y0=200):
    """Rows of equal hatches (photovoltaic panels: 1.1 x 1.7 m) on the roof."""
    for i in range(columns):
        for j in range(rows):
            x, y = x0 + i * (size[0] + 15), y0 + j * (size[1] + 15)
            hatch = msp.add_hatch(dxfattribs={"layer": layer})
            hatch.dxf.pattern_name = pattern
            hatch.dxf.solid_fill = 0
            hatch.paths.add_polyline_path([(x, y), (x + size[0], y), (x + size[0], y + size[1]), (x, y + size[1])])


def flat_roof_with_panels(msp, pattern="ANSI33", columns=4, rows=4, **kw):
    add_flat_roof(msp, parapet=False)
    add_panels(msp, columns=columns, rows=rows, pattern=pattern)


def test_repeated_hatches_the_size_of_a_panel_on_a_flat_roof_become_blocks_on_the_slab(tmp_path):
    report, obj = converted(tmp_path, roof=flat_roof_with_panels, turns=1)
    assert report.flat_roof["modules"] == 16 and "Pannelli" in obj.groups
    (_, y0, _), (_, y1, _) = obj.bbox("Pannelli")
    assert (y0, y1) == pytest.approx((report.wall_height + 0.2, report.wall_height + 0.2 + 0.35), abs=0.01)
    (px0, _, pz0), (px1, _, pz1) = obj.bbox("Pannelli")
    (rx0, _, rz0), (rx1, _, rz1) = obj.bbox("Copertura")
    assert rx0 < px0 and px1 < rx1 and rz0 < pz0 and pz1 < rz1  # on the slab, not beside it
    assert any("pannelli fotovoltaici" in w for w in report.warnings)


def test_a_few_hatches_are_no_panels(tmp_path):
    report, obj = converted(tmp_path, roof=flat_roof_with_panels, columns=3, rows=3)  # nine: fewer than a roof of panels
    assert report.flat_roof["modules"] == 0 and "Pannelli" not in obj.groups


def test_the_panels_of_a_pitched_roof_are_not_made(tmp_path):
    """Sixteen equal hatches of tiles lie on a roof that has them: it is a pitched roof, and no panel is made of a tile."""
    report, obj = converted(tmp_path, roof=flat_roof_with_panels, pattern="COPPI01")
    assert report.roof is not None and report.flat_roof is None and "Pannelli" not in obj.groups


def test_hatches_too_big_to_be_panels_are_not_panels(tmp_path):
    def big(msp, **kw):
        add_flat_roof(msp, parapet=False)
        add_panels(msp, columns=4, rows=3, size=(305, 305), x0=0, y0=-40)  # twelve of 3.05 x 3.05 m: bays, not panels

    report, _ = converted(tmp_path, roof=big)
    assert report.flat_roof["modules"] == 0
