import os
import stat
import sys

import ezdxf
import pytest

from dwg2c4d import cli, dwgfile as convert_mod
from dwg2c4d.dwgfile import ConversionError, open_drawing

from helpers import Obj

posix_only = pytest.mark.skipif(sys.platform.startswith("win"), reason="uses a shell script")


# --- CLI ---------------------------------------------------------------------------------

def test_cli_end_to_end(tmp_path, capsys, sample_lines):
    out = tmp_path / "casa.obj"
    assert cli.main([str(sample_lines), "-o", str(out), "--altezza-muri", "3.0"]) == 0
    text = capsys.readouterr().out
    assert out.exists() and out.with_suffix(".mtl").exists()
    assert "8.00 x 6.00 m" in text and "2 / 2" in text
    assert Obj(out).bbox("Muri")[1][1] == pytest.approx(3.0)
    mtl = out.with_suffix(".mtl").read_text()
    for name in ("Muri", "Vetri", "Pavimento_Soggiorno"):  # the sample has a room called Soggiorno
        assert f"newmtl {name}" in mtl
    assert f"mtllib {out.with_suffix('.mtl').name}" in out.read_text()


def test_cli_default_output_next_to_input(tmp_path, sample_lines):
    assert cli.main([str(sample_lines)]) == 0
    assert sample_lines.with_suffix(".obj").exists()


def test_cli_list_layers(capsys, sample_lines):
    assert cli.main([str(sample_lines), "--elenca-layer"]) == 0
    text = capsys.readouterr().out
    for layer, use in (("MURI", "muri"), ("PORTE", "porte"), ("FINESTRE", "finestre")):
        assert any(line.startswith(layer) and use in line for line in text.splitlines())
    assert any(line.startswith("QUOTE") and "-" in line for line in text.splitlines())
    assert "PORTA90" in text
    assert "Unita' dichiarate nel file: cm" in text
    assert "zona: x" in text  # where each layer's content sits, to pick a --area


def test_cli_options_are_applied(tmp_path, sample_lines):
    out = tmp_path / "o.obj"
    assert cli.main([str(sample_lines), "-o", str(out), "--no-pavimento", "--no-vetri", "--soffitto",
                     "--unita-output", "cm", "--specchia", "--origine", "disegno", "--infissi", "semplici"]) == 0
    obj = Obj(out)
    assert set(obj.groups) == {"Muri", "Soffitto"}
    assert obj.bbox("Muri")[1][0] == pytest.approx(800)
    assert obj.bbox("Muri")[0][2] == pytest.approx(0, abs=1e-3)  # mirrored: z in [0, 600]


def test_cli_layer_overrides_and_area(tmp_path, capsys):
    from conftest import make_square_plan
    path = make_square_plan(tmp_path / "p.dxf", layer="A1")
    assert cli.main([str(path), "-o", str(tmp_path / "x.obj")]) == 1
    assert "--elenca-layer" in capsys.readouterr().err
    assert cli.main([str(path), "-o", str(tmp_path / "x.obj"), "--muri", "A1"]) == 0
    # a crop window with no walls inside is reported like "no walls found"
    assert cli.main([str(path), "-o", str(tmp_path / "y.obj"), "--muri", "A1",
                     "--area", "5000,5000,6000,6000"]) == 1
    assert "Nessun muro" in capsys.readouterr().err


def test_cli_config_file_is_overridden_by_flags(tmp_path, sample_lines):
    cfg = tmp_path / "c.json"
    cfg.write_text('{"wall_height": 3.5}')
    a, b = tmp_path / "a.obj", tmp_path / "b.obj"
    assert cli.main([str(sample_lines), "-o", str(a), "--config", str(cfg)]) == 0
    assert cli.main([str(sample_lines), "-o", str(b), "--config", str(cfg), "--altezza-muri", "2.5"]) == 0
    assert Obj(a).bbox("Muri")[1][1] == pytest.approx(3.5)
    assert Obj(b).bbox("Muri")[1][1] == pytest.approx(2.5)


