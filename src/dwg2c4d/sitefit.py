"""Where the converted plan lies in another view of the sheet (the survey with the contour lines).

A sheet often draws the survey (contour lines, the lot's boundaries, the road) as a view of its own where the house is not
drawn at all, and the plan of the project in another place, copied into a second site plan that shares the lot's lines with
the survey. The plan is then laid on the survey in two steps, each by the lines the two drawings have in common: every
long line of one drawing votes for the shift that would put it on a line of the other one of the same length and
direction, and the shift with most line length behind it wins (a copy of a plan, turned by a quarter, is found too: the
four quarter turns are tried). Nothing but straight lines of equal size is compared, so the contour lines (which belong to
one drawing only) and the texts do not take part.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# --- the vote ---
MIN_LINE = 1.0  # m: a line shorter than this takes no part (the pieces of curves, hatches and furniture)
VOTERS = 400  # the longest lines of the drawing to be placed vote...
PARTNERS = 4000  # ... each for the lines of the other drawing (the longest) of its length and direction
LENGTH_TOL = 0.08  # m: two lines are of the same length within this
ANGLE_TOL = 0.8  # degrees: ... and of the same direction within this
CELL = 0.20  # m: votes this close are one shift
# --- what is believed ---
COPY_SHARE = 0.4  # a plan lies on its copy when this share of its line length (long lines) lands on lines of the copy
COMMON_LENGTH = 60.0  # m: two drawings of the same lot share at least this length of line...
PEAK_RATIO = 3.0  # ... and the best shift has this many times more of it behind it than any other
MIN_VOTERS = 6  # ... from at least this many lines

_PACK = 1 << 31
_SHIFT = 1 << 30


@dataclass
class Placement:
    """The move that carries the plan onto the view of the contour lines: turned by ``turns`` quarters (counter-clockwise,
    about the origin), then moved by ``shift`` (m). ``views`` is the chain of views it goes through, ``common`` the length
    of line (m) that the weakest step stands on."""

    turns: int = 0
    shift: tuple[float, float] = (0.0, 0.0)
    views: tuple[int, ...] = ()
    common: float = 0.0

    def to_sheet(self, xy: np.ndarray) -> np.ndarray:
        """Points of the plan (N x 2, m) in the frame of the contours."""
        c, s = (1, 0, -1, 0)[self.turns], (0, 1, 0, -1)[self.turns]
        return np.stack([c * xy[:, 0] - s * xy[:, 1] + self.shift[0], s * xy[:, 0] + c * xy[:, 1] + self.shift[1]], axis=1)

    def to_plan(self, xy: np.ndarray) -> np.ndarray:
        """Points of the contours (N x 2, m) in the frame of the plan."""
        c, s = (1, 0, -1, 0)[self.turns], (0, 1, 0, -1)[self.turns]
        x, y = xy[:, 0] - self.shift[0], xy[:, 1] - self.shift[1]
        return np.stack([c * x + s * y, -s * x + c * y], axis=1)

    def then(self, other: "Placement") -> "Placement":
        """This move, then ``other``."""
        c, s = (1, 0, -1, 0)[other.turns], (0, 1, 0, -1)[other.turns]
        shift = (c * self.shift[0] - s * self.shift[1] + other.shift[0], s * self.shift[0] + c * self.shift[1] + other.shift[1])
        return Placement((self.turns + other.turns) % 4, shift, self.views + other.views, min(self.common, other.common))


def _features(seg: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Length, direction (degrees, 0 to 180) and middle of each segment (N x 4)."""
    dx, dy = seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1]
    return np.hypot(dx, dy), np.degrees(np.arctan2(dy, dx)) % 180.0, (seg[:, 0] + seg[:, 2]) / 2.0, (seg[:, 1] + seg[:, 3]) / 2.0


def _peak(votes: np.ndarray, who: np.ndarray, length: np.ndarray) -> tuple[tuple[float, float], float, int] | None:
    """The shift most line length stands on: (shift, length, number of lines). Each line counts once per shift; two
    grids of cells half a cell apart, so that a shift cut by the edge of a cell of one lies whole in a cell of the other."""
    best = None
    for half in (0.0, 0.5):
        cell = np.floor(votes / CELL + half).astype(np.int64)
        key = (cell[:, 0] + _SHIFT) * _PACK + cell[:, 1] + _SHIFT
        pairs = np.unique(np.stack([key, who], axis=1), axis=0)  # (cell, line): a line votes once for a cell
        cells, inverse = np.unique(pairs[:, 0], return_inverse=True)
        score = np.bincount(inverse, weights=length[pairs[:, 1]])
        k = int(score.argmax())
        if best is None or score[k] > best[0]:
            members = key == cells[k]
            best = (float(score[k]), tuple(np.median(votes[members], axis=0)), int((inverse == k).sum()))
    return (best[1], best[0], best[2]) if best else None


