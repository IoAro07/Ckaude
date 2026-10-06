"""Written texts of the drawing: opening sizes ("120x150", "120" over "150"), sill heights
("ht 100"), room heights ("h 300") and room names.

``read_texts`` collects TEXT, MTEXT, block attributes and multileaders as ``RawText`` (centre
of the written text, in metres, drawing coordinates); ``classify_texts`` turns them into
``Word`` objects with a kind:

  size   two or three numbers: width x height [x sill]   ("120x150", or two stacked lines)
  num    a lone number
  sill   a tagged number: "h 100", "ht 100", "alt. 300" (room height or sill: told apart later)
  name   words of a room name (merged: CAMERA + DA LETTO), spelling fixed when it is close
         to a known room name
  other  anything else, never used
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field

from ezdxf.document import Drawing

from .config import Config

SIZE_RE = re.compile(
    r"^\s*(?:L\s*)?(\d{2,4}(?:[.,]\d+)?)\s*[x×*/\\]\s*(?:H\s*)?(\d{2,4}(?:[.,]\d+)?)"
    r"\s*(?:[x×*/\\]\s*(\d{2,4}(?:[.,]\d+)?))?\s*$", re.I)
LH_RE = re.compile(r"^\s*L\s*\.?\s*(\d{2,4}(?:[.,]\d+)?)\s*[,;\s]\s*H\s*\.?\s*(\d{2,4}(?:[.,]\d+)?)\s*$", re.I)
TAGNUM_RE = re.compile(r"^\s*(h|ht|hf|hd|alt|dt|dav)\s*\.?\s*[=:.\-]?\s*(\d{1,4}(?:[.,]\d+)?)\s*$", re.I)
TAGONLY_RE = re.compile(r"^(h|ht|hf|hd|alt|dt|dav)$", re.I)
NUM_RE = re.compile(r"^\d{1,4}([.,]\d+)?$")
MAX_BLOCK_DEPTH = 3
CHAR_WIDTH = 0.55  # a character is about this many text heights wide
LINE_PITCH = 1.5  # line to line distance of a multi-line text, in text heights

ROOM_WORDS = [
    "SOGGIORNO", "CUCINA", "BAGNO", "CAMERA DA LETTO", "CAMERA", "CAMERETTA", "DISIMPEGNO", "INGRESSO",
    "CORRIDOIO", "STUDIO", "LAVANDERIA", "RIPOSTIGLIO", "BALCONE", "TERRAZZO", "TERRAZZA", "SALA",
    "SALA DA PRANZO", "PRANZO", "ANTIBAGNO", "WC", "CABINA ARMADIO", "ARMADI", "LIVING", "KITCHEN",
    "BEDROOM", "BATHROOM", "HALL", "ENTRANCE", "OFFICE", "SALOTTO", "TAVERNA", "CANTINA", "GARAGE",
    "CAMERA MATRIMONIALE", "CAMERA SINGOLA", "SOGGIORNO CUCINA", "ZONA GIORNO", "ZONA NOTTE", "LOGGIA",
    "SERVIZIO", "SERVIZI", "DISPENSA", "VANO SCALA", "SCALA", "PORTICO", "VERANDA", "CAMERA OSPITI",
]


@dataclass
class RawText:
    """A written text: ``lines`` as typed (top to bottom), centre ``x, y`` of the whole block."""

    lines: list[str]
    x: float
    y: float
    angle: float
    height: float
    layer: str
    source: str = "text"
    width: float = 0.0  # measured length along the text, when known (exploded texts); else estimated

    @property
    def text(self) -> str:
        return " ".join(s.strip() for s in self.lines if s.strip())


@dataclass
class Word:
    text: str
    x: float
    y: float
    angle: float
    height: float
    kind: str  # size | num | sill | tag | name | other
    value: float | tuple | None = None
    tag: str = ""  # for "sill": the letters in front of the number, lower case ("h", "ht"...)
    layer: str = ""
    used: bool = False
    members: list = field(default_factory=list)
    width: float = 0.0

    @property
    def length(self) -> float:
        return self.width or CHAR_WIDTH * self.height * max(len(self.text), 1)

    def dist(self, px: float, py: float) -> float:
        """Distance from a point to the written text, along its own direction (not just its centre)."""
        dx, dy = math.cos(math.radians(self.angle)), math.sin(math.radians(self.angle))
        half = max(self.length / 2 - 0.5 * self.height, 0.0)
        t = max(-half, min(half, (px - self.x) * dx + (py - self.y) * dy))
        return math.hypot(px - (self.x + dx * t), py - (self.y + dy * t))

    def __repr__(self) -> str:
        return f"<{self.kind} {self.text!r} {self.value} @({self.x:.2f},{self.y:.2f}) h={self.height:.2f}>"


def _num(s: str) -> float:
    return float(s.replace(",", "."))


# --- reading ---------------------------------------------------------------------------------

def _centre(x: float, y: float, angle: float, h: float, n_chars: int, n_lines: int, halign: int, valign: int):
    """Centre of a text block anchored at (x, y). halign 0 left, 1 centre, 2 right;
    valign 0 baseline, 1 bottom, 2 middle, 3 top. Width is estimated: no font is available."""
    a = math.radians(angle)
    d, n = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))
    width = CHAR_WIDTH * h * max(n_chars, 1)
    block = h + (n_lines - 1) * LINE_PITCH * h
    ax = {0: 0.5, 1: 0.0, 2: -0.5}.get(halign, 0.0) * width
    ay = {0: 0.35 * h, 1: 0.5 * block, 2: 0.0, 3: -0.5 * block}.get(valign, 0.0)
    return x + d[0] * ax + n[0] * ay, y + d[1] * ax + n[1] * ay


def _text_entity(e, layer: str, scale: float, source: str = "text") -> RawText | None:
    t = e.dxftype()
    default_h = 0.1 / scale  # a text without a height: 10 cm
    try:
        if t in ("TEXT", "ATTRIB"):
            raw = e.dxf.text or ""
            if not raw.strip():
                return None
            ha, va = int(e.dxf.get("halign", 0)), int(e.dxf.get("valign", 0))
            h = float(e.dxf.height)
            ang = float(e.dxf.get("rotation", 0))
            if (ha or va) and e.dxf.hasattr("align_point"):
                p, q = e.dxf.align_point, e.dxf.insert
                if ha in (3, 5):  # aligned / fit: between the two points
                    cx, cy = (p.x + q.x) / 2, (p.y + q.y) / 2
                elif ha == 4:  # middle
                    cx, cy = p.x, p.y
                else:
                    cx, cy = _centre(p.x, p.y, ang, h, len(raw), 1, ha, va)
            else:
                p = e.dxf.insert
                cx, cy = _centre(p.x, p.y, ang, h, len(raw), 1, 0, 0)
            return RawText([raw], cx * scale, cy * scale, ang, h * scale, layer, source)
        if t == "MTEXT":
            lines = [s for s in e.plain_text().split("\n") if s.strip()]
            if not lines:
                return None
            p = e.dxf.insert
            h = float(e.dxf.get("char_height", 0) or 0)
            ang = float(e.dxf.get("rotation", 0))
            if e.dxf.hasattr("text_direction"):
                dd = e.dxf.text_direction
                ang = math.degrees(math.atan2(dd.y, dd.x))
            att = int(e.dxf.get("attachment_point", 1))
            ha = {1: 0, 2: 1, 3: 2}[(att - 1) % 3 + 1]
            va = {0: 3, 1: 2, 2: 1}[(att - 1) // 3]
            hh = h if h > 0 else default_h
            cx, cy = _centre(p.x, p.y, ang, hh, max(len(s) for s in lines), len(lines), ha, va)
            return RawText(lines, cx * scale, cy * scale, ang, hh * scale, layer, source)
        if t == "MULTILEADER":
            ctx = e.context
            txt = ctx.mtext.default_content if ctx and ctx.mtext else ""
            lines = [s for s in re.split(r"\\P|\n", txt) if s.strip()]
            if not lines:
                return None
            p = ctx.mtext.insert
            hh = float(getattr(ctx, "char_height", 0) or 0) or default_h
            return RawText(lines, p.x * scale, p.y * scale, 0.0, hh * scale, layer, "mleader")
    except Exception:  # a damaged text is not worth stopping for
        return None
    return None


def read_texts(doc: Drawing, cfg: Config, scale: float, area=None) -> list[RawText]:
    """All texts of the modelspace (and inside blocks), centres in metres. ``scale``: metres per
    drawing unit. ``area``: (xmin, ymin, xmax, ymax) in drawing units; texts outside are dropped."""
    out: list[RawText] = []

    def visible(layer: str) -> bool:
        if cfg.include_hidden or not doc.layers.has_entry(layer):
            return True
        entry = doc.layers.get(layer)
        return not (entry.is_off() or entry.is_frozen())

    def walk(entities, depth: int, inherit: str | None) -> None:
        for e in entities:
            kind = e.dxftype()
            layer = e.dxf.layer
            if layer == "0" and inherit:
                layer = inherit
            if not visible(layer):
                continue
            if kind == "INSERT":
                for att in e.attribs:
                    rt = _text_entity(att, layer, scale, "attrib")
                    if rt:
                        out.append(rt)
                if depth < MAX_BLOCK_DEPTH:
                    try:
                        walk(list(e.virtual_entities()), depth + 1, layer)
                    except Exception:
                        pass
            elif kind in ("TEXT", "MTEXT", "MULTILEADER"):
                rt = _text_entity(e, layer, scale)
                if rt:
                    out.append(rt)

    walk(doc.modelspace(), 0, None)
    if area:
        margin = 1.0  # a label may sit just outside the plan's crop: keep up to a metre around it
        x0, y0, x1, y1 = (v * scale for v in area[:4])
        out = [t for t in out if x0 - margin <= t.x <= x1 + margin and y0 - margin <= t.y <= y1 + margin]
    return out


# --- classification --------------------------------------------------------------------------

def _split(rt: RawText) -> list[RawText]:
    """One text per line, except lines that are all numbers (a stacked size) which stay together."""
    if len(rt.lines) < 2:
        return [rt]
    if all(NUM_RE.match(s.strip()) for s in rt.lines) and len(rt.lines) <= 3:
        return [rt]
    a = math.radians(rt.angle)
    nx, ny = -math.sin(a), math.cos(a)
    n = len(rt.lines)
    parts = []
    for i, line in enumerate(rt.lines):
        off = ((n - 1) / 2 - i) * LINE_PITCH * rt.height
        parts.append(RawText([line], rt.x + nx * off, rt.y + ny * off, rt.angle, rt.height, rt.layer, rt.source))
    return parts


def classify_texts(texts: list[RawText]) -> list[Word]:
    words: list[Word] = []
    tags, nums = [], []
    for rt in (p for r in texts for p in _split(r)):
        s = rt.text.strip()
        if not s:
            continue
        h = rt.height if rt.height > 0 else 0.1
        if len(rt.lines) >= 2:  # stacked numbers: width over height (over sill)
            vals = tuple(_num(x.strip()) for x in rt.lines)
            words.append(Word(s, rt.x, rt.y, rt.angle, h, "size", vals, layer=rt.layer))
            continue
        m = TAGNUM_RE.match(s)
        if m:
            words.append(Word(s, rt.x, rt.y, rt.angle, h, "sill", _num(m.group(2)), m.group(1).lower(), rt.layer,
                              width=rt.width))
            continue
        m = SIZE_RE.match(s) or LH_RE.match(s)
        if m:
            words.append(Word(s, rt.x, rt.y, rt.angle, h, "size", tuple(_num(g) for g in m.groups() if g),
                              layer=rt.layer))
            continue
        if TAGONLY_RE.match(s):
            w = Word(s, rt.x, rt.y, rt.angle, h, "tag", None, s.lower(), rt.layer, width=rt.width)
            tags.append(w)
            words.append(w)
            continue
        if NUM_RE.match(s):
            w = Word(s, rt.x, rt.y, rt.angle, h, "num", _num(s), layer=rt.layer, width=rt.width)
            nums.append(w)
            words.append(w)
            continue
        kind = "name" if sum(c.isalpha() for c in s) >= 2 else "other"
        words.append(Word(s, rt.x, rt.y, rt.angle, h, kind, layer=rt.layer, width=rt.width))
    _join_tags(tags, nums)
    words += _stack_numbers(nums)
    return [w for w in words if not (w.kind == "num" and w.used)]


def _join_tags(tags: list[Word], nums: list[Word]) -> None:
    """"h" and "100" typed as two texts side by side become one tagged number."""
    for tg in tags:
        dx, dy = math.cos(math.radians(tg.angle)), math.sin(math.radians(tg.angle))
        best, best_along = None, math.inf
        for nw in nums:
            if nw.used or abs(((nw.angle - tg.angle + 180) % 360) - 180) > 8:
                continue
            vx, vy = nw.x - tg.x, nw.y - tg.y
            along = vx * dx + vy * dy
            off = abs(dx * vy - dy * vx)
            big = max(tg.height, nw.height)
            if 0 < along <= 4.5 * big and off <= 0.6 * big and abs(nw.height - tg.height) <= 0.4 * big:
                if along < best_along:
                    best, best_along = nw, along
        if best is not None:
            tg.kind, tg.value = "sill", best.value
            tg.text = f"{tg.text} {best.text}"
            tg.x, tg.y = (tg.x + best.x) / 2, (tg.y + best.y) / 2
            tg.members, best.used = [best], True


def _stack_numbers(nums: list[Word]) -> list[Word]:
    """Two numbers one above the other, as written beside an opening ("120" over "150"), are a size:
    width over height, in the text's own reading direction."""
    pairs = []
    for i, a in enumerate(nums):
        for b in nums[i + 1:]:
            if a.used or b.used or abs(((a.angle - b.angle + 180) % 360) - 180) > 8:
                continue
            ang = math.radians(a.angle)
            dx, dy = math.cos(ang), math.sin(ang)
            vx, vy = b.x - a.x, b.y - a.y
            along, down = vx * dx + vy * dy, -(vx * -dy + vy * dx)  # down: against the text's "up" (-sin, cos)
            h = max(a.height, b.height)
            if abs(a.height - b.height) > 0.4 * h or abs(along) > 0.6 * max(a.length, b.length):
                continue
            if 0.9 * h <= abs(down) <= 3.0 * h:
                pairs.append((abs(down) + abs(along), a, b, down))
    out = []
    for _, a, b, down in sorted(pairs, key=lambda t: t[0]):
        if a.used or b.used:
            continue
        top, bottom = (a, b) if down > 0 else (b, a)  # down > 0: b is below a
        top.used = bottom.used = True
        out.append(Word(f"{top.text} {bottom.text}", (a.x + b.x) / 2, (a.y + b.y) / 2, a.angle,
                        max(a.height, b.height), "size", (top.value, bottom.value), layer=a.layer,
                        width=max(a.length, b.length)))
    return out


