import math

import ezdxf
import pytest
from shapely.ops import unary_union

from dwg2c4d import Config, convert
from dwg2c4d.dwgfile import ConversionError
from dwg2c4d.sample import DOORS, THICK, WALL_RECTS, WINDOWS, build_sample

from conftest import make_square_plan
from helpers import Obj

H = 2.70


def expected_wall_volume(with_openings=True):
    """Wall volume of the sample plan, computed from its design constants (m^3)."""
    area = unary_union(WALL_RECTS).area / 1e4  # cm^2 -> m^2
    vol = area * H
    if with_openings:
        for _n, _x, _y, d, w in DOORS:
            t = THICK if d == "h" and w == 90 else 10
            vol -= (w / 100) * (t / 100) * 2.10
        for _x, _y, _d, w in WINDOWS:
            vol -= (w / 100) * (THICK / 100) * 1.30
    return vol


# --- the three drawing styles must give the same building ------------------------------

@pytest.mark.parametrize("style", ["lines", "polylines", "hatch"])
def test_wall_styles_give_same_volume(tmp_path, style):
    dxf = build_sample(tmp_path / f"{style}.dxf", style)
    rep = convert(dxf, tmp_path / "out.obj")
    obj = Obj(rep.output)
    assert rep.doors == 2 and rep.windows == 2
    assert rep.size_m == pytest.approx((8.0, 6.0), abs=0.01)
    assert obj.volume("Muri") == pytest.approx(expected_wall_volume(), rel=0.01)


def test_centerline_style_builds_walls_of_given_thickness(tmp_path):
    dxf = build_sample(tmp_path / "c.dxf", "centerline")
    rep = convert(dxf, tmp_path / "out.obj", Config(wall_mode="centerline", wall_thickness=0.30,
                                                    glass=False))
    # perimeter loop 7.7 x 5.7 -> ring 8.0x6.0 minus 7.4x5.4 = 8.04, plus the two interior walls
    assert rep.size_m == pytest.approx((8.0, 6.0), abs=0.01)
    assert rep.wall_area_m2 > 8.0
    assert rep.doors == 2 and rep.windows == 2


def test_auto_mode_treats_fat_closed_loop_as_centerline_with_warning(tmp_path):
    dxf = build_sample(tmp_path / "c.dxf", "centerline")
    rep = convert(dxf, tmp_path / "out.obj")
    assert any("assi" in w for w in rep.warnings)
    assert rep.size_m == pytest.approx((8.0, 6.0), abs=0.01)


# --- openings ----------------------------------------------------------------------------

def test_wall_is_rebuilt_above_doors_and_around_windows(tmp_path):
    """The sample has gaps in the wall lines at every opening; the lintel / parapet must exist."""
    rep = convert(build_sample(tmp_path / "a.dxf", "lines"), tmp_path / "out.obj")
    obj = Obj(rep.output)
    with_openings = obj.volume("Muri")

    rep2 = convert(build_sample(tmp_path / "b.dxf", "lines", with_openings=False), tmp_path / "out2.obj")
    closed = Obj(rep2.output).volume("Muri")
    assert closed == pytest.approx(expected_wall_volume(with_openings=False), rel=0.01)
    removed = closed - with_openings
    expect_removed = expected_wall_volume(False) - expected_wall_volume(True)
    assert removed == pytest.approx(expect_removed, rel=0.03)


def test_window_glass_and_door_heights(tmp_path):
    rep = convert(build_sample(tmp_path / "a.dxf", "lines"), tmp_path / "out.obj")
    obj = Obj(rep.output)
    # glass: two panes, 1.2 m x 0.02 m, between the sill (0.9) and the head (2.2)
    assert obj.volume("Vetri") == pytest.approx(2 * 1.2 * 0.02 * 1.3, rel=0.02)
    lo, hi = obj.bbox("Vetri")
    assert lo[1] == pytest.approx(0.9, abs=1e-6) and hi[1] == pytest.approx(2.2, abs=1e-6)


