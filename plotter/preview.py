"""Preview rendering (PNG) and plot-time estimation.

Rendering uses matplotlib's object API with the Agg canvas (no pyplot global state), so it is
safe to call from the CLI, tests and the Streamlit app.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from plotter.config import Config
from plotter.gcode import Move, interpret, machine_to_page
from plotter.paths import stats as drawing_stats
from plotter.types import Drawing

logger = logging.getLogger(__name__)

DEFAULT_DPI = 150
CORNER_STOP_DEG = 45.0
"""Direction changes sharper than this are treated as a full stop in the time estimate."""

_INK = "#1b1f3b"
_TRAVEL = "#e58e8e"
_PAGE_EDGE = "#8a8a8a"
_MARGIN = "#b8b8b8"
_PAD_MM = 8.0


# --------------------------------------------------------------------------- time estimate


def _trapezoid_time(length: float, v: float, a: float) -> float:
    """Time to travel ``length`` from rest to rest with max speed ``v`` and accel ``a``."""
    if length <= 0:
        return 0.0
    d_acc = v * v / (2 * a)
    if length >= 2 * d_acc:
        return 2 * v / a + (length - 2 * d_acc) / v
    return 2 * math.sqrt(length / a)


def _runs(moves: Sequence[Move]) -> list[tuple[float, float]]:
    """Group moves into continuous runs ``(length, feed_mm_min)`` split at sharp corners."""
    runs: list[tuple[float, float]] = []
    cos_limit = math.cos(math.radians(CORNER_STOP_DEG))
    cur_len, cur_feed = 0.0, 0.0
    prev: Move | None = None
    for m in moves:
        length = m.length
        if length == 0:
            continue
        joined = False
        if prev is not None and m.rapid == prev.rapid and m.feed_mm_min == prev.feed_mm_min:
            ax, ay = prev.x1 - prev.x0, prev.y1 - prev.y0
            bx, by = m.x1 - m.x0, m.y1 - m.y0
            cos = (ax * bx + ay * by) / (prev.length * length)
            contiguous = prev.x1 == m.x0 and prev.y1 == m.y0
            joined = contiguous and cos >= cos_limit
        if joined:
            cur_len += length
        else:
            if cur_len > 0:
                runs.append((cur_len, cur_feed))
            cur_len, cur_feed = length, m.feed_mm_min
        prev = m
    if cur_len > 0:
        runs.append((cur_len, cur_feed))
    return runs


def estimate_time(lines: Sequence[str], cfg: Config) -> float:
    """Estimate how long a job takes, in seconds.

    Model: moves are grouped into runs that the GRBL planner can take without stopping
    (direction change ≤ 45°); each run follows a trapezoidal velocity profile with
    ``motion.accel_mm_s2``. G0 uses ``travel_feed_mm_min``, G1 the programmed feed. Pen
    settle dwells (``G4``) are added on top.

    Args:
        lines: G-code lines.
        cfg: Configuration.

    Returns:
        Estimated duration in seconds.
    """
    moves, dwell = interpret(lines, cfg)
    a = cfg.motion.accel_mm_s2
    return dwell + sum(_trapezoid_time(length, f / 60.0, a) for length, f in _runs(moves))


# --------------------------------------------------------------------------- rendering


def _segments_from_drawing(
    drawing: Drawing, start: tuple[float, float]
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    strokes = [p for p in drawing if len(p) >= 1]
    ink = [p if len(p) > 1 else np.vstack([p, p]) for p in strokes]
    travel: list[np.ndarray] = []
    pos = np.asarray(start, dtype=np.float64)
    for p in strokes:
        travel.append(np.vstack([pos, p[0]]))
        pos = p[-1]
    return ink, travel


def _segments_from_gcode(
    lines: Sequence[str], cfg: Config
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    moves, _ = interpret(lines, cfg)
    ink: list[np.ndarray] = []
    travel: list[np.ndarray] = []
    for m in moves:
        seg = machine_to_page([[m.x0, m.y0], [m.x1, m.y1]], cfg)
        (ink if m.pen_down else travel).append(seg)
    return ink, travel


def render(
    source: Drawing | Sequence[str],
    out_png: str | Path,
    cfg: Config,
    *,
    title: str | None = None,
    dpi: int = DEFAULT_DPI,
    show_travel: bool = True,
) -> Path:
    """Render a drawing or a G-code job to a PNG preview of the A4 sheet.

    Shows the page outline, the margin box, pen-down strokes (solid, dark) and travel moves
    (thin, dashed, light red).

    Args:
        source: A :data:`~plotter.types.Drawing` (page-mm) or a list of G-code lines.
        out_png: Output path; parent directories are created.
        cfg: Configuration.
        title: Optional title; a stats line is always added.
        dpi: Output resolution.
        show_travel: Draw pen-up moves.

    Returns:
        The written path.
    """
    is_gcode = len(source) > 0 and isinstance(source[0], str)
    if is_gcode:
        lines = [str(s) for s in source]
        ink, travel = _segments_from_gcode(lines, cfg)
        down = sum(float(np.hypot(*np.diff(s, axis=0).T).sum()) for s in ink)
        up = sum(float(np.hypot(*np.diff(s, axis=0).T).sum()) for s in travel)
        n_strokes = len(ink)
        secs = estimate_time(lines, cfg)
        info = f"{down / 1000:.2f} m pen-down · {up / 1000:.2f} m travel · ~{secs / 60:.1f} min"
    else:
        drawing = [np.asarray(p, dtype=np.float64) for p in source]
        ink, travel = _segments_from_drawing(drawing, cfg.home_in_page_mm)
        st = drawing_stats(drawing, cfg.home_in_page_mm)
        n_strokes = st["paths"]
        info = (
            f"{st['paths']} paths · {st['pen_down_mm'] / 1000:.2f} m pen-down · "
            f"{st['pen_up_mm'] / 1000:.2f} m travel"
        )

    pw, ph = cfg.machine.page_size_mm
    x0, y0, x1, y1 = cfg.printable_area
    hx, hy = cfg.home_in_page_mm
    xmin, xmax = min(0.0, hx) - _PAD_MM, pw + _PAD_MM
    ymin, ymax = -_PAD_MM, max(ph, hy) + _PAD_MM
    fig = Figure(figsize=((xmax - xmin) / 25.4, (ymax - ymin + 14) / 25.4))
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0.0, 0.0, 1.0, (ymax - ymin) / (ymax - ymin + 14)))
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymax, ymin)  # page frame: +y down
    ax.set_aspect("equal")
    ax.axis("off")
    ax.add_patch(Rectangle((0, 0), pw, ph, fill=True, fc="white", ec=_PAGE_EDGE, lw=1.0))
    ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, ec=_MARGIN, lw=0.6, ls="--"))
    ax.plot([hx], [hy], marker="o", ms=3, color=_PAGE_EDGE)
    if show_travel and travel:
        ax.add_collection(
            LineCollection(travel, colors=_TRAVEL, linewidths=0.35, linestyles=(0, (3, 3)))
        )
    if ink:
        ax.add_collection(LineCollection(ink, colors=_INK, linewidths=0.6, capstyle="round"))
    heading = f"{title} — {info}" if title else info
    fig.text(0.5, 1 - 7 / (ymax - ymin + 14), heading, ha="center", va="center", fontsize=7)
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.set_facecolor("#f4f4f4")
    fig.savefig(out, dpi=dpi, facecolor=fig.get_facecolor())
    logger.info("rendered preview of %d strokes to %s", n_strokes, out)
    return out
