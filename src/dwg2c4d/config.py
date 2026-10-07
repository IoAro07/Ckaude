"""Conversion settings and layer classification rules."""

from __future__ import annotations

import fnmatch
import json
import math
import re
from dataclasses import dataclass, field, fields
from pathlib import Path

CATEGORIES = ("wall", "door", "window", "column", "roof")

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
        "wall": ("mur", "paret", "wall", "tramezz", "tamponam", "partit", "fondell", "divisor"),
        "door": ("door",),
        "window": ("finestr", "window", "glaz"),
        "column": ("pilast", "colonn", "column", "pillar", "cols"),
        "roof": ("tett", "roof", "copertur"),
    }
    # A layer holding elevations, furniture, dimensions... is never a plan element even if
    # its name also says "parete" (e.g. "Prospetto Parete Attrezzata").
    _VETO = ("prospett", "sezion", "section", "elevat", "arred", "furnit", "quot", "dimens",
             "text", "testi", "tett", "roof", "verde", "landscap")
    # Checked in this order: "MURI_PORTANTI" must not become a door layer.
    _ORDER = ("door", "window", "column", "wall", "roof")

    @staticmethod
    def tokens(name: str) -> list[str]:
        return [t for t in re.split(r"[^a-z]+", name.lower()) if t]

    def _default_match(self, category: str, name: str, ignore_veto: bool = False) -> bool:
        toks = self.tokens(name)
        # "Tetto"/"Roof" are on the veto list for plan elements, but they are what the
        # roof category looks for.
        if category != "roof" and not ignore_veto and any(t.startswith(self._VETO) for t in toks):
            return False
        exact = self._EXACT.get(category, ())
        prefixes = self._PREFIX.get(category, ())
        return any(t in exact or t.startswith(prefixes) for t in toks)

    def _override_match(self, category: str, name: str) -> bool:
        low = name.lower()
        return any(fnmatch.fnmatchcase(low, p.lower()) for p in self.overrides[category])

    def matches(self, category: str, name: str, ignore_veto: bool = False) -> bool:
        if category in self.overrides:
            return self._override_match(category, name)
        return self._default_match(category, name, ignore_veto)

    def classify_layer(self, layer: str, ignore_veto: bool = False) -> str | None:
        for cat in self._ORDER:
            if self.matches(cat, layer, ignore_veto):
                return cat
        return None

    def classify_block(self, block_name: str, ignore_veto: bool = False) -> str | None:
        """Block-name hint (doors and windows only), e.g. block ``PORTA90``.
        Same token rules as layers, so ``PORTANTE`` is not a door."""
        if block_name.startswith("*"):  # anonymous / dynamic blocks
            return None
        for cat in ("door", "window"):
            if self.matches(cat, block_name, ignore_veto):
                return cat
        return None

    def vetoed(self, layer: str) -> bool:
        """Elevation / furniture / annotation layers: nothing on them is a plan element,
        not even a block called "Porta Asciugamani" (towel rail) or "Porta TV"."""
        return any(t.startswith(self._VETO) for t in self.tokens(layer))

    def classify(self, layer: str, block_name: str | None = None,
                 ignore_veto: bool = False) -> str | None:
        """``ignore_veto``: used when reading an elevation drawing, whose layers are named
        "Prospetto ..." and whose door/window blocks are exactly what we want."""
        if block_name and (ignore_veto or not self.vetoed(layer)):
            hint = self.classify_block(block_name, ignore_veto)
            if hint:
                return hint
        return self.classify_layer(layer, ignore_veto)


DEFAULT_WALL_HEIGHT = 2.70