def test_custom_heights(tmp_path):
    cfg = Config(wall_height=3.0, door_height=2.2, window_sill=1.0, window_height=1.0)
    rep = convert(build_sample(tmp_path / "a.dxf", "lines"), tmp_path / "out.obj", cfg)
    obj = Obj(rep.output)
    assert obj.bbox("Muri")[1][1] == pytest.approx(3.0)
    lo, hi = obj.bbox("Vetri")
    assert (lo[1], hi[1]) == (pytest.approx(1.0), pytest.approx(2.0))


def test_no_glass_option(tmp_path):
    rep = convert(build_sample(tmp_path / "a.dxf", "lines"), tmp_path / "o.obj", Config(glass=False))
    assert "Vetri" not in rep.groups


def test_opening_symbol_not_on_a_wall_is_reported(tmp_path):
    path = make_square_plan(tmp_path / "p.dxf")
    doc = ezdxf.readfile(path)
    doc.layers.add("PORTE")
    doc.modelspace().add_line((1000, 1000), (1100, 1000), dxfattribs={"layer": "PORTE"})
    doc.saveas(path)
    rep = convert(path, tmp_path / "o.obj")
    assert rep.doors == 0
    assert any("non toccano nessun muro" in w for w in rep.warnings)


# --- floor / ceiling ---------------------------------------------------------------------

def test_floor_slab_covers_the_building_and_is_watertight(tmp_path):
    rep = convert(build_sample(tmp_path / "a.dxf", "lines"), tmp_path / "o.obj", Config(floors_by_room=False))
    obj = Obj(rep.output)
    assert obj.volume("Pavimento") == pytest.approx(8.0 * 6.0 * 0.20, rel=0.001)
    lo, hi = obj.bbox("Pavimento")
    assert (lo[1], hi[1]) == (pytest.approx(-0.2), pytest.approx(0.0))
    assert obj.open_edges("Pavimento") == 0


def test_floor_options(tmp_path):
    dxf = build_sample(tmp_path / "a.dxf", "lines")
    assert "Pavimento" not in convert(dxf, tmp_path / "a.obj", Config(floor_thickness=0)).groups
    rep = convert(dxf, tmp_path / "b.obj", Config(ceiling=True, ceiling_thickness=0.1))
    obj = Obj(rep.output)
    lo, hi = obj.bbox("Soffitto")
    assert (lo[1], hi[1]) == (pytest.approx(2.7), pytest.approx(2.8))


# --- mesh quality ------------------------------------------------------------------------

def test_closed_room_walls_are_watertight_above_the_floor(tmp_path):
    dxf = build_sample(tmp_path / "a.dxf", "polylines", with_openings=False)
    obj = Obj(convert(dxf, tmp_path / "o.obj").output)
    assert obj.open_edges("Muri", above=0.0) == 0  # only the bottom rim (y=0) is open
    assert obj.non_manifold_edges("Muri") == 0


def test_glass_panes_are_closed_boxes(tmp_path):
    obj = Obj(convert(build_sample(tmp_path / "a.dxf", "lines"), tmp_path / "o.obj").output)
    assert obj.open_edges("Vetri") == 0


@pytest.mark.parametrize("mirror", [False, True])
def test_normals_agree_with_winding_and_point_outwards(tmp_path, mirror):
    cfg = Config(mirror=mirror, ceiling=True, floors_by_room=False)
    obj = Obj(convert(build_sample(tmp_path / "a.dxf", "lines"), tmp_path / "o.obj", cfg).output)
    assert obj.winding_matches_normals()
    # mirroring is a reflection; the writer reverses the winding to compensate, so the
    # signed volume stays positive (= normals outwards) either way
    for g in ("Muri", "Pavimento", "Vetri", "Soffitto"):
        assert obj.volume(g) > 0


# --- axes and units ----------------------------------------------------------------------

def test_obj_is_y_up_and_keeps_plan_orientation(tmp_path):
    obj = Obj(convert(build_sample(tmp_path / "a.dxf", "lines"), tmp_path / "o.obj").output)
    lo, hi = obj.bbox("Muri")
    # CAD (x, y, z) -> OBJ (x, z, -y): the plan occupies x in [0, 8] and z in [-6, 0]
    assert lo == pytest.approx([0.0, 0.0, -6.0], abs=1e-4)
    assert hi == pytest.approx([8.0, 2.7, 0.0], abs=1e-4)


