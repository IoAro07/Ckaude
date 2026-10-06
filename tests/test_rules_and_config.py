import json

import pytest

from dwg2c4d.config import Config, LayerRules


@pytest.mark.parametrize("layer,expected", [
    ("MURI", "wall"), ("A-WALL", "wall"), ("A-WALL-FULL", "wall"), ("PARETI_INT", "wall"),
    ("0_MURATURE", "wall"), ("TRAMEZZI", "wall"), ("Muri-Portanti", "wall"),
    ("MURI_PORTANTI", "wall"),  # contains 'porta...' but is a wall layer
    ("PORTE", "door"), ("A-DOOR", "door"), ("porte_interne", "door"), ("PORTONE", "door"),
    ("FINESTRE", "window"), ("A-GLAZ", "window"), ("SERRAMENTI", "window"), ("WINDOWS", "window"),
    ("PILASTRI", "column"), ("A-COLS", "column"), ("COLONNE", "column"),
    ("FONDELLI", "wall"), ("Fondelli_interni", "wall"), ("DIVISORI", "wall"),  # interior partitions
    ("QUOTE", None), ("TESTI", None), ("Layer1", None), ("0", None), ("PORTANTI", None),
    ("ARREDI", None), ("HATCH", None),
    # layer names from a real plan: numbered, with elevations and furniture on layers that
    # mention walls/doors
    ("1 Porte", "door"), ("2 Finestre", "window"), ("12 Prospetto Parete Attrezzata", None),
    ("11 Prospetto Frontale", None), ("6 Arredo Camera da Letto", None), ("17 Quote e Testi", None),
    ("16 Tetto", "roof"), ("15 Verde", None), ("Sezione A-A Muri", None),
])
def test_default_layer_classification(layer, expected):
    assert LayerRules().classify_layer(layer) == expected


@pytest.mark.parametrize("block,expected", [
    ("PORTA90", "door"), ("Porta_80", "door"), ("DOOR-36", "door"), ("FINESTRA120", "window"),
    ("WINDOW1", "window"), ("*U12", None), ("TAVOLO", None), ("PORTANTE", None),
])
def test_block_name_hints(block, expected):
    assert LayerRules().classify_block(block) == expected


def test_block_hint_wins_over_layer():
    rules = LayerRules()
    assert rules.classify("MURI", "PORTA90") == "door"
    assert rules.classify("Layer7", "PORTA90") == "door"
    # a door block on a furniture layer is not a plan door ("Porta Asciugamani" = towel rail)
    assert rules.classify("6 Arredo Bagno", "Porta Asciugamani") is None
    assert rules.classify("ARREDI", "PORTA90") is None
    assert rules.classify("MURI", "TAVOLO") == "wall"


def test_overrides_replace_defaults_for_that_category_only():
    rules = LayerRules({"wall": ["A-MURI*", "setti"]})
    assert rules.classify_layer("A-MURI-EST") == "wall"
    assert rules.classify_layer("SETTI") == "wall"
    assert rules.classify_layer("MURI") is None  # default replaced
    assert rules.classify_layer("PORTE") == "door"  # other categories keep defaults


def test_config_json_roundtrip(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({
        "wall_height": 3.0, "wall_mode": "faces", "area": [0, 0, 10, 10],
        "layers": {"wall": ["XW*"]},
    }))
    cfg = Config.from_json(f)
    cfg.validate()
    assert cfg.wall_height == 3.0 and cfg.wall_mode == "faces" and cfg.area == (0, 0, 10, 10)
    assert cfg.layers.classify_layer("XW1") == "wall"


def test_config_rejects_unknown_keys(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"altezza": 3}))
    with pytest.raises(ValueError, match="sconosciute"):
        Config.from_json(f)
    f.write_text(json.dumps({"layers": {"floors": ["x"]}}))
    with pytest.raises(ValueError, match="categorie"):
        Config.from_json(f)


@pytest.mark.parametrize("kwargs", [
    {"wall_mode": "nope"}, {"units": "yards"}, {"out_units": "km"}, {"wall_height": 0},
    {"window_sill": -1}, {"area": (5, 5, 1, 1)},
])
def test_config_validation(kwargs):
    with pytest.raises(ValueError):
        Config(**kwargs).validate()
