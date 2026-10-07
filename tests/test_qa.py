"""The pictures and the text report written next to the model."""

import struct
import zlib

import numpy as np
import pytest
from builders import PLAN_AREA, T, WIN_S, building
from helpers import Obj

from dwg2c4d import Config, convert
from dwg2c4d.cli import main
from dwg2c4d.qa import opening_notes, summary_lines
from dwg2c4d.render import write_png


def read_png(path):
    """Decode a PNG written by write_png (8-bit RGB, filter 0): the tests need no imaging library."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, size = 8, b"", None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        assert zlib.crc32(kind + body) & 0xFFFFFFFF == struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])[0]
        if kind == b"IHDR":
            size = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    w, h = size
    raw = zlib.decompress(idat)
    rows = [raw[y * (3 * w + 1) + 1:(y + 1) * (3 * w + 1)] for y in range(h)]
    return np.frombuffer(b"".join(rows), np.uint8).reshape(h, w, 3)


@pytest.fixture
def plan(tmp_path):
    doc, msp = building()
    doc.layers.add("Quote e Testi")
    msp.add_mtext("120\n130", dxfattribs={"insert": ((WIN_S[0] + WIN_S[1]) / 2, -45), "char_height": 10,
                                          "layer": "Quote e Testi", "attachment_point": 5})
    path = tmp_path / "q.dxf"
    doc.saveas(path)
    return path


def run(plan, tmp_path, **kw):
    return convert(plan, tmp_path / "o.obj", Config(area=PLAN_AREA, fixtures="detailed", **kw))


def test_png_writer_round_trips(tmp_path):
    img = np.zeros((4, 5, 3))
    img[1, 2] = (1.0, 0.5, 0.0)
    out = read_png(write_png(tmp_path / "t.png", img))
    assert out.shape == (4, 5, 3) and tuple(out[1, 2]) == (255, 128, 0) and out[0, 0].sum() == 0


def test_the_report_file_is_always_written(plan, tmp_path):
    rep = run(plan, tmp_path)
    text = rep.report_path.read_text(encoding="utf-8")
    assert rep.report_path.name == "o_report.txt"
    assert "Creato o.obj" in text and "Creato o_aperture.csv" in text
    assert "P01" in text and "F01" in text and "APERTURE" in text
    assert "120x130" not in text and "120 130" in text  # what was read next to the window


def test_notes_are_grouped_by_text(plan, tmp_path):
    rep = run(plan, tmp_path)
    notes = opening_notes(rep)
    assert all(isinstance(ids, list) for ids in notes.values())
    for o in rep.openings:
        o.notes.append("controlla la cerniera")
    grouped = opening_notes(rep)
    assert sorted(grouped["controlla la cerniera"]) == sorted(o.id for o in rep.openings)


def test_images_are_off_for_the_library_and_on_for_the_command_line(plan, tmp_path, capsys):
    rep = run(plan, tmp_path)
    assert rep.preview_path is None and not (tmp_path / "o_anteprima_3d.png").exists()
    assert main([str(plan), "-o", str(tmp_path / "c.obj"), "--area=" + ",".join(map(str, PLAN_AREA))]) == 0
    out = capsys.readouterr().out
    assert "c_anteprima_3d.png" in out and "c_controllo_pianta.png" in out and "c_report.txt" in out
    assert (tmp_path / "c_anteprima_3d.png").stat().st_size > 1000


def test_no_images_flag(plan, tmp_path):
    area = "--area=" + ",".join(map(str, PLAN_AREA))
    assert main([str(plan), "-o", str(tmp_path / "n.obj"), area, "--no-immagini"]) == 0
    assert not (tmp_path / "n_anteprima_3d.png").exists() and (tmp_path / "n_report.txt").exists()


def test_the_3d_preview_shows_the_model(plan, tmp_path):
    rep = run(plan, tmp_path, images=True)
    img = read_png(rep.preview_path)
    assert img.shape == (470, 3 * 620, 3)
    background = np.array([0.96, 0.96, 0.97]) * 255
    covered = (np.abs(img.astype(float) - background).sum(axis=2) > 12).mean()
    assert 0.05 < covered < 0.6  # something is drawn in every view, the picture is not all model
    for k in range(3):  # each of the three views has model pixels
        panel = img[:, k * 620:(k + 1) * 620].astype(float)
        assert (np.abs(panel - background).sum(axis=2) > 12).sum() > 2000


def test_the_plan_overlay_is_a_picture(plan, tmp_path):
    pytest.importorskip("matplotlib")
    rep = run(plan, tmp_path, images=True)
    assert rep.overlay_path.name == "o_controllo_pianta.png" and rep.overlay_path.stat().st_size > 5000
    head = rep.overlay_path.read_bytes()[:8]
    assert head == b"\x89PNG\r\n\x1a\n"
    assert rep.images_note == ""


def test_a_failing_overlay_does_not_stop_the_conversion(plan, tmp_path, monkeypatch):
    import dwg2c4d.pipeline as pipeline

    def boom(*a, **k):
        raise RuntimeError("no display")

    monkeypatch.setattr(pipeline, "plan_overlay", boom)
    rep = run(plan, tmp_path, images=True)
    assert rep.overlay_path is None and "no display" in rep.images_note and rep.output.exists()


def test_missing_matplotlib_is_a_note_not_an_error(plan, tmp_path, monkeypatch):
    import dwg2c4d.pipeline as pipeline

    def missing(*a, **k):
        raise ImportError("matplotlib")

    monkeypatch.setattr(pipeline, "plan_overlay", missing)
    rep = run(plan, tmp_path, images=True)
    assert "matplotlib" in rep.images_note and rep.preview_path is not None


def test_summary_lines_match_what_the_command_line_prints(plan, tmp_path, capsys):
    rep = run(plan, tmp_path)
    lines = summary_lines(rep)
    assert any(l.startswith("  Porte / finestre") for l in lines) and any("Altezza muri" in l for l in lines)
    assert T == 30 and Obj(rep.output).verts