def test_entrance_stays_near_the_left_of_the_bottom_wall(tmp_path):
    """Asymmetry check: a wrongly mirrored plan would put the entrance on the right."""
    obj = Obj(convert(build_sample(tmp_path / "a.dxf", "lines"), tmp_path / "o.obj").output)
    # Lintel soffit above the entrance: horizontal faces at y = 2.1, x in [1.0, 1.9], z in [-0.3, 0]
    soffit = [ids for ids, _ in obj.groups["Muri"]
              if all(abs(obj.v[i][1] - 2.1) < 1e-6 for i in ids)
              and all(-0.31 < obj.v[i][2] < 0.01 for i in ids)]
    assert soffit
    xs = [obj.v[i][0] for ids in soffit for i in ids]
    assert min(xs) == pytest.approx(1.0, abs=0.02) and max(xs) == pytest.approx(1.9, abs=0.02)


def test_mirror_flag_flips_z(tmp_path):
    dxf = build_sample(tmp_path / "a.dxf", "lines")
    lo, hi = Obj(convert(dxf, tmp_path / "o.obj", Config(mirror=True)).output).bbox("Muri")
    assert (lo[2], hi[2]) == (pytest.approx(0.0, abs=1e-4), pytest.approx(6.0, abs=1e-4))


@pytest.mark.parametrize("out,factor", [("cm", 100), ("mm", 1000)])
def test_output_units(tmp_path, out, factor):
    dxf = build_sample(tmp_path / "a.dxf", "lines")
    obj = Obj(convert(dxf, tmp_path / "o.obj", Config(out_units=out)).output)
    assert obj.bbox("Muri")[1] == pytest.approx([8.0 * factor, 2.7 * factor, 0.0], abs=1e-3 * factor)


@pytest.mark.parametrize("tag,side_units,expected_m", [
    (4, 5000, 5.0),   # mm
    (5, 500, 5.0),    # cm
    (6, 5, 5.0),      # m
    (1, 197.0, 5.0),  # inches
])
def test_units_read_from_file(tmp_path, tag, side_units, expected_m):
    dxf = make_square_plan(tmp_path / "p.dxf", side=side_units, thick=side_units * 0.06, units=tag)
    rep = convert(dxf, tmp_path / "o.obj")
    assert not rep.unit_guessed
    assert rep.size_m[0] == pytest.approx(expected_m, rel=0.01)


@pytest.mark.parametrize("side_units,expected_unit", [(12.0, "m"), (600.0, "cm"), (20000.0, "mm")])
def test_units_guessed_when_missing_and_warned(tmp_path, side_units, expected_unit):
    dxf = make_square_plan(tmp_path / "p.dxf", side=side_units, thick=side_units * 0.05, units=None)
    rep = convert(dxf, tmp_path / "o.obj")
    assert rep.unit_guessed and rep.unit == expected_unit
    assert any("non dichiara le unita'" in w for w in rep.warnings)


def test_forced_units_override_file_tag(tmp_path):
    dxf = make_square_plan(tmp_path / "p.dxf", side=500, thick=30, units=4)  # tagged mm, really cm
    rep = convert(dxf, tmp_path / "o.obj", Config(units="cm"))
    assert rep.size_m[0] == pytest.approx(5.0, rel=0.01)


# --- other geometry ----------------------------------------------------------------------

def test_nested_closed_polylines_make_a_ring_not_a_block(tmp_path):
    rep = convert(make_square_plan(tmp_path / "p.dxf", side=500, thick=30), tmp_path / "o.obj")
    assert rep.wall_area_m2 == pytest.approx(5.0 ** 2 - 4.4 ** 2)
    assert rep.wall_pieces == 1


def test_mirrored_ocs_polylines_land_in_the_right_place(tmp_path):
    # Entities with extrusion (0,0,-1): the OCS x axis points the opposite way.
    dxf = make_square_plan(tmp_path / "p.dxf", side=500, thick=30, ocs_flip=True)
    obj = Obj(convert(dxf, tmp_path / "o.obj").output)
    lo, hi = obj.bbox("Muri")
    assert (lo[0], hi[0]) == (pytest.approx(0.0, abs=1e-4), pytest.approx(5.0, abs=1e-4))


