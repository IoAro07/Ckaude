"""The height of a curb or a low wall, when the drawing writes it next to it ("Hmuro= 1,00 ml.", "Recinzione ... H= 160 cm").

A site plan marks its retaining walls and fences with a text that says how high they are. A text counts only if it names
the thing (muro, muretto, cordolo, recinzione, parapetto, argine) and gives an "H" with a number and a plausible height;
the room heights ("H 2,70 m" under the name of a room) never name a wall. The text belongs to the nearest body of
``falsewalls`` (a curb or low wall) within ``REACH``; a body that two texts give different heights is left at the
default height, and the report says so.
"""

from __future__ import annotations

import re

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from .falsewalls import _components
from .geom import union

REACH = 6.0  # m: a text belongs to the body of low wall nearest to it, if it is this close at most
HEIGHT_RANGE = (0.05, 6.0)  # m: a written height outside this is no height of a curb or a wall
CM_FROM = 10.0  # a written height without a unit that is this big or bigger is in centimetres (below it, metres)
SAME = 0.01  # m: two texts that differ by less than this say the same height
NUMBER = r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(ml|mt|cm|m)?\b"
THING = r"(?:muro|muretto|cordolo|recinzion\w*|parapetto|ringhiera|argine)"
H_THING = re.compile(rf"\bh\s*{THING}\s*[=:]?\s*{NUMBER}", re.I)  # Hmuro= 1,00 ml.
THING_H = re.compile(rf"\b{THING}\b[^=:]*?\bh\s*[=:]\s*{NUMBER}", re.I | re.S)  # Recinzione di confine ... H= 160 cm


def written_height(text: str) -> float | None:
    """The height in metres a text gives to a wall, a curb or a fence, or None."""
    for pattern in (H_THING, THING_H):
        m = pattern.search(text)
        if m is None:
            continue
        value, unit = float(m.group(1).replace(",", ".")), (m.group(2) or "").lower()
        metres = value / 100.0 if unit == "cm" or (not unit and value >= CM_FROM) else value
        if HEIGHT_RANGE[0] <= metres <= HEIGHT_RANGE[1]:
            return metres
    return None


def written_heights(low: BaseGeometry, words: list) -> tuple[BaseGeometry, list[tuple[BaseGeometry, float, str]], list[str]]:
    """(the low bodies with no written height, [(body, height, the text)] for those that have one, remarks for the report).
    ``words``: anything with ``text``, ``x`` and ``y`` (metres)."""
    bodies = _components(low)
    given: dict[int, list[tuple[float, str]]] = {}
    for w in words:
        height = written_height(w.text)
        if height is None or not bodies:
            continue
        spot = Point(w.x, w.y)
        near = min(range(len(bodies)), key=lambda i: bodies[i].distance(spot))
        if bodies[near].distance(spot) <= REACH:
            given.setdefault(near, []).append((height, w.text.strip()))
    notes: list[str] = []
    written: list[tuple[BaseGeometry, float, str]] = []
    taken: set[int] = set()
    for i, found in sorted(given.items()):
        if len({round(h / SAME) for h, _ in found}) > 1:
            notes.append("Muretti: sullo stesso muro ci sono scritte con altezze diverse ("
                         + "; ".join(f"'{t}'" for _, t in found) + "): lascio l'altezza predefinita, scegli tu con --altezza-muretti.")
            continue
        written.append((bodies[i], found[0][0], found[0][1]))
        taken.add(i)
    if written:
        n = len(written)
        notes.append(f"Muretti: {n} {'muro basso ha' if n == 1 else 'muri bassi hanno'} l'altezza scritta nel disegno vicino a "
                     f"{'lui' if n == 1 else 'loro'} ({', '.join(f'{h:g} m da {t!r}' for _, h, t in written)}): controlla che la scritta "
                     "sia di quel muro.")
    return union(b for i, b in enumerate(bodies) if i not in taken), written, notes
