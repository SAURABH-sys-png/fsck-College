"""Lay out text on ruled lines with a Hershey single-stroke font.

* Glyphs come from the ``Hershey-Fonts`` package (public-domain Hershey data); cap height is
  scaled to ``text.size_mm``.
* Words wrap by measured advance widths inside the left/right margins; blank lines and
  leading indentation are preserved; a word wider than the line is hyphenated.
* Baselines start at ``text.first_line_y_mm`` and step by ``text.line_spacing_mm``;
  overflow starts a new page (one :data:`~plotter.types.Drawing` per page).
* Optional seeded jitter (baseline, scale, rotation) gives a hand-written look. Its worst
  case is reserved inside the margins, so text never crosses them.
"""

from __future__ import annotations

import functools
import logging
import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

import numpy as np

from plotter.config import Config
from plotter.errors import ConfigError
from plotter.types import Drawing, Polyline

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

TAB_SPACES = 4
FALLBACK_CHAR = "?"
_NORMALISE = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "−": "-",
        "•": "*",
        " ": " ",
        "…": "...",
        "\t": " " * TAB_SPACES,
        "\r": None,
    }
)


@dataclass(frozen=True)
class Glyph:
    """One glyph in font units, origin at the left end of the baseline, +y down."""

    strokes: tuple[Polyline, ...]
    advance: float


@dataclass(frozen=True)
class Font:
    """A loaded Hershey font."""

    name: str
    glyphs: Mapping[str, Glyph]
    cap_units: float  # baseline − cap line
    ascent_units: float  # highest ink above the baseline (≥ cap height)
    descent_units: float  # lowest ink below the baseline


@functools.lru_cache(maxsize=8)
def load_font(name: str) -> Font:
    """Load a Hershey font by name (e.g. ``futural``) into immutable glyph data.

    Raises:
        ConfigError: If the font name is unknown.
    """
    from HersheyFonts import HersheyFonts

    hf = HersheyFonts()
    if name not in hf.default_font_names:
        raise ConfigError(
            f"unknown Hershey font {name!r}; choose one of {', '.join(hf.default_font_names)}"
        )
    hf.load_default_font(name)
    base = float(hf.render_options["base_line"])
    cap = float(hf.render_options["cap_line"])
    glyphs: dict[str, Glyph] = {}
    asc, desc = base - cap, 0.0
    for code in range(32, 127):
        ch = chr(code)
        found = list(hf.glyphs_for_text(ch))
        if not found:
            continue
        g = found[0]
        strokes = tuple(
            np.array([[x - g.left_offset, y - base] for x, y in s], dtype=np.float64)
            for s in g.strokes
            if len(s) >= 2
        )
        if strokes:
            ys = np.concatenate([s[:, 1] for s in strokes])
            asc = max(asc, float(-ys.min()))
            desc = max(desc, float(ys.max()))
        glyphs[ch] = Glyph(strokes=strokes, advance=float(g.char_width))
    return Font(name, MappingProxyType(glyphs), base - cap, asc, desc)


@dataclass(frozen=True)
class _Metrics:
    scale: float  # mm per font unit
    left: float  # x of the first glyph origin
    width: float  # usable advance width per line (mm)
    first_baseline: float
    lines_per_page: int


def _metrics(cfg: Config, font: Font) -> _Metrics:
    t = cfg.text
    j = t.jitter
    s = t.size_mm / font.cap_units
    k = 1 + (j.scale_pct / 100 if j.enabled else 0.0)
    sin_r = math.sin(math.radians(j.rotate_deg)) if j.enabled else 0.0
    wobble = j.baseline_mm if j.enabled else 0.0
    max_adv = max((g.advance for g in font.glyphs.values()), default=0.0) * s
    ascent, descent = font.ascent_units * s * k, font.descent_units * s * k
    side = ascent * sin_r  # rotation can swing the top of a glyph sideways
    x0, y0, x1, y1 = cfg.printable_area
    width = (x1 - x0) - 2 * side - (k - 1) * max_adv
    top_need = ascent + max_adv * k * sin_r + wobble
    bottom_need = descent + max_adv * k * sin_r + wobble
    first = t.first_line_y_mm
    if first - top_need < y0 - 1e-9:
        raise ConfigError(
            f"text.first_line_y_mm={first} is too close to the top margin for "
            f"{t.size_mm} mm text (needs ≥ {y0 + top_need:.1f}); increase it"
        )
    n = math.floor((y1 - bottom_need - first) / t.line_spacing_mm) + 1
    if n < 1 or width <= max_adv:
        raise ConfigError("text.size_mm is too large for the printable area")
    return _Metrics(s, x0 + side, width, first, n)