def test_columns_from_circles_and_rectangles(tmp_path):
    path = make_square_plan(tmp_path / "p.dxf")
    doc = ezdxf.readfile(path)
    doc.layers.add("PILASTRI")
    msp = doc.modelspace()
    msp.add_circle((250, 250), 20, dxfattribs={"layer": "PILASTRI"})
    msp.add_lwpolyline([(100, 100), (140, 100), (140, 140), (100, 140)], close=True,
                       dxfattribs={"layer": "PILASTRI"})
    doc.saveas(path)
    rep = convert(path, tmp_path / "o.obj")
    obj = Obj(rep.output)
    assert rep.columns == 2
    expected = (math.pi * 0.2 ** 2 + 0.4 * 0.4) * H
    assert obj.volume("Pilastri") == pytest.approx(expected, rel=0.01)


def test_hidden_layers_are_skipped_unless_requested(tmp_path):
    path = make_square_plan(tmp_path / "p.dxf")
    doc = ezdxf.readfile(path)
    doc.layers.add("MURI_EXTRA").off()
    doc.modelspace().add_lwpolyline([(1000, 0), (1500, 0), (1500, 30), (1000, 30)], close=True,
                                    dxfattribs={"layer": "MURI_EXTRA"})
    doc.saveas(path)
    assert convert(path, tmp_path / "a.obj").size_m[0] == pytest.approx(5.0, rel=0.01)
    assert convert(path, tmp_path / "b.obj", Config(include_hidden=True)).size_m[0] == \
        pytest.approx(15.0, rel=0.01)


def test_crop_area_keeps_only_one_of_two_buildings(tmp_path):
    path = make_square_plan(tmp_path / "p.dxf")
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    for pts in ([(2000, 0), (2300, 0), (2300, 300), (2000, 300)],
                [(2030, 30), (2270, 30), (2270, 270), (2030, 270)]):
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "MURI"})
    doc.saveas(path)
    assert convert(path, tmp_path / "all.obj").size_m[0] == pytest.approx(23.0, rel=0.01)
    cropped = convert(path, tmp_path / "one.obj", Config(area=(1900, -100, 2400, 400)))
    assert cropped.size_m == pytest.approx((3.0, 3.0), abs=0.01)


def test_plan_inserted_as_a_block_is_still_read(tmp_path):
    """A drawing whose content is one big block on layer 0."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    doc.layers.add("MURI")
    blk = doc.blocks.new("PIANTA")
    blk.add_lwpolyline([(0, 0), (400, 0), (400, 300), (0, 300)], close=True, dxfattribs={"layer": "MURI"})
    blk.add_lwpolyline([(30, 30), (370, 30), (370, 270), (30, 270)], close=True, dxfattribs={"layer": "MURI"})
    doc.modelspace().add_blockref("PIANTA", (1000, 1000), dxfattribs={"layer": "0"})
    path = tmp_path / "p.dxf"
    doc.saveas(path)
    rep = convert(path, tmp_path / "o.obj")
    assert rep.size_m == pytest.approx((4.0, 3.0), abs=0.01)


def test_bulged_polyline_walls_follow_the_curve(tmp_path):
    """A wall ring with rounded ends (bulges) must be flattened, not cut straight."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    doc.layers.add("MURI")
    msp = doc.modelspace()
    # slot shape: two straight sides, two half-circle ends of radius 100 (bulge 1)
    msp.add_lwpolyline([(0, 0, 0, 0, 0), (300, 0, 0, 0, 1), (300, 200, 0, 0, 0), (0, 200, 0, 0, 1)],
                       format="xyseb", close=True, dxfattribs={"layer": "MURI"})
    msp.add_lwpolyline([(0, 30, 0, 0, 0), (300, 30, 0, 0, 1), (300, 170, 0, 0, 0), (0, 170, 0, 0, 1)],
                       format="xyseb", close=True, dxfattribs={"layer": "MURI"})
    doc.saveas(tmp_path / "p.dxf")
    rep = convert(tmp_path / "p.dxf", tmp_path / "o.obj", Config(max_wall_thickness=0.6))
    # the bulge on the 200 cm chord bulges outwards by 100 cm on both ends
    assert rep.size_m[0] == pytest.approx(5.0, abs=0.02)
    assert rep.size_m[1] == pytest.approx(2.0, abs=0.02)


