from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from plotter import text_layout as T
from plotter.config import Config
from plotter.errors import ConfigError

LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor "
    "incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud "
    "exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. "
)


def _no_jitter(cfg: Config) -> Config:
    return replace(cfg, text=replace(cfg.text, jitter=replace(cfg.text.jitter, enabled=False)))


def _all_points(pages: list[list[np.ndarray]]) -> np.ndarray:
    return np.vstack([p for page in pages for p in page])


def test_font_loads_and_cap_height(cfg: Config) -> None:
    font = T.load_font("futural")
    assert font.cap_units == 21
    assert {"A", "a", "0", " ", "?"} <= set(font.glyphs)
    c = _no_jitter(cfg)
    pages = T.layout_text("H", c)
    pts = _all_points(pages)
    height = pts[:, 1].max() - pts[:, 1].min()
    assert height == pytest.approx(c.text.size_mm, abs=1e-6)
    assert pts[:, 1].max() == pytest.approx(c.text.first_line_y_mm)  # sits on the baseline


def test_unknown_font(cfg: Config) -> None:
    c = replace(cfg, text=replace(cfg.text, font="comic-sans"))
    with pytest.raises(ConfigError, match="unknown Hershey font"):
        T.layout_text("x", c)


def test_measure_scales_with_text(cfg: Config) -> None:
    assert T.measure("", cfg) == 0
    assert T.measure("WW", cfg) == pytest.approx(2 * T.measure("W", cfg))
    assert T.measure("“hi”", cfg) == pytest.approx(T.measure('"hi"', cfg))


@pytest.mark.parametrize("jitter", [False, True])
def test_text_never_crosses_margins(cfg: Config, jitter: bool) -> None:
    c = cfg if jitter else _no_jitter(cfg)
    long_word = "Pneumonoultramicroscopicsilicovolcanoconiosis" * 3
    text = (LOREM * 6 + "\n\n" + long_word + "\n" + "W" * 200 + "\n    indented gjpqy|[]{}") * 2
    pages = T.layout_text(text, c)
    x0, y0, x1, y1 = c.printable_area
    for page in pages:
        pts = np.vstack(page)
        assert pts[:, 0].min() >= x0
        assert pts[:, 0].max() <= x1
        assert pts[:, 1].min() >= y0
        assert pts[:, 1].max() <= y1


def test_long_text_makes_multiple_pages(cfg: Config) -> None:
    pages = T.layout_text(LOREM * 40, cfg)
    assert len(pages) >= 2
    assert all(page for page in pages)


def test_lines_per_page_and_spacing(cfg: Config) -> None:
    c = _no_jitter(cfg)
    per_page = T._metrics(c, T.load_font("futural")).lines_per_page
    pages = T.layout_text("\n".join(["I"] * 100), c)  # 'I' is one stroke down to the baseline
    assert len(pages) == int(np.ceil(100 / per_page))
    baselines = sorted({round(float(p[:, 1].max()), 6) for p in pages[0]})
    assert len(baselines) == per_page
    expected = c.text.first_line_y_mm + c.text.line_spacing_mm * np.arange(per_page)
    assert np.allclose(baselines, expected)
    # every page restarts at the first baseline
    assert min(float(p[:, 1].max()) for p in pages[1]) == pytest.approx(c.text.first_line_y_mm)


def test_blank_lines_preserved(cfg: Config) -> None:
    c = _no_jitter(cfg)
    a = T.layout_text("top\nbottom", c)[0]
    b = T.layout_text("top\n\n\nbottom", c)[0]
    assert np.vstack(b)[:, 1].max() == pytest.approx(
        np.vstack(a)[:, 1].max() + 2 * c.text.line_spacing_mm
    )


def test_wrap_and_hyphenation(cfg: Config) -> None:
    lines = T.wrap("short words only", cfg)
    assert lines == [(0.0, "short words only")]
    lines = T.wrap("x" * 300, cfg)
    assert len(lines) > 1
    assert all(t.endswith("-") for _, t in lines[:-1])
    assert "".join(t.rstrip("-") for _, t in lines) == "x" * 300
    lines = T.wrap("   indented", cfg)
    assert lines[0][0] > 0


def test_wrap_respects_width(cfg: Config) -> None:
    font = T.load_font(cfg.text.font)
    m = T._metrics(cfg, font)
    for indent, line in T.wrap(LOREM * 3, cfg):
        assert indent + T.measure(line, cfg) <= m.width + 1e-9


def test_jitter_deterministic(cfg: Config) -> None:
    a = T.layout_text(LOREM, cfg)
    b = T.layout_text(LOREM, cfg)
    assert all(np.array_equal(p, q) for p, q in zip(a[0], b[0], strict=True))
    c = replace(cfg, text=replace(cfg.text, jitter=replace(cfg.text.jitter, seed=7)))
    d = T.layout_text(LOREM, c)
    assert not all(np.array_equal(p, q) for p, q in zip(a[0], d[0], strict=True))


def test_jitter_changes_shape_slightly(cfg: Config) -> None:
    plain = T.layout_text("Hello", _no_jitter(cfg))[0]
    wobbly = T.layout_text("Hello", cfg)[0]
    diff = max(float(np.abs(p - q).max()) for p, q in zip(plain, wobbly, strict=True))
    assert 0 < diff < 1.0


def test_empty_and_unknown_chars(cfg: Config, caplog: pytest.LogCaptureFixture) -> None:
    assert T.layout_text("   \n\n", cfg) == []
    pages = T.layout_text("café ✓", cfg)
    assert pages
    assert "no glyph" in caplog.text


def test_text_too_big_raises(cfg: Config) -> None:
    c = replace(cfg, text=replace(cfg.text, size_mm=200.0, first_line_y_mm=250.0))
    with pytest.raises(ConfigError):
        T.layout_text("x", c)
    c = replace(cfg, text=replace(cfg.text, first_line_y_mm=20.5))
    with pytest.raises(ConfigError, match="top margin"):
        T.layout_text("x", c)


def test_paths_are_float64_n2(cfg: Config) -> None:
    for p in T.layout_text("Abc", cfg)[0]:
        assert p.dtype == np.float64
        assert p.ndim == 2
        assert p.shape[1] == 2
        assert len(p) >= 2
