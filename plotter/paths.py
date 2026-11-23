"""Pure geometry operations on drawings (page-mm).

Everything here takes a :data:`~plotter.types.Drawing` and returns a new one; inputs are never
mutated. Heavy lifting is vectorised with numpy and ``scipy.spatial.cKDTree``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import numpy as np
from scipy.spatial import cKDTree

from plotter.types import Drawing, DrawingStats, Polyline

logger = logging.getLogger(__name__)

_EPS = 1e-9
_TWO_OPT_MAX_PASSES = 8
_KNN_START = 8

Point = tuple[float, float]


# --------------------------------------------------------------------------- measures


def path_length(path: Polyline) -> float:
    """Return the arc length of one path in mm."""
    if len(path) < 2:
        return 0.0
    return float(np.hypot(*np.diff(path, axis=0).T).sum())


def bounds(drawing: Drawing) -> tuple[float, float, float, float] | None:
    """Return ``(xmin, ymin, xmax, ymax)`` of all points, or ``None`` if empty."""
    pts = [p for p in drawing if len(p)]
    if not pts:
        return None
    allp = np.vstack(pts)
    xmin, ymin = allp.min(axis=0)
    xmax, ymax = allp.max(axis=0)
    return (float(xmin), float(ymin), float(xmax), float(ymax))


def stats(drawing: Drawing, start: Point = (0.0, 0.0)) -> DrawingStats:
    """Summarise pen motion.

    Args:
        drawing: Paths in drawing order.
        start: Pen position before the first path (page-mm).

    Returns:
        Path count, pen-down distance and pen-up (travel) distance in mm. Travel includes the
        move from ``start`` to the first path but not the return home.
    """
    paths = [p for p in drawing if len(p)]
    down = sum(path_length(p) for p in paths)
    return {"paths": len(paths), "pen_down_mm": down, "pen_up_mm": _travel(paths, start)}


def _travel(drawing: Sequence[Polyline], start: Point) -> float:
    if not drawing:
        return 0.0
    starts = np.array([p[0] for p in drawing])
    ends = np.array([p[-1] for p in drawing])
    prev = np.vstack([np.asarray(start, np.float64)[None, :], ends[:-1]])
    return float(np.hypot(*(starts - prev).T).sum())


# --------------------------------------------------------------------------- transforms


def fit_to_area(
    drawing: Drawing,
    area_mm: tuple[float, float, float, float],
    keep_aspect: bool = True,
) -> Drawing:
    """Scale and translate ``drawing`` so its bounding box fills ``area_mm``, centred.

    Args:
        drawing: Input paths (any units).
        area_mm: Target rectangle ``(x0, y0, x1, y1)`` in page-mm.
        keep_aspect: Preserve the aspect ratio (uniform scale). If False, stretch to fill.

    Returns:
        A new drawing inside ``area_mm``.

    Raises:
        ValueError: If the target area is empty.
    """
    x0, y0, x1, y1 = area_mm
    aw, ah = x1 - x0, y1 - y0
    if aw <= 0 or ah <= 0:
        raise ValueError(f"target area {area_mm} is empty")
    b = bounds(drawing)
    if b is None:
        return []
    bx0, by0, bx1, by1 = b
    bw, bh = bx1 - bx0, by1 - by0
    sx = aw / bw if bw > _EPS else np.inf
    sy = ah / bh if bh > _EPS else np.inf
    if keep_aspect:
        s = min(sx, sy)
        sx = sy = 1.0 if not np.isfinite(s) else s
    else:
        sx = 1.0 if not np.isfinite(sx) else sx
        sy = 1.0 if not np.isfinite(sy) else sy
    scale = np.array([sx, sy])
    centre_src = np.array([(bx0 + bx1) / 2, (by0 + by1) / 2])
    centre_dst = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
    return [(p - centre_src) * scale + centre_dst for p in drawing]


# --------------------------------------------------------------------------- clean-up


def _rdp_mask(points: Polyline, tol: float) -> np.ndarray:
    """Ramer–Douglas–Peucker keep-mask (iterative, distance to segment, vectorised)."""
    n = len(points)
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        s, e = stack.pop()
        if e - s < 2:
            continue
        a = points[s]
        seg = points[e] - a
        rel = points[s + 1 : e] - a
        seg_len2 = float(seg @ seg)
        if seg_len2 < _EPS:
            d = np.hypot(rel[:, 0], rel[:, 1])
        else:
            t = np.clip(rel @ seg / seg_len2, 0.0, 1.0)
            diff = rel - t[:, None] * seg
            d = np.hypot(diff[:, 0], diff[:, 1])
        i = int(np.argmax(d))
        if d[i] > tol:
            idx = s + 1 + i
            keep[idx] = True
            stack.append((s, idx))
            stack.append((idx, e))
    return keep


def simplify(drawing: Drawing, tol: float) -> Drawing:
    """Simplify every path with Ramer–Douglas–Peucker.

    Args:
        drawing: Input paths.
        tol: Maximum deviation in mm. ``0`` returns a copy.

    Returns:
        Simplified paths (endpoints and sharp corners are always kept).
    """
    if tol <= 0:
        return [p.copy() for p in drawing]
    return [p[_rdp_mask(p, tol)] if len(p) > 2 else p.copy() for p in drawing]


def dedupe_points(drawing: Drawing, min_seg: float) -> Drawing:
    """Drop points that are closer than ``min_seg`` (arc length) to an earlier kept point.

    The first and last points of each path are always kept.

    Args:
        drawing: Input paths.
        min_seg: Minimum segment length in mm.

    Returns:
        Paths with tiny segments merged.
    """
    if min_seg <= 0:
        return [p.copy() for p in drawing]
    out: Drawing = []
    for p in drawing:
        if len(p) <= 2:
            out.append(p.copy())
            continue
        cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(p, axis=0).T))])
        _, first = np.unique(np.floor(cum / min_seg), return_index=True)
        idx = np.union1d(first, [len(p) - 1])
        out.append(p[idx])
    return out


def filter_short(drawing: Drawing, min_len: float) -> Drawing:
    """Remove paths with fewer than 2 points or shorter than ``min_len`` mm."""
    return [p.copy() for p in drawing if len(p) >= 2 and path_length(p) >= min_len]


# --------------------------------------------------------------------------- joining


def join_paths(drawing: Drawing, tol: float) -> Drawing:
    """Merge paths whose endpoints lie within ``tol`` mm of each other.

    Chains are grown greedily from both ends, reversing candidate paths where needed, so a
    figure traced as many fragments becomes few long strokes (fewer pen lifts).

    Args:
        drawing: Input paths.
        tol: Maximum endpoint gap to bridge, in mm. ``0`` joins only exactly-touching ends.

    Returns:
        Joined paths.
    """
    paths = [p for p in drawing if len(p) >= 2]
    n = len(paths)
    if n < 2:
        return [p.copy() for p in paths]
    endpoints = np.vstack([[p[0] for p in paths], [p[-1] for p in paths]])
    tree = cKDTree(endpoints)
    used = np.zeros(n, dtype=bool)
    radius = max(tol, _EPS)
    out: Drawing = []
    for i in range(n):
        if used[i]:
            continue
        used[i] = True
        chain: list[Polyline] = [paths[i]]
        for _ in range(2):  # grow the tail, then (reversed) the head
            while True:
                tail = chain[-1][-1]
                cands = tree.query_ball_point(tail, radius)
                best, best_d = -1, np.inf
                for c in cands:
                    if used[c % n]:
                        continue
                    d = float(np.hypot(*(endpoints[c] - tail)))
                    if d < best_d:
                        best, best_d = c, d
                if best < 0:
                    break
                j = best % n
                used[j] = True
                seg = paths[j][::-1] if best >= n else paths[j]
                chain.append(seg[1:] if best_d < _EPS else seg)
            chain = [c[::-1] for c in reversed(chain)]
        out.append(np.ascontiguousarray(np.vstack(chain)))
    return out


# --------------------------------------------------------------------------- ordering


def order_paths(
    drawing: Drawing,
    method: str = "greedy",
    start: Point = (0.0, 0.0),
    two_opt_max_paths: int = 1500,
) -> Drawing:
    """Reorder (and possibly reverse) paths to reduce pen-up travel.

    Args:
        drawing: Input paths.
        method: ``"greedy"`` (nearest neighbour), ``"greedy+2opt"`` or ``"none"``.
        start: Pen position before the first path (page-mm).
        two_opt_max_paths: Skip the O(n²) 2-opt pass for drawings with more paths.

    Returns:
        Reordered paths. The result never has more travel than the input order.

    Raises:
        ValueError: If ``method`` is unknown.
    """
    if method not in ("greedy", "greedy+2opt", "none"):
        raise ValueError(f"unknown ordering method {method!r}")
    paths = [p for p in drawing if len(p)]
    if method == "none" or len(paths) < 2:
        return [p.copy() for p in paths]
    order = _greedy_order(paths, start)
    if method == "greedy+2opt":
        if len(order) <= two_opt_max_paths:
            order = _two_opt(paths, order, start)
        else:
            logger.info("skipping 2-opt: %d paths > %d", len(order), two_opt_max_paths)
    result = [paths[j][::-1].copy() if rev else paths[j].copy() for j, rev in order]
    if _travel(result, start) > _travel(paths, start):
        return [p.copy() for p in paths]
    return result


def _greedy_order(paths: Sequence[Polyline], start: Point) -> list[tuple[int, bool]]:
    """Nearest-neighbour tour over path endpoints using a periodically rebuilt KD-tree."""
    n = len(paths)
    starts = np.array([p[0] for p in paths])
    ends = np.array([p[-1] for p in paths])
    used = np.zeros(n, dtype=bool)
    pos = np.asarray(start, dtype=np.float64)
    order: list[tuple[int, bool]] = []

    def build(alive: np.ndarray) -> tuple[cKDTree, np.ndarray, np.ndarray]:
        ids = np.concatenate([alive, alive])
        is_end = np.concatenate([np.zeros(len(alive), bool), np.ones(len(alive), bool)])
        pts = np.vstack([starts[alive], ends[alive]])
        return cKDTree(pts), ids, is_end

    tree, ids, is_end = build(np.arange(n))
    tree_alive = n
    while len(order) < n:
        remaining = n - len(order)
        if remaining < tree_alive // 2:
            tree, ids, is_end = build(np.flatnonzero(~used))
            tree_alive = remaining
        total = len(ids)
        k = min(_KNN_START, total)
        choice: tuple[int, bool] | None = None
        while choice is None:
            _, idx = tree.query(pos, k=k)
            for t in np.atleast_1d(idx):
                if t >= total:
                    break
                j = int(ids[t])
                if not used[j]:
                    choice = (j, bool(is_end[t]))
                    break
            if choice is None:
                k = min(k * 4, total)
        j, rev = choice
        used[j] = True
        order.append((j, rev))
        pos = starts[j] if rev else ends[j]
    return order


def _two_opt(
    paths: Sequence[Polyline], order: list[tuple[int, bool]], start: Point
) -> list[tuple[int, bool]]:
    """Improve an oriented visiting order with 2-opt segment reversals (open tour)."""
    n = len(order)
    seq = list(order)
    s = np.array([paths[j][-1] if r else paths[j][0] for j, r in seq])
    e = np.array([paths[j][0] if r else paths[j][-1] for j, r in seq])
    origin = np.asarray(start, dtype=np.float64)
    for _ in range(_TWO_OPT_MAX_PASSES):
        improved = False
        for i in range(n - 1):
            prev = origin if i == 0 else e[i - 1]
            js = np.arange(i + 1, n)
            old_a = np.hypot(*(s[i] - prev))
            new_a = np.hypot(*(e[js] - prev).T)
            nxt = np.minimum(js + 1, n - 1)
            has_next = js < n - 1
            old_b = np.where(has_next, np.hypot(*(s[nxt] - e[js]).T), 0.0)
            new_b = np.where(has_next, np.hypot(*(s[nxt] - s[i]).T), 0.0)
            delta = new_a + new_b - old_a - old_b
            k = int(np.argmin(delta))
            if delta[k] < -1e-9:
                j = int(js[k])
                seq[i : j + 1] = [(pj, not pr) for pj, pr in reversed(seq[i : j + 1])]
                s_blk, e_blk = s[i : j + 1].copy(), e[i : j + 1].copy()
                s[i : j + 1], e[i : j + 1] = e_blk[::-1], s_blk[::-1]
                improved = True
        if not improved:
            break
    return seq
