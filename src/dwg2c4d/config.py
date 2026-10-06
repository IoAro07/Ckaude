"""Conversion settings and layer classification rules."""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field, fields
from pathlib import Path

CATEGORIES = ("wall", "door", "window", "column")

# Drawing-unit name -> metres. Names follow the DXF $INSUNITS table.
UNIT_TO_METERS = {
    "in": 0.0254,
    "ft": 0.3048,
    "mm": 0.001,
    "cm": 0.01,
    "m": 1.0,
}
INSUNITS_TO_NAME = {1: "in", 2: "ft", 4: "mm", 5: "cm", 6: "m"}

WALL_MODES = ("auto", "solid", "faces", "centerline")


@dataclass
class LayerRules:
    """Maps layer / block names to a category.

    Defaults recognise common Italian and English naming (MURI, PARETI, A-WALL,
    PORTE, A-DOOR, FINESTRE, A-GLAZ, PILASTRI, A-COLS ...). Names are split into
    alphabetic tokens, so ``MURI_PORTANTI`` is a wall layer and *not* a door layer.
    A user-supplied list of glob patterns for a category replaces its defaults.
    """

    overrides: dict[str, list[str]] = field(default_factory=dict)

    _EXACT = {
        "door": {"porta", "porte", "portone", "portoni", "portoncino", "portafinestra", "door", "doors", "dr"},
        "window": {
            "finestra", "finestre", "finestratura", "infisso", "infissi", "serramento",
            "serramenti", "vetrata", "vetrate", "window", "windows", "glaz", "glazing", "win",
        },
    }
    _PREFIX = {
        "wall": ("mur", "paret", "wall", "tramezz", "tamponam", "partit"),
        "door": ("door",),
        "window": ("finestr", "window", "glaz"),
        "column": ("pilast", "colonn", "column", "pillar", "cols"),
    }
    # Checked in this order: "MURI_PORTANTI" must not become a door layer.
    _ORDER = ("door", "window", "column", "wall")

    @staticmethod
    def tokens(name: str) -> list[str]:
        return [t for t in re.split(r"[^a-z]+", name.lower()) if t]

    def _default_match(self, category: str, name: str) -> bool:
        toks = self.tokens(name)
        exact = self._EXACT.get(category, ())
        prefixes = self._PREFIX.get(category, ())
        return any(t in exact or t.startswith(prefixes) for t in toks)

    def _override_match(self, category: str, name: str) -> bool:
        low = name.lower()
        return any(fnmatch.fnmatchcase(low, p.lower()) for p in self.overrides[category])

    def matches(self, category: str, name: str) -> bool:
        if category in self.overrides:
            return self._override_match(category, name)
        return self._default_match(category, name)

    def classify_layer(self, layer: str) -> str | None:
        for cat in self._ORDER:
            if self.matches(cat, layer):
                return cat
        return None

    def classify_block(self, block_name: str) -> str | None:
        """Block-name hint (doors and windows only), e.g. block ``PORTA90``.
        Same token rules as layers, so ``PORTANTE`` is not a door."""
        if block_name.startswith("*"):  # anonymous / dynamic blocks
            return None
        for cat in ("door", "window"):
            if self.matches(cat, block_name):
                return cat
        return None

    def classify(self, layer: str, block_name: str | None = None) -> str | None:
        if block_name:
            hint = self.classify_block(block_name)
            if hint:
                return hint
        return self.classify_layer(layer)


@dataclass
class Config:
    # Heights, in metres.
    wall_height: float = 2.70
    door_height: float = 2.10
    window_sill: float = 0.90
    window_height: float = 1.30

    # Wall interpretation.
    wall_mode: str = "auto"
    wall_thickness: float = 0.30  # used for centerline walls
    max_wall_thickness: float = 0.60  # thinness limit for double-line faces; opening depth
    merge_tolerance: float = 0.01  # hairline gaps between wall pieces are closed

    # Extra elements.
    floor_thickness: float = 0.20  # 0 disables the floor slab
    ceiling: bool = False
    ceiling_thickness: float = 0.20
    glass: bool = True
    glass_thickness: float = 0.02

    # Input / output.
    units: str | None = None  # force drawing units: mm, cm, m, in, ft
    out_units: str = "m"
    area: tuple[float, float, float, float] | None = None  # crop, in drawing units
    include_hidden: bool = False
    mirror: bool = False  # mirror the plan (if Cinema 4D shows it flipped)
    arc_tolerance: float = 0.002  # max deviation when flattening curves, metres
    converter: str | None = None  # path of ODAFileConverter / dwg2dxf

    layers: LayerRules = field(default_factory=LayerRules)

    def validate(self) -> None:
        if self.wall_mode not in WALL_MODES:
            raise ValueError(f"wall_mode deve essere uno di {WALL_MODES}")
        if self.units is not None and self.units not in UNIT_TO_METERS:
            raise ValueError(f"unita' del disegno non valida: {self.units!r}")
        if self.out_units not in ("m", "cm", "mm"):
            raise ValueError("unita' di output deve essere m, cm o mm")
        for name in ("wall_height", "door_height", "window_height", "wall_thickness",
                     "max_wall_thickness", "glass_thickness"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} deve essere positivo")
        if self.window_sill < 0 or self.floor_thickness < 0:
            raise ValueError("davanzale e spessore pavimento non possono essere negativi")
        if self.area is not None:
            x0, y0, x1, y1 = self.area
            if not (x1 > x0 and y1 > y0):
                raise ValueError("area: servono xmin,ymin,xmax,ymax con max > min")

    @classmethod
    def from_json(cls, path: str | Path) -> "Config":
        """Load a JSON file; keys are Config field names, plus ``layers`` for glob lists."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"chiavi sconosciute nel file di configurazione: {sorted(unknown)}")
        data = dict(data)
        layers = data.pop("layers", {})
        bad = set(layers) - set(CATEGORIES)
        if bad:
            raise ValueError(f"categorie di layer sconosciute: {sorted(bad)} (valide: {CATEGORIES})")
        if "area" in data and data["area"] is not None:
            data["area"] = tuple(float(v) for v in data["area"])
        cfg = cls(**data)
        cfg.layers = LayerRules({k: list(v) for k, v in layers.items()})
        return cfg