def vote(mine: np.ndarray, there: np.ndarray, turns: int) -> tuple[tuple[float, float], float, int, float] | None:
    """The best shift that carries the lines ``mine`` (N x 4, metres), turned by ``turns`` quarters, onto lines of ``there``
    of the same length and direction: (shift, the length of line it carries, the number of lines, the ratio of that length to
    what the best other shift carries), or None when no line has a partner."""
    la, aa, xa, ya = _features(mine)
    lb, ab, xb, yb = _features(there)
    c, s = (1, 0, -1, 0)[turns], (0, 1, 0, -1)[turns]
    aa = (aa + 90.0 * turns) % 180.0
    xa, ya = c * xa - s * ya, s * xa + c * ya
    order = np.argsort(lb)
    low = np.searchsorted(lb[order], la - LENGTH_TOL)
    high = np.searchsorted(lb[order], la + LENGTH_TOL)
    votes, who = [], []
    for i in range(len(la)):
        j = order[low[i]:high[i]]
        if len(j) == 0:
            continue
        d = np.abs(ab[j] - aa[i])
        j = j[np.minimum(d, 180.0 - d) <= ANGLE_TOL]
        if len(j):
            votes.append(np.stack([xb[j] - xa[i], yb[j] - ya[i]], axis=1))
            who.append(np.full(len(j), i))
    if not votes:
        return None
    votes, who = np.concatenate(votes), np.concatenate(who)
    first = _peak(votes, who, la)
    if first is None:
        return None
    far = np.hypot(votes[:, 0] - first[0][0], votes[:, 1] - first[0][1]) > 4 * CELL
    second = _peak(votes[far], who[far], la) if far.any() else None
    return first[0], first[1], first[2], first[1] / max(second[1] if second else 0.0, 1e-9)


def long_lines(soup, scale: float, box: tuple[float, float, float, float], skip: set[str], count: int) -> np.ndarray:
    """The ``count`` longest straight lines (at least MIN_LINE long) whose middle lies in ``box`` (drawing units), outside
    the layers ``skip``, in metres (N x 4)."""
    seg = soup.seg
    mx, my = (seg[:, 0] + seg[:, 2]) / 2.0, (seg[:, 1] + seg[:, 3]) / 2.0
    keep = (mx >= box[0]) & (mx <= box[2]) & (my >= box[1]) & (my <= box[3])
    if skip:
        keep &= ~np.isin(np.asarray(soup.layers, dtype=object)[soup.seg_layer], list(skip))
    seg = seg[keep] * scale
    length = np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1])
    seg, length = seg[length >= MIN_LINE], length[length >= MIN_LINE]
    return seg[np.argsort(-length)[:count]]


def _best_turn(mine: np.ndarray, there: np.ndarray) -> tuple[int, tuple[float, float], float, int, float] | None:
    """The quarter turn (0 to 3) and shift that carry ``mine`` onto ``there`` with most line length behind them."""
    found = []
    for turns in range(4):
        v = vote(mine, there, turns)
        if v is not None:
            found.append((turns, *v))
    return max(found, key=lambda f: f[2]) if found else None


def register(analysis, scale: float, plan_view, contour_view, skip: set[str]) -> Placement | None:
    """The move that lays the plan (``plan_view``) on the view of the contour lines (``contour_view``), or None.

    Directly when the contour view draws the house as well (a copy of the plan: ``COPY_SHARE`` of the lines of the plan
    land on lines of the view); else through a third view that holds a copy of the plan and shares the lines of the lot
    with the contour view (``COMMON_LENGTH`` of line in one place, ``PEAK_RATIO`` times more than anywhere else). The
    best supported way wins. ``skip``: the layers of the contour lines, which are no help."""
    soup = analysis.soup
    mine = long_lines(soup, scale, plan_view.bbox, skip, VOTERS)
    if len(mine) < MIN_VOTERS:
        return None
    total = float(np.hypot(mine[:, 2] - mine[:, 0], mine[:, 3] - mine[:, 1]).sum())
    target = long_lines(soup, scale, contour_view.bbox, skip, PARTNERS)
    ways: list[Placement] = []
    direct = _best_turn(mine, target) if len(target) else None
    if direct is not None and direct[2] >= COPY_SHARE * total and direct[3] >= MIN_VOTERS:
        ways.append(Placement(direct[0], direct[1], (plan_view.id, contour_view.id), direct[2]))
    for via in analysis.views:
        if via is plan_view or via is contour_view:
            continue
        there = long_lines(soup, scale, via.bbox, skip, PARTNERS)
        first = _best_turn(mine, there) if len(there) else None
        if first is None or first[2] < COPY_SHARE * total or first[3] < MIN_VOTERS:
            continue
        second = _best_turn(long_lines(soup, scale, via.bbox, skip, VOTERS), target) if len(target) else None
        if second is None or second[2] < COMMON_LENGTH or second[4] < PEAK_RATIO or second[3] < MIN_VOTERS:
            continue
        ways.append(Placement(first[0], first[1], (plan_view.id, via.id), first[2]).then(
            Placement(second[0], second[1], (contour_view.id,), second[2])))
    short = [w for w in ways if len(w.views) == 2]  # the house drawn in the contour view itself needs no go-between
    return max(short or ways, key=lambda w: w.common) if ways else None
