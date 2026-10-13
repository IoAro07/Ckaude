"""Edges of the terrain: the interpolated ground is smooth, the terrain always carries the platform, a site plan asked for as the
plan gets no platform under all its linework."""

import ezdxf
import numpy as np
import pytest
from builders import building
from helpers import Obj
from shapely.geometry import Polygon, box
from test_autodetect import _opened, _titled_building
from test_terrain import add_contours

from dwg2c4d import Config, convert
from dwg2c4d.autodetect import analyze
from dwg2c4d.config import LayerRules
from dwg2c4d.terrain import SMOOTH, build_ground


AWAY = box(18, 18, 19, 19)  # a platform in a corner: the ridge is far from it


def test_the_ground_is_blurred_a_little_so_that_the_flat_triangles_between_contour_lines_leave_no_terraces():
    # a sharp ridge: the ground rises 50 % up to x = 0 and falls again; the grid is read at 1 m
    pts = np.array([[x, y, 105.0 - 0.5 * abs(x)] for x in np.arange(-10.0, 10.5, 1.0) for y in np.arange(-30.0, 31.0, 6.0)])
    t = build_ground(pts, (-20, -20, 20, 20), AWAY, Polygon(), 100.0, -0.2, 1.0)
    assert SMOOTH > 0 and 4.5 < t.height(0.0, 0.0) < 4.9  # the point of the ridge is a little lower than the lines say (5.0)
    plane = np.array([[x, y, 100.0 + 0.1 * x] for x in np.arange(-30.0, 31.0, 2.0) for y in np.arange(-30.0, 31.0, 6.0)])
    flat = build_ground(plane, (-20, -20, 20, 20), AWAY, Polygon(), 100.0, -0.2, 1.0)
    assert flat.height(5.0, 3.0) == pytest.approx(0.5, abs=0.01)  # a plane stays what it is


def test_the_terrain_carries_the_whole_platform_even_where_the_contour_lines_stop_short_of_the_house(tmp_path):
    doc, msp = building()
    doc.layers.add("CURVE-LIV")
    add_contours(msp, first=500, every=200, levels=20)  # the lines start 5 m east of the west wall
    msp.add_text("+0.00 (100.50)", height=20, dxfattribs={"insert": (1200, 100), "layer": "0"})
    doc.saveas(tmp_path / "t.dxf")
    report = convert(tmp_path / "t.dxf", tmp_path / "t.obj", Config(terrain_auto=True, floors_to_outer_face=False))
    t = report.plan.terrain
    assert t is not None and t.window[0] <= -2.0 + 1e-9  # it reaches the strip of the platform west of the house
    obj = Obj(tmp_path / "t.obj")
    assert obj.bbox("Terreno")[0][0] <= obj.bbox("Muri")[0][0] - 2.0 + 1e-6  # no wall hangs over the edge of the ground


def test_a_site_plan_asked_for_as_the_plan_gets_no_platform_under_all_its_lines_unless_asked(tmp_path):
    doc, path = _titled_building(tmp_path)
    doc = ezdxf.readfile(path)
    doc.layers.add("CATASTO")
    doc.layers.add("CURVE-LIV")
    msp = doc.modelspace()
    for pts in (((5000, -6000), (11000, -6000), (11000, -2000), (5000, -2000)),
                ((5100, -5900), (10900, -5900), (10900, -2100), (5100, -2100))):
        msp.add_lwpolyline(list(pts), close=True, dxfattribs={"layer": "CATASTO"})
    msp.add_text("PLANIMETRIA CATASTALE", height=20, dxfattribs={"layer": "TESTI", "insert": (7000, -6300)})
    for k in range(14):  # contour lines across the parcel
        x = 5300 + 400 * k
        msp.add_lwpolyline([(x, -5800), (x, -2200)], dxfattribs={"layer": "CURVE-LIV", "elevation": 10000 + 40 * k})
    doc.saveas(path)
    site = next(v for v in analyze(ezdxf.readfile(path)).views if v.kind == "site")
    cfg = {"auto": True, "view": site.id, "terrain_auto": True, "layers": LayerRules({"wall": ["CATASTO"]})}
    asked = _opened(path, Config(**cfg))
    assert asked.terrain is None and not any(w.startswith("Terreno") for w in asked.warnings)
    forced = _opened(path, Config(**{**cfg, "terrain": True}))
    assert forced.terrain is not None  # --terreno is the user's word
    building_plan = _opened(path, Config(auto=True, terrain_auto=True))  # the building itself: the parcel is a drawing apart
    assert building_plan.terrain is None and any("non riesco a capire dove sta la casa sul rilievo" in w for w in building_plan.warnings)


def test_contour_vertices_far_beyond_the_terrain_are_not_triangulated(monkeypatch):
    import dwg2c4d.terrain as terrain

    seen = []
    real = terrain.surface
    monkeypatch.setattr(terrain, "surface", lambda points, xs, ys: seen.append(len(points)) or real(points, xs, ys))
    near = np.array([[x, y, 100.0 + 0.1 * x] for x in np.arange(-30.0, 31.0, 3.0) for y in np.arange(-30.0, 31.0, 3.0)])
    far = np.array([[x, y, 100.0] for x in np.arange(500.0, 900.0, 4.0) for y in np.arange(500.0, 900.0, 4.0)])  # a different lot, 500 m off
    build_ground(np.concatenate([near, far]), (-20, -20, 20, 20), AWAY, Polygon(), 100.0, -0.2, 1.0)
    assert seen == [int(((near[:, 0] >= -35) & (near[:, 0] <= 35)).sum())]  # only the points within 15 m of the terrain went in


def test_the_check_picture_of_the_plan_draws_the_contour_lines_of_the_terrain_within_the_plan(tmp_path, monkeypatch):
    from matplotlib.axes import Axes
    from test_terrain import run

    seen = []
    real = Axes.contour

    def spy(self, *args, **kw):
        seen.append(self)
        return real(self, *args, **kw)

    monkeypatch.setattr(Axes, "contour", spy)
    report, _ = run(tmp_path, images=True)
    assert len(seen) == 1 and report.overlay_path is not None and report.overlay_path.exists()
    seen.clear()
    run(tmp_path, images=True, terrain_auto=False)
    assert seen == []  # no terrain: the picture is what it was


def test_the_3d_preview_shows_the_terrain_only_round_the_house(tmp_path, monkeypatch):
    import dwg2c4d.qa as qa
    from test_terrain import run

    seen = {}
    monkeypatch.setattr(qa, "save_views", lambda mesh, path, **kw: seen.update(kw) or path)
    report, _ = run(tmp_path, images=True)
    x0, x1, y0, y1 = seen["clip"]  # (xmin, xmax, ymin, ymax) round the 10 x 6 m house at the drawing's origin
    assert (x0, x1, y0, y1) == pytest.approx((-15.0, 25.0, -15.0, 21.0), abs=0.2)
    seen.clear()
    report, _ = run(tmp_path, images=True, terrain_auto=False)
    assert seen["clip"] is None  # no terrain: the whole model, as ever