def test_no_walls_gives_a_helpful_error(tmp_path):
    doc = ezdxf.new("R2018")
    doc.layers.add("QUOTE")
    doc.modelspace().add_line((0, 0), (1, 1), dxfattribs={"layer": "QUOTE"})
    doc.saveas(tmp_path / "p.dxf")
    with pytest.raises(ConversionError, match="--elenca-layer"):
        convert(tmp_path / "p.dxf", tmp_path / "o.obj")


def test_user_layer_override_picks_non_standard_names(tmp_path):
    path = make_square_plan(tmp_path / "p.dxf", layer="XYZ-7")
    with pytest.raises(ConversionError):
        convert(path, tmp_path / "a.obj")
    cfg = Config()
    from dwg2c4d.config import LayerRules
    cfg.layers = LayerRules({"wall": ["xyz-*"]})
    assert convert(path, tmp_path / "b.obj", cfg).size_m[0] == pytest.approx(5.0, rel=0.01)


# --- robustness: things real drawings do -------------------------------------------------

def test_double_line_walls_with_drawn_jambs(tmp_path):
    rep = convert(build_sample(tmp_path / "j.dxf", "jambs"), tmp_path / "o.obj")
    assert rep.doors == 2 and rep.windows == 2
    assert Obj(rep.output).volume("Muri") == pytest.approx(expected_wall_volume(), rel=0.01)


@pytest.mark.parametrize("style", ["lines", "polylines"])
def test_result_does_not_depend_on_plan_rotation(tmp_path, style):
    from ezdxf.math import Matrix44

    one_slab = Config(floors_by_room=False)
    ref = Obj(convert(build_sample(tmp_path / "a.dxf", style), tmp_path / "a.obj", one_slab).output)
    doc = ezdxf.readfile(build_sample(tmp_path / "b.dxf", style))
    m = Matrix44.z_rotate(math.radians(30))
    for e in doc.modelspace():
        e.transform(m)
    doc.saveas(tmp_path / "rot.dxf")
    rep = convert(tmp_path / "rot.dxf", tmp_path / "rot.obj", one_slab)
    assert rep.doors == 2 and rep.windows == 2
    for g in ("Muri", "Vetri", "Pavimento"):
        assert Obj(rep.output).volume(g) == pytest.approx(ref.volume(g), rel=0.005)


def test_lines_that_stop_short_of_each_other_are_still_joined(tmp_path):
    """Undershoot of a few mm at corners and T-junctions (very common in CAD files)."""
    doc = ezdxf.readfile(build_sample(tmp_path / "u.dxf", "lines"))
    for e in doc.modelspace().query("LINE[layer=='MURI']"):
        s, t = e.dxf.start, e.dxf.end
        length = (t - s).magnitude
        if length > 2:
            u = (t - s) / length
            e.dxf.start, e.dxf.end = s + u * 0.5, t - u * 0.5  # 5 mm each end
    doc.saveas(tmp_path / "under.dxf")
    rep = convert(tmp_path / "under.dxf", tmp_path / "o.obj")
    assert not rep.warnings
    assert Obj(rep.output).volume("Muri") == pytest.approx(expected_wall_volume(), rel=0.01)