@dataclass
class Config:
    # Heights, in metres. ``wall_height_auto``: take the wall height from the "h 300" texts written
    # in the rooms, unless a wall height was given (it turns itself off then).
    wall_height: float = DEFAULT_WALL_HEIGHT
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
    texts: bool = True  # read sizes, sills, room names and heights from the written texts
    passages: bool = True  # doorways drawn only as a gap between two wall ends (no door symbol)
    passage_max: float = 2.0  # metres: a wider gap between facing wall ends is open space, not a doorway
    vector_text: bool = True  # also read texts that were exploded into lines (letters drawn as lines)
    text_layers: list[str] = field(default_factory=list)  # extra layers (globs) that may hold exploded texts
    label_radius: float = 1.2  # metres: how far from an opening its written size may be
    wall_height_auto: bool = True
    fixtures: str = "simple"  # simple: a thin pane per window | detailed: frames, sashes, leaves, handles

    # Input / output.
    units: str | None = None  # force drawing units: mm, cm, m, in, ft
    out_units: str = "m"
    area: tuple[float, float, float, float] | None = None  # crop, in drawing units
    include_hidden: bool = False
    origin: str = "drawing"  # where the model's zero is: drawing | center | min (of the walls)
    c4d_json: bool = False  # also write <name>_model.json for a Cinema 4D import script
    write_table: bool = True  # write <name>_aperture.csv: every door/window, editable by hand
    table_in: str | None = None  # an edited openings table to apply (MODIFICA_* columns)
    walls_from_blocks: bool = False  # read blocks inserted on wall layers as walls
    mirror: bool = False  # mirror the plan (if Cinema 4D shows it flipped)
    arc_tolerance: float = 0.002  # max deviation when flattening curves, metres
    converter: str | None = None  # path of ODAFileConverter / dwg2dxf

    # Elevations (prospetti): each entry is (xmin, ymin, xmax, ymax[, floor_y]) in drawing
    # units. Door/window heights are read from them; floor_y (the drawing y of the finished
    # floor) defaults to the bottom of the lowest door in that elevation.
    elevations: list[tuple[float, ...]] = field(default_factory=list)

    # Roof built from the roof plan (layers named Tetto/Roof/Copertura, or --layer-tetto).
    roof: bool = False
    roof_area: tuple[float, float, float, float] | None = None  # where the roof plan is drawn
    roof_offset: tuple[float, float] | None = None  # drawing units; default: centre on the walls
    roof_pitch: float | None = None  # degrees; default: from the elevations, else 25
    roof_thickness: float = 0.15
    roof_default_pitch: float = 25.0

    layers: LayerRules = field(default_factory=LayerRules)

    def __post_init__(self) -> None:
        if self.wall_height != DEFAULT_WALL_HEIGHT:
            self.wall_height_auto = False  # an explicit height beats the written ones

    def validate(self) -> None:
        numbers = [getattr(self, n) for n in (
            "wall_height", "door_height", "window_sill", "window_height", "wall_thickness",
            "max_wall_thickness", "floor_thickness", "ceiling_thickness", "glass_thickness",
            "roof_thickness", "roof_default_pitch", "label_radius", "passage_max")]
        numbers += [v for box_ in (self.area, self.roof_area, self.roof_offset) if box_ for v in box_]
        numbers += [v for ev in self.elevations for v in ev]
        if self.roof_pitch is not None:
            numbers.append(self.roof_pitch)
        if not all(math.isfinite(v) for v in numbers):
            raise ValueError("i valori numerici devono essere finiti (niente nan/inf)")
        if self.wall_mode not in WALL_MODES:
            raise ValueError(f"wall_mode deve essere uno di {WALL_MODES}")
        if self.units is not None and self.units not in UNIT_TO_METERS:
            raise ValueError(f"unita' del disegno non valida: {self.units!r}")
        if self.fixtures not in ("simple", "detailed"):
            raise ValueError("fixtures deve essere simple o detailed")
        if self.origin not in ("drawing", "center", "min"):
            raise ValueError("origin deve essere drawing, center o min")
        if self.out_units not in ("m", "cm", "mm"):
            raise ValueError("unita' di output deve essere m, cm o mm")
        for name in ("wall_height", "door_height", "window_height", "wall_thickness",
                     "max_wall_thickness", "glass_thickness"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} deve essere positivo")
        if self.label_radius <= 0:
            raise ValueError("label_radius deve essere positivo")
        if self.window_sill < 0 or self.floor_thickness < 0:
            raise ValueError("davanzale e spessore pavimento non possono essere negativi")
        for name, box_ in (("area", self.area), ("roof_area", self.roof_area)):
            if box_ is not None:
                x0, y0, x1, y1 = box_
                if not (x1 > x0 and y1 > y0):
                    raise ValueError(f"{name}: servono xmin,ymin,xmax,ymax con max > min")
        for ev in self.elevations:
            if len(ev) not in (4, 5) or not (ev[2] > ev[0] and ev[3] > ev[1]):
                raise ValueError("prospetto: servono xmin,ymin,xmax,ymax (con max > min) "
                                 "ed eventualmente la quota Y del pavimento finito")
        if self.roof_pitch is not None and not (1.0 <= self.roof_pitch <= 80.0):
            raise ValueError("pendenza del tetto: tra 1 e 80 gradi")
        if self.roof_thickness <= 0:
            raise ValueError("roof_thickness deve essere positivo")

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
        for key in ("area", "roof_area", "roof_offset"):
            if data.get(key) is not None:
                data[key] = tuple(float(v) for v in data[key])
        if "elevations" in data:
            data["elevations"] = [tuple(float(v) for v in ev) for ev in data["elevations"]]
        cfg = cls(**data)
        cfg.layers = LayerRules({k: list(v) for k, v in layers.items()})
        return cfg
