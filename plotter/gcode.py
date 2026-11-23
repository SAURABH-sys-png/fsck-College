"""Drawing → GRBL G-code, plus the safety validator.

This is the **only** module that knows the machine frame. The page→machine transform is
(CLAUDE.md §3)::

    X = ox + x
    Y = oy + (page_height − y)

Every job produced by :func:`to_gcode` is passed through :func:`validate`, which raises
:class:`~plotter.errors.OutOfBoundsError` for any coordinate outside the bed and
:class:`~plotter.errors.GcodeError` for unsafe sequences (G0 with the pen down, arcs, relative
mode, over-long lines). Never bypass it.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from plotter import __version__
from plotter.config import Config
from plotter.errors import GcodeError, OutOfBoundsError
from plotter.types import Drawing, DrawingStats

logger = logging.getLogger(__name__)

MAX_LINE_CHARS = 70
"""Every emitted line is shorter than this (GRBL's line buffer is 80 bytes)."""

_WORD_RE = re.compile(r"([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
_COMMENT_RE = re.compile(r"\([^)]*\)|;.*$")
_BOUNDS_EPS = 1e-6


def _norm(cmd: str) -> str:
    """Upper-case and collapse whitespace so pen commands compare reliably."""
    return " ".join(cmd.upper().split())


FloatArray = npt.NDArray[np.float64]


# --------------------------------------------------------------------------- transform


def page_to_machine(points: npt.ArrayLike, cfg: Config) -> FloatArray:
    """Convert page-frame mm (origin top-left, +y down) to machine mm (origin home, +Y away).

    Args:
        points: ``(N, 2)`` page coordinates.
        cfg: Configuration (page origin and height).

    Returns:
        ``(N, 2)`` machine coordinates.
    """
    p = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    ox, oy = cfg.machine.page_origin_mm
    h = cfg.machine.page_size_mm[1]
    return np.column_stack([ox + p[:, 0], oy + (h - p[:, 1])])


def machine_to_page(points: npt.ArrayLike, cfg: Config) -> FloatArray:
    """Inverse of :func:`page_to_machine`."""
    m = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    ox, oy = cfg.machine.page_origin_mm
    h = cfg.machine.page_size_mm[1]
    return np.column_stack([m[:, 0] - ox, h - (m[:, 1] - oy)])


# --------------------------------------------------------------------------- generation


def fmt(value: float, decimals: int) -> str:
    """Format a number compactly: fixed decimals, trailing zeros stripped, no ``-0``."""
    s = f"{value:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def to_gcode(drawing: Drawing, cfg: Config) -> list[str]:
    """Generate a validated GRBL job for ``drawing``.

    Structure: header (``G21 G90 G17 G94``, pen up, settle) → per path (``G0`` to start, pen
    down, settle, ``G1 … F`` through the points, pen up, settle) → footer (pen up, ``G0 X0
    Y0``, ``M5``). Points that round to the same coordinate are collapsed; a path that
    collapses to one point is plotted as a dot.

    Args:
        drawing: Paths in page-mm, already ordered.
        cfg: Configuration.

    Returns:
        G-code lines without line endings.

    Raises:
        OutOfBoundsError: If any point lands outside the bed.
        GcodeError: If the result is otherwise unsafe (should never happen).
    """
    d = cfg.motion.coord_decimals
    up, down = cfg.pen.up_cmd.strip(), cfg.pen.down_cmd.strip()
    dwell = f"G4 P{fmt(cfg.pen.settle_s, 3)}"
    feed = fmt(cfg.motion.draw_feed_mm_min, 0)
    lines = ["G21", "G90", "G17", "G94", up, dwell]
    for path in drawing:
        if len(path) == 0:
            continue
        m = np.round(page_to_machine(path, cfg), d)
        if len(m) > 1:
            changed = np.any(np.diff(m, axis=0) != 0, axis=1)
            m = m[np.concatenate([[True], changed])]
        xs = [fmt(v, d) for v in m[:, 0]]
        ys = [fmt(v, d) for v in m[:, 1]]
        lines += [f"G0 X{xs[0]} Y{ys[0]}", down, dwell]
        for k in range(1, len(m)):
            lines.append(f"G1 X{xs[k]} Y{ys[k]}" + (f" F{feed}" if k == 1 else ""))
        lines += [up, dwell]
    lines += [up, dwell, "G0 X0 Y0", "M5"]
    validate(lines, cfg)
    return lines


# --------------------------------------------------------------------------- parsing


def strip_comment(line: str) -> str:
    """Remove ``( … )`` and ``; …`` comments and surrounding whitespace."""
    return _COMMENT_RE.sub("", line).strip()


def parse_words(line: str) -> list[tuple[str, float]]:
    """Split a comment-free G-code line into ``(letter, value)`` words.

    Raises:
        GcodeError: If the line contains anything that is not a word.
    """
    text = line.upper()
    words = [(m.group(1), float(m.group(2))) for m in _WORD_RE.finditer(text)]
    leftover = _WORD_RE.sub("", text).strip()
    if leftover:
        raise GcodeError(f"cannot parse G-code {line!r} (unexpected {leftover!r})")
    return words


@dataclass(frozen=True)
class Move:
    """One straight move in machine mm."""

    x0: float
    y0: float
    x1: float
    y1: float
    rapid: bool
    pen_down: bool
    feed_mm_min: float

    @property
    def length(self) -> float:
        """Euclidean length in mm."""
        return float(np.hypot(self.x1 - self.x0, self.y1 - self.y0))


@dataclass
class _State:
    x: float = 0.0
    y: float = 0.0
    pen_down: bool = False
    pen_known: bool = False
    motion: int = 0
    feed: float = 0.0


def interpret(lines: Sequence[str], cfg: Config) -> tuple[list[Move], float]:
    """Replay ``lines`` and return the moves and the total dwell time.

    This is the shared parser behind :func:`validate`, the preview renderer and the time
    estimate. It enforces every structural safety rule while it goes.

    Args:
        lines: G-code lines (comments allowed).
        cfg: Configuration (bed size, pen commands, feeds).

    Returns:
        ``(moves, dwell_seconds)``.

    Raises:
        OutOfBoundsError: If a target lies outside the bed.
        GcodeError: For unsafe or unsupported G-code.
    """
    bw, bh = cfg.machine.bed_size_mm
    up = _norm(cfg.pen.up_cmd)
    down = _norm(cfg.pen.down_cmd)
    st = _State(feed=cfg.motion.draw_feed_mm_min)
    moves: list[Move] = []
    dwell = 0.0
    for n, raw in enumerate(lines, 1):
        if len(raw.rstrip("\r\n")) >= MAX_LINE_CHARS:
            raise GcodeError(f"line {n} is {len(raw)} chars; GRBL lines must be < 70: {raw!r}")
        line = strip_comment(raw)
        if not line:
            continue
        norm = _norm(line)
        if norm == up:
            st.pen_down, st.pen_known = False, True
            continue
        if norm == down:
            st.pen_down, st.pen_known = True, True
            continue
        if norm.startswith("$"):
            raise GcodeError(f"line {n}: '$' system commands are not allowed in a job: {raw!r}")
        words = parse_words(norm)
        gs = [int(v) for k, v in words if k == "G" and v.is_integer()]
        ms = [int(v) for k, v in words if k == "M" and v.is_integer()]
        vals = {k: v for k, v in words if k in "XYZFPS"}
        if any(k == "G" and not v.is_integer() for k, v in words):
            raise GcodeError(f"line {n}: unsupported G-code {raw!r}")
        if 2 in gs or 3 in gs:
            raise GcodeError(f"line {n}: arcs (G2/G3) are not allowed: {raw!r}")
        if 91 in gs:
            raise GcodeError(f"line {n}: relative mode (G91) cannot be bounds-checked: {raw!r}")
        if 20 in gs:
            raise GcodeError(f"line {n}: inch mode (G20) is not allowed; use G21: {raw!r}")
        if "Z" in vals:
            raise GcodeError(f"line {n}: Z moves are not used on this machine: {raw!r}")
        if 3 in ms or 4 in ms:
            raise GcodeError(
                f"line {n}: spindle command {raw!r} does not match pen.up_cmd/pen.down_cmd; "
                "the pen state would be unknown"
            )
        if 5 in ms:
            st.pen_down = False
        if 4 in gs:
            p = vals.get("P", 0.0)
            if p < 0:
                raise GcodeError(f"line {n}: negative dwell {raw!r}")
            dwell += p
            continue
        if "F" in vals:
            if vals["F"] <= 0:
                raise GcodeError(f"line {n}: feed must be > 0: {raw!r}")
            st.feed = vals["F"]
        for g in gs:
            if g in (0, 1):
                st.motion = g
        if "X" not in vals and "Y" not in vals:
            continue
        x, y = vals.get("X", st.x), vals.get("Y", st.y)
        if not (-_BOUNDS_EPS <= x <= bw + _BOUNDS_EPS and -_BOUNDS_EPS <= y <= bh + _BOUNDS_EPS):
            raise OutOfBoundsError(
                f"line {n}: X{x:g} Y{y:g} is outside the bed (0..{bw:g}, 0..{bh:g} mm). "
                "Check machine.page_origin_mm, margins and the drawing's fit to the page."
            )
        if not st.pen_known:
            raise GcodeError(f"line {n}: motion before the pen was raised; start with pen up")
        rapid = st.motion == 0
        if rapid and st.pen_down:
            raise GcodeError(f"line {n}: G0 travel while the pen is down: {raw!r}")
        feed = cfg.motion.travel_feed_mm_min if rapid else st.feed
        moves.append(Move(st.x, st.y, x, y, rapid, st.pen_down, feed))
        st.x, st.y = x, y
    if st.pen_down:
        raise GcodeError("the job ends with the pen down; add the pen-up command at the end")
    return moves, dwell


def validate(lines: Sequence[str], cfg: Config) -> None:
    """Raise if ``lines`` are unsafe for this machine.

    Checks: every X/Y inside the bed, the pen is raised before the first move, no ``G0``
    while the pen is down, the job ends with the pen up, no arcs/relative/inch/Z, every line
    shorter than 70 characters, and pen commands match the config.

    Args:
        lines: G-code lines.
        cfg: Configuration.

    Raises:
        OutOfBoundsError: If any coordinate falls outside the bed.
        GcodeError: For any other safety violation.
    """
    interpret(lines, cfg)


def job_distances(lines: Sequence[str], cfg: Config) -> DrawingStats:
    """Return pen-down/pen-up distances (machine mm) for a G-code job."""
    moves, _ = interpret(lines, cfg)
    down = sum(m.length for m in moves if m.pen_down)
    up = sum(m.length for m in moves if not m.pen_down)
    down_cmd = _norm(cfg.pen.down_cmd)
    strokes = sum(1 for line in lines if _norm(strip_comment(line)) == down_cmd)
    return {"paths": strokes, "pen_down_mm": down, "pen_up_mm": up}


# --------------------------------------------------------------------------- files


def write_gcode(
    lines: Iterable[str],
    path: str | Path,
    *,
    source: str = "",
    stats: DrawingStats | None = None,
    est_time_s: float | None = None,
) -> Path:
    """Write ``lines`` to ``path`` with a ``;`` comment header.

    Args:
        lines: G-code lines.
        path: Output file; parent directories are created.
        source: Description of the input (file name), recorded in the header.
        stats: Optional drawing statistics for the header.
        est_time_s: Optional time estimate for the header.

    Returns:
        The written path.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    now = _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    header = [f"; ai-pen-plotter {__version__}", f"; date: {now}"]
    if source:
        header.append(f"; source: {source}")
    if stats is not None:
        header.append(
            f"; paths: {stats['paths']}  pen-down: {stats['pen_down_mm'] / 1000:.2f} m  "
            f"pen-up: {stats['pen_up_mm'] / 1000:.2f} m"
        )
    if est_time_s is not None:
        header.append(f"; estimated time: {est_time_s / 60:.1f} min")
    body = list(lines)
    out.write_text("\n".join([*header, *body]) + "\n", encoding="utf-8")
    logger.info("wrote %d G-code lines to %s", len(body), out)
    return out


def read_gcode(path: str | Path) -> list[str]:
    """Read a G-code file into a list of lines (line endings stripped).

    Raises:
        GcodeError: If the file does not exist.
    """
    p = Path(path)
    if not p.is_file():
        raise GcodeError(f"G-code file not found: {p}")
    return p.read_text(encoding="utf-8").splitlines()