def test_curved_double_line_wall(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    doc.layers.add("MURI")
    msp = doc.modelspace()
    for r in (300, 330):
        msp.add_arc((0, 0), r, 0, 180, dxfattribs={"layer": "MURI"})
    msp.add_line((300, 0), (330, 0), dxfattribs={"layer": "MURI"})
    msp.add_line((-330, 0), (-300, 0), dxfattribs={"layer": "MURI"})
    doc.saveas(tmp_path / "arc.dxf")
    rep = convert(tmp_path / "arc.dxf", tmp_path / "o.obj")
    assert rep.wall_area_m2 == pytest.approx(math.pi * (3.3 ** 2 - 3.0 ** 2) / 2, rel=0.005)
    assert not any("assi" in w for w in rep.warnings)


def test_large_plan_converts_quickly(tmp_path):
    """12 x 12 rooms, ~1000 entities and 130 doors: must stay interactive."""
    import time

    from shapely.geometry import box

    n, side, t = 12, 400, 20
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    for name in ("MURI", "PORTE"):
        doc.layers.add(name)
    blk = doc.blocks.new("PORTA90")
    blk.add_line((0, 0), (0, 90))
    blk.add_arc((0, 0), 90, 0, 90)
    rects = []
    for i in range(n + 1):
        rects.append(box(i * side - t / 2, -t / 2, i * side + t / 2, n * side + t / 2))
        rects.append(box(-t / 2, i * side - t / 2, n * side + t / 2, i * side + t / 2))
    cuts = unary_union([box(j * side + 100, i * side - t / 2, j * side + 190, i * side + t / 2)
                        for i in range(1, n) for j in range(n)])
    msp = doc.modelspace()
    for ls in unary_union(rects).boundary.difference(cuts).geoms:
        c = list(ls.coords)
        for a, b in zip(c, c[1:]):
            msp.add_line(a, b, dxfattribs={"layer": "MURI"})
    for i in range(1, n):
        for j in range(n):
            msp.add_blockref("PORTA90", (j * side + 100, i * side), dxfattribs={"layer": "PORTE"})
    doc.saveas(tmp_path / "big.dxf")
    start = time.time()
    rep = convert(tmp_path / "big.dxf", tmp_path / "o.obj")
    assert time.time() - start < 20
    assert rep.doors == (n - 1) * n and not rep.warnings
    assert rep.wall_pieces == 1


@pytest.mark.parametrize("style", ["lines", "polylines", "hatch"])
def test_continuous_walls_with_symbols_drawn_on_top(tmp_path, style):
    """The other way to draw a plan: no gap in the wall lines, the door/window symbol
    just sits over the wall. The wall must be cut, giving the same result as with gaps."""
    dxf = build_sample(tmp_path / "c.dxf", style, gaps=False)
    rep = convert(dxf, tmp_path / "o.obj")
    assert rep.doors == 2 and rep.windows == 2
    assert Obj(rep.output).volume("Muri") == pytest.approx(expected_wall_volume(), rel=0.01)


# --- regressions found on a real DWG -------------------------------------------------------

def _square_with_block_on_wall_layer(tmp_path):
    path = make_square_plan(tmp_path / "p.dxf")
    doc = ezdxf.readfile(path)
    blk = doc.blocks.new("Divano")
    for a, b in (((0, 0), (200, 0)), ((200, 0), (200, 90)), ((200, 90), (0, 90)), ((0, 90), (0, 0))):
        blk.add_line(a, b, dxfattribs={"layer": "0"})
    # furniture inserted on the wall layer (very common, also on layer 0)
    doc.modelspace().add_blockref("Divano", (150, 150), dxfattribs={"layer": "MURI"})
    doc.saveas(path)
    return path


def test_blocks_on_a_wall_layer_are_ignored_as_furniture(tmp_path):
    rep = convert(_square_with_block_on_wall_layer(tmp_path), tmp_path / "o.obj")
    assert rep.wall_area_m2 == pytest.approx(5.0 ** 2 - 4.4 ** 2)
    assert any("blocchi inseriti su un layer di muri" in w for w in rep.warnings)


def test_blocks_on_a_wall_layer_can_be_read_as_walls(tmp_path):
    path = _square_with_block_on_wall_layer(tmp_path)
    rep = convert(path, tmp_path / "o.obj", Config(walls_from_blocks=True))
    assert rep.wall_area_m2 != pytest.approx(5.0 ** 2 - 4.4 ** 2)
    assert not any("blocchi inseriti" in w for w in rep.warnings)


def test_layer_zero_override_does_not_pull_in_furniture_blocks(tmp_path):
    """Walls drawn on layer 0 together with furniture blocks (the real file's situation)."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    msp = doc.modelspace()
    for pts in ([(0, 0), (500, 0), (500, 500), (0, 500)], [(20, 20), (480, 20), (480, 480), (20, 480)]):
        for a, b in zip(pts, pts[1:] + pts[:1]):
            msp.add_line(a, b)  # layer 0
    blk = doc.blocks.new("Tavolo")
    blk.add_circle((0, 0), 60)  # closed shape on layer 0 inside the block
    msp.add_blockref("Tavolo", (250, 250))
    doc.saveas(tmp_path / "p.dxf")
    from dwg2c4d.config import LayerRules
    cfg = Config()
    cfg.layers = LayerRules({"wall": ["0"]})
    rep = convert(tmp_path / "p.dxf", tmp_path / "o.obj", cfg)
    assert rep.wall_area_m2 == pytest.approx(5.0 ** 2 - 4.6 ** 2, rel=0.01)
    assert rep.wall_pieces == 1


def test_block_reference_without_definition_does_not_abort(tmp_path):
    """Some DWG->DXF converters leave anonymous blocks (*U, *X) undefined."""
    doc = ezdxf.readfile(make_square_plan(tmp_path / "p.dxf"))
    doc.layers.add("PORTE")
    doc.blocks.new("Fantasma")
    msp = doc.modelspace()
    msp.add_blockref("Fantasma", (250, 250), dxfattribs={"layer": "PORTE"})
    msp.add_blockref("Fantasma", (260, 250), dxfattribs={"layer": "0"})
    doc.blocks.delete_block("Fantasma", safe=False)
    doc.saveas(tmp_path / "q.dxf")
    rep = convert(tmp_path / "q.dxf", tmp_path / "o.obj")
    assert rep.size_m[0] == pytest.approx(5.0, rel=0.01)
    assert any("non leggibili" in w for w in rep.warnings)


def test_wrong_declared_units_are_corrected_when_another_unit_fits(tmp_path):
    """A 500-unit building tagged 'mm' would be 0.5 m wide: the numbers fit cm, so use cm
    (both real files of the author declare mm but are in cm)."""
    rep = convert(make_square_plan(tmp_path / "p.dxf", units=4), tmp_path / "o.obj")
    assert rep.unit == "cm" and rep.size_m[0] == pytest.approx(5.0, rel=0.01)
    assert any("dichiara 'mm'" in w and "'cm'" in w for w in rep.warnings)
    ok = convert(make_square_plan(tmp_path / "q.dxf", units=5), tmp_path / "o2.obj")
    assert not any("dichiara" in w or "Dimensioni insolite" in w for w in ok.warnings)


def test_units_are_not_corrected_when_forced_or_when_nothing_fits(tmp_path):
    forced = convert(make_square_plan(tmp_path / "p.dxf", units=4), tmp_path / "a.obj", Config(units="mm"))
    assert forced.unit == "mm" and any("Dimensioni insolite" in w for w in forced.warnings)
    # a 0.5-unit building tagged m: cm and mm only make it smaller, nothing fits, so keep m and warn
    tiny = convert(make_square_plan(tmp_path / "t.dxf", side=0.5, thick=0.05, units=6),
                   tmp_path / "b.obj")
    assert tiny.unit == "m" and any("Dimensioni insolite" in w for w in tiny.warnings)


def test_window_next_to_a_t_junction_has_the_wall_thickness(tmp_path):
    """A long crossing wall touches the window gap: it must not decide the wall direction
    nor inflate the measured thickness."""
    from shapely.geometry import box

    doc = ezdxf.new("R2018", setup=True)
    doc.units = 5
    for name in ("MURI", "FINESTRE"):
        doc.layers.add(name)
    msp = doc.modelspace()
    t = 20
    walls = unary_union([
        box(0, 0, 1000, t), box(0, 500 - t, 1000, 500),   # bottom, top
        box(0, 0, t, 500), box(1000 - t, 0, 1000, 500),   # left, right
        box(280, t, 300, 480),                             # long interior wall, ends on the top wall
    ])
    gap = box(300, 500 - t, 520, 500)                      # window gap right next to that junction
    for ls in getattr(walls.boundary.difference(gap), "geoms", []):
        c = list(ls.coords)
        for a, b in zip(c, c[1:]):
            msp.add_line(a, b, dxfattribs={"layer": "MURI"})
    for off in (0, t / 2, t):
        msp.add_line((300, 480 + off), (520, 480 + off), dxfattribs={"layer": "FINESTRE"})
    for x in (300, 520):  # the symbol's end lines connect the three lines into one window
        msp.add_line((x, 480), (x, 500), dxfattribs={"layer": "FINESTRE"})
    doc.saveas(tmp_path / "t.dxf")
    rep = convert(tmp_path / "t.dxf", tmp_path / "o.obj")
    assert rep.windows == 1 and not any("non toccano" in w for w in rep.warnings)
    lo, hi = Obj(rep.output).bbox("Vetri")
    assert hi[0] - lo[0] == pytest.approx(2.2, abs=0.03)   # window width
    assert hi[2] - lo[2] == pytest.approx(0.02, abs=1e-3)  # a thin pane...
    # ...sitting in a 20 cm wall: the underside of the lintel above the window (a horizontal
    # face at 2.2 m spanning the window width) is as deep as the wall is thick
    obj = Obj(rep.output)
    soffits = [ids for ids, _ in obj.groups["Muri"]
               if all(abs(obj.v[i][1] - 2.2) < 1e-6 for i in ids)
               and min(obj.v[i][0] for i in ids) == pytest.approx(3.0, abs=0.01)
               and max(obj.v[i][0] for i in ids) == pytest.approx(5.2, abs=0.01)]
    assert soffits
    zs = [obj.v[i][2] for ids in soffits for i in ids]
    assert max(zs) - min(zs) == pytest.approx(0.2, abs=0.005)


def test_closed_shapes_inside_a_hole_are_kept_as_islands(tmp_path):
    """Outer + inner perimeter polylines, and partitions / a column drawn as closed
    polylines inside the inner one on the same layer: the inner perimeter is a hole, the
    partitions are islands in it and must not be cut away with the hole."""
    path = make_square_plan(tmp_path / "p.dxf", side=1000, thick=30)
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    for pts in ([(30, 300), (700, 300), (700, 310), (30, 310)],      # partition 6.7 x 0.1
                [(500, 500), (540, 500), (540, 540), (500, 540)]):   # column 0.4 x 0.4
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "MURI"})
    doc.saveas(path)
    rep = convert(path, tmp_path / "o.obj")
    ring = 10.0 ** 2 - 9.4 ** 2
    assert rep.wall_area_m2 == pytest.approx(ring + 6.7 * 0.1 + 0.4 * 0.4, rel=1e-3)
    assert rep.wall_pieces == 2  # ring + partition (touching) and the free-standing column


def test_wall_direction_averages_edges_that_point_the_opposite_way():
    """Edges at angle 0 and pi-1e-13 are the same line: their mean is horizontal, not vertical."""
    from shapely.geometry import Point, Polygon

    from dwg2c4d.openings import _WallEdges

    wall = Polygon([(0, 0), (10, 0), (10, 0.2), (0, 0.2 + 1e-12)])
    (ux, uy), *_ = _WallEdges(wall).directions_near(Point(5, 0.1).buffer(0.5))
    assert abs(ux) > 0.9999 and abs(uy) < 0.01


def test_a_partition_drawn_a_little_into_the_wall_is_still_a_partition():
    from shapely.geometry import box

    from dwg2c4d.geom import nest_polygons

    ring = [box(0, 0, 10, 6), box(0.3, 0.3, 9.7, 5.7)]
    stub = box(5.0, 0.29, 5.1, 3.0)  # 1 cm into the south wall, the rest inside the room
    walls = nest_polygons(ring + [stub])
    assert walls.area == pytest.approx(60 - 5.4 * 9.4 + 0.1 * (3.0 - 0.3) + 0.0, abs=1e-6)
    assert walls.buffer(-0.01).contains(box(5.02, 1.0, 5.08, 2.0))
