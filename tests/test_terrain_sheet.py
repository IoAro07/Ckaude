"""The terrain when the contour lines are drawn in another view of the sheet than the plan: the plan is laid on the survey by the
lines of the lot the two have in common. Synthetic sheets (centimetres)."""

import pytest
from shapely.geometry import LineString, Polygon
from test_views import add_plan, add_title, new_sheet

from dwg2c4d import Config, convert

LOT = [(0, 0), (6200, 0), (6200, 3100), (4300, 4800), (700, 4800), (0, 2600)]  # an irregular lot, 62 x 48 m (cm)
HOUSE_IN_LOT = (2500, 1500)  # where the house (14 x 9 m) stands in the lot: its south-west corner
SLOPE = (0.05, 0.08)  # the ground rises 5 % eastwards and 8 % northwards


def ground(x, y):
    """The ground at (x, y) of the plan (m), above the finished floor (100 m)."""
    return SLOPE[0] * x + SLOPE[1] * y


def add_survey(msp, dx, dy, layer="CURVE-LIV"):
    """The lot and the contour lines of the slope in it (every 20 cm: diagonal lines) drawn with its corner at (dx, dy)."""
    msp.add_lwpolyline([(x + dx, y + dy) for x, y in LOT], close=True)
    lot = Polygon(LOT)
    hx, hy = HOUSE_IN_LOT
    for k in range(-14, 24):
        c = 0.2 * k  # the height above the floor of this line, m
        # the points of the line ground(x, y) = c, x and y in m from the house's corner, far beyond the lot at both ends
        a, b = (-60.0, (c + SLOPE[0] * 60.0) / SLOPE[1]), (90.0, (c - SLOPE[0] * 90.0) / SLOPE[1])
        line = LineString([(hx + 100 * a[0], hy + 100 * a[1]), (hx + 100 * b[0], hy + 100 * b[1])]).intersection(lot)
        for part in getattr(line, "geoms", [line]):
            if part.length > 1.0:
                msp.add_lwpolyline([(x + dx, y + dy) for x, y in part.coords],
                                   dxfattribs={"layer": layer, "elevation": round((100.0 + c) * 100.0)})


def survey_sheet(path, offset_survey=(30000, 0), offset_site=(16000, 0)):
    """Three drawings: the plan (view A), the survey of the lot with the contour lines (view B, no house in it) and the site
    plan (view C) that has the same lot and a copy of the plan in it, joined to the lot by a drive."""
    doc, msp = new_sheet()
    doc.layers.add("CURVE-LIV")
    add_plan(msp, 0, 0)
    add_title(msp, "PIANTA PIANO TERRA", 100, -150)
    add_survey(msp, *offset_survey)
    add_title(msp, "RILIEVO", offset_survey[0] + 100, -150)
    msp.add_lwpolyline([(x + offset_site[0], y + offset_site[1]) for x, y in LOT], close=True)
    hx, hy = offset_site[0] + HOUSE_IN_LOT[0], offset_site[1] + HOUSE_IN_LOT[1]
    add_plan(msp, hx, hy)
    for dx in (500, 560):
        msp.add_line((hx + dx, offset_site[1]), (hx + dx, hy))
    add_title(msp, "PLANIMETRIA GENERALE", offset_site[0] + 100, -150)
    msp.add_text("+0.00 (100.00)", height=20, dxfattribs={"insert": (1600, 1000)})
    doc.saveas(path)
    return path


def converted(tmp_path, **kw):
    path = survey_sheet(tmp_path / "s.dxf", **kw)
    return convert(path, tmp_path / "s.obj", Config(auto=True, terrain_auto=True, images=False, floors_to_outer_face=False))


FAR = ((28.0, 0.0), (-12.0, 5.0), (7.0, -11.0), (7.0, 24.0))  # far from the platform: there the ground is the slope itself


def test_the_plan_is_put_on_the_survey_drawn_in_another_view_by_the_lines_of_the_lot(tmp_path):
    report = converted(tmp_path)
    t = report.plan.terrain
    assert t is not None and report.terrain["registered"] and report.terrain["view"] is not None
    assert report.terrain["shift"] == pytest.approx((325.0, 15.0), abs=0.05)  # 300 m of survey offset + the house in the lot
    for x, y in FAR:
        assert t.height(x, y) == pytest.approx(ground(x, y), abs=0.25), (x, y)
    assert any("l'ho messa sul rilievo" in w for w in report.warnings)


def test_the_survey_may_lie_anywhere_on_the_sheet(tmp_path):
    report = converted(tmp_path, offset_survey=(-24000, 7000), offset_site=(16000, -3000))
    assert report.terrain["shift"] == pytest.approx((-240.0 + 25.0, 70.0 + 15.0), abs=0.05)
    for x, y in FAR:
        assert report.plan.terrain.height(x, y) == pytest.approx(ground(x, y), abs=0.25), (x, y)


def test_when_the_plan_cannot_be_put_on_the_survey_there_is_no_terrain_and_it_is_said(tmp_path):
    doc, msp = new_sheet()
    doc.layers.add("CURVE-LIV")
    add_plan(msp, 0, 0)
    add_title(msp, "PIANTA PIANO TERRA", 100, -150)
    add_survey(msp, 30000, 0)  # a survey that shares no line with anything
    add_title(msp, "RILIEVO", 30100, -150)
    doc.saveas(tmp_path / "s.dxf")
    report = convert(tmp_path / "s.dxf", tmp_path / "s.obj", Config(auto=True, terrain_auto=True, images=False))
    assert report.terrain is None
    assert any("non riesco a capire dove sta la casa sul rilievo" in w for w in report.warnings)