@pytest.mark.parametrize("args,needle", [
    (["nonexistent.dxf"], "non trovato"),
    (["x.txt"], "non supportato"),
    (["{dxf}", "--altezza-muri", "-1"], "wall_height"),
    (["{dxf}", "--area", "5,5,1,1"], "area"),
])
def test_cli_errors_are_friendly(tmp_path, capsys, sample_lines, args, needle):
    (tmp_path / "x.txt").write_text("hi")
    args = [a.replace("{dxf}", str(sample_lines)) for a in args]
    args = [str(tmp_path / a) if a in ("x.txt",) else a for a in args]
    assert cli.main(args) == 1
    assert needle in capsys.readouterr().err


def test_cli_area_argument_must_have_four_numbers():
    with pytest.raises(SystemExit):
        cli.main(["x.dxf", "--area", "1,2,3"])


# --- DWG handling -----------------------------------------------------------------------

def _fake_dwg(path):
    path.write_bytes(b"AC1032" + b"\0" * 100)
    return path


def test_dwg_without_any_converter_explains_what_to_install(tmp_path, monkeypatch):
    monkeypatch.setattr(convert_mod, "_oda_candidates", lambda: [])
    monkeypatch.setattr(convert_mod.shutil, "which", lambda name: None)
    with pytest.raises(ConversionError) as err:
        open_drawing(_fake_dwg(tmp_path / "a.dwg"))
    msg = str(err.value)
    assert "ODA File Converter" in msg and "DXF" in msg and "LibreDWG" in msg


@posix_only
def test_dwg_converted_through_dwg2dxf(tmp_path, monkeypatch, sample_lines):
    """A stand-in 'dwg2dxf' that honours LibreDWG's CLI: dwg2dxf -y -o OUT IN."""
    script = tmp_path / "dwg2dxf"
    script.write_text(f'#!/bin/sh\nwhile [ "$1" != "-o" ]; do shift; done\ncp "{sample_lines}" "$2"\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    dwg = _fake_dwg(tmp_path / "pianta.dwg")

    doc = open_drawing(dwg, converter=str(script))
    assert len(doc.modelspace().query("LINE")) > 0

    monkeypatch.setattr(convert_mod, "_oda_candidates", lambda: [])
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    out = tmp_path / "o.obj"
    assert cli.main([str(dwg), "-o", str(out), "--origine", "disegno"]) == 0
    assert Obj(out).bbox("Muri")[1][0] == pytest.approx(8.0)


@posix_only
def test_dwg2dxf_failure_is_reported(tmp_path):
    script = tmp_path / "dwg2dxf"
    script.write_text("#!/bin/sh\necho 'boom' >&2\nexit 3\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    with pytest.raises(ConversionError, match="boom"):
        open_drawing(_fake_dwg(tmp_path / "a.dwg"), converter=str(script))


def test_oda_converter_is_configured_through_ezdxf_options(tmp_path, monkeypatch, sample_lines):
    from ezdxf.addons import odafc

    seen = {}

    def fake_readfile(path, *a, **k):
        seen["path"] = path
        seen["win"] = ezdxf.options.get("odafc-addon", "win_exec_path")
        seen["unix"] = ezdxf.options.get("odafc-addon", "unix_exec_path")
        return ezdxf.readfile(str(sample_lines))

    monkeypatch.setattr(odafc, "readfile", fake_readfile)
    exe = tmp_path / "ODAFileConverter"
    exe.write_text("")
    open_drawing(_fake_dwg(tmp_path / "a.dwg"), converter=str(exe))
    assert seen["unix"] == str(exe) and seen["win"] == str(exe)


def test_dxf_that_is_really_a_dwg_is_detected_by_content(tmp_path, monkeypatch):
    monkeypatch.setattr(convert_mod, "_oda_candidates", lambda: [])
    monkeypatch.setattr(convert_mod.shutil, "which", lambda name: None)
    odd = _fake_dwg(tmp_path / "pianta.bin")
    with pytest.raises(ConversionError, match="convertitore"):
        open_drawing(odd)