def merge_name_words(words: list[Word]) -> list[Word]:
    """CAMERA + DA + LETTO -> "CAMERA DA LETTO": words on the same line, close together."""
    names = sorted((w for w in words if w.kind == "name"), key=lambda w: (round(w.y / 0.05), w.x))
    others = [w for w in words if w.kind != "name"]
    used: set[int] = set()
    merged: list[Word] = []
    for i, w in enumerate(names):
        if i in used:
            continue
        used.add(i)
        group, cur = [w], w
        dx, dy = math.cos(math.radians(w.angle)), math.sin(math.radians(w.angle))
        while True:
            nxt = None
            for j, v in enumerate(names):
                if j in used or abs(v.angle - w.angle) > 5 or abs(v.height - w.height) > 0.3 * w.height:
                    continue
                vx, vy = v.x - cur.x, v.y - cur.y
                along, off = vx * dx + vy * dy, abs(dx * vy - dy * vx)
                gap = along - cur.length / 2 - v.length / 2
                if along > 0 and off < 0.5 * w.height and gap < 1.6 * w.height and (nxt is None or along < nxt[0]):
                    nxt = (along, j, v)
            if nxt is None:
                break
            used.add(nxt[1])
            group.append(nxt[2])
            cur = nxt[2]
        span = max(abs((g.x - group[0].x) * dx + (g.y - group[0].y) * dy) + g.length / 2 for g in group) \
            + group[0].length / 2
        merged.append(Word(" ".join(g.text for g in group), sum(g.x for g in group) / len(group),
                           sum(g.y for g in group) / len(group), w.angle, w.height, "name",
                           layer=w.layer, members=group, width=span if len(group) > 1 else group[0].width))
    return others + merged


def fix_room_name(text: str) -> tuple[str, bool]:
    """(spelling, known): a name close to a typical room name takes its spelling."""
    up = text.upper().strip()
    match = difflib.get_close_matches(up, ROOM_WORDS, n=1, cutoff=0.8)
    if match:
        return match[0].capitalize(), True
    return text.strip().capitalize(), False


def read_words(doc: Drawing, cfg: Config, scale: float) -> list[Word]:
    texts = read_texts(doc, cfg, scale, cfg.area)
    if cfg.vector_text:
        from .vtext import read_exploded_texts  # numpy only, but not needed unless the drawing has them

        texts += read_exploded_texts(doc, cfg, scale)
    words = merge_name_words(classify_texts(texts))
    for w in words:
        if w.kind == "name":
            w.text, w.value = fix_room_name(w.text)
    return words