def normalise(text: str) -> str:
    """Map typographic punctuation to ASCII and expand tabs."""
    return text.translate(_NORMALISE)


def _advance(font: Font, text: str, scale: float) -> float:
    fallback = font.glyphs.get(FALLBACK_CHAR)
    total = 0.0
    for ch in text:
        g = font.glyphs.get(ch, fallback)
        total += g.advance if g else 0.0
    return total * scale


def measure(text: str, cfg: Config) -> float:
    """Return the nominal width of a single line of ``text`` in mm."""
    font = load_font(cfg.text.font)
    return _advance(font, normalise(text), cfg.text.size_mm / font.cap_units)


def _split_long_word(word: str, font: Font, s: float, width: float) -> list[str]:
    """Break a word that is wider than the line into hyphenated chunks."""
    chunks: list[str] = []
    rest = word
    while _advance(font, rest, s) > width:
        cut = 1
        while cut < len(rest) and _advance(font, rest[: cut + 1] + "-", s) <= width:
            cut += 1
        chunks.append(rest[:cut] + "-")
        rest = rest[cut:]
    chunks.append(rest)
    return chunks


def wrap(text: str, cfg: Config) -> list[tuple[float, str]]:
    """Word-wrap ``text`` into lines that fit the printable width.

    Returns:
        ``(indent_mm, line_text)`` pairs; blank lines are ``(0.0, "")``.
    """
    font = load_font(cfg.text.font)
    m = _metrics(cfg, font)
    s = m.scale
    space = _advance(font, " ", s)
    out: list[tuple[float, str]] = []
    for para in normalise(text).split("\n"):
        if not para.strip():
            out.append((0.0, ""))
            continue
        indent = min((len(para) - len(para.lstrip(" "))) * space, m.width / 2)
        avail = m.width - indent
        line: list[str] = []
        used = 0.0
        for word in re.split(r" +", para.strip()):
            w = _advance(font, word, s)
            if w > avail:
                if line:
                    out.append((indent, " ".join(line)))
                    indent, avail, line, used = 0.0, m.width, [], 0.0
                chunks = _split_long_word(word, font, s, avail)
                out.extend((0.0, c) for c in chunks[:-1])
                word, w = chunks[-1], _advance(font, chunks[-1], s)
            need = w if not line else used + space + w
            if line and need > avail:
                out.append((indent, " ".join(line)))
                indent, avail, line, used = 0.0, m.width, [word], w
            else:
                line.append(word)
                used = need
        out.append((indent, " ".join(line)))
    return out


def layout_text(text: str, cfg: Config) -> list[Drawing]:
    """Lay ``text`` out on as many A4 pages as needed.

    Args:
        text: Plain text; newlines separate lines, blank lines are kept.
        cfg: Configuration (``text`` section and margins).

    Returns:
        One drawing per page (page-mm). Empty or whitespace-only text returns ``[]``.

    Raises:
        ConfigError: If the font is unknown or the text size does not fit the page.
    """
    if not text.strip():
        return []
    font = load_font(cfg.text.font)
    m = _metrics(cfg, font)
    j = cfg.text.jitter
    rng = np.random.default_rng(j.seed)
    s = m.scale
    fallback = font.glyphs.get(FALLBACK_CHAR)
    missing = sorted({ch for ch in normalise(text) if ch not in font.glyphs and ch != "\n"})
    if missing:
        logger.warning("no glyph for %s; drawing %r instead", "".join(missing), FALLBACK_CHAR)
    lines = wrap(text, cfg)
    while lines and not lines[-1][1]:
        lines.pop()
    pages: list[Drawing] = []
    for start in range(0, len(lines), m.lines_per_page):
        page: Drawing = []
        for row, (indent, line) in enumerate(lines[start : start + m.lines_per_page]):
            baseline = m.first_baseline + row * cfg.text.line_spacing_mm
            x = m.left + indent
            for ch in line:
                g = font.glyphs.get(ch, fallback)
                if g is None:
                    continue
                if g.strokes:
                    k, theta, dy = 1.0, 0.0, 0.0
                    if j.enabled:
                        k = 1 + rng.uniform(-j.scale_pct, j.scale_pct) / 100
                        theta = math.radians(rng.uniform(-j.rotate_deg, j.rotate_deg))
                        dy = rng.uniform(-j.baseline_mm, j.baseline_mm)
                    c, sn = math.cos(theta), math.sin(theta)
                    rot = np.array([[c, sn], [-sn, c]])  # row-vector rotation
                    origin = np.array([x, baseline + dy])
                    page.extend((st * (s * k)) @ rot + origin for st in g.strokes)
                x += g.advance * s
        pages.append(page)
    logger.info("laid out %d lines on %d page(s)", len(lines), len(pages))
    return pages
