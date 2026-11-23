from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from plotter import gcode as G
from plotter import preview
from plotter.config import Config


def test_render_drawing_and_gcode(tmp_path: Path, cfg: Config) -> None:
    d = [np.array([[30, 30], [180, 30], [180, 260]], dtype=np.float64), np.array([[50.0, 50.0]])]
    png = preview.render(d, tmp_path / "a" / "d.png", cfg, title="test")
    assert png.exists()
    assert png.stat().st_size > 5000
    lines = G.to_gcode(d, cfg)
    png2 = preview.render(lines, tmp_path / "g.png", cfg)
    assert png2.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert preview.render([], tmp_path / "empty.png", cfg).exists()


def test_png_is_150_dpi_a4ish(tmp_path: Path, cfg: Config) -> None:
    import matplotlib.image as mpimg

    png = preview.render([], tmp_path / "p.png", cfg)
    h, w = mpimg.imread(png).shape[:2]
    assert 1300 < w < 1450  # ~ (210 + margins) mm at 150 dpi
    assert h > w


def test_trapezoid() -> None:
    # long move: cruise dominates
    t = preview._trapezoid_time(1000, 50, 500)
    assert t == pytest.approx(1000 / 50 + 50 / 500)
    # short move never reaches v
    assert preview._trapezoid_time(1, 50, 500) == pytest.approx(2 * np.sqrt(1 / 500))
    assert preview._trapezoid_time(0, 50, 500) == 0


def test_estimate_time(cfg: Config) -> None:
    lines = G.to_gcode([np.array([[30, 30], [130, 30]], dtype=np.float64)], cfg)
    t = preview.estimate_time(lines, cfg)
    dwell = 4 * 0.15
    draw = 100 / 50  # 100 mm at 3000 mm/min
    assert dwell + draw < t < dwell + draw + 10


def test_collinear_segments_form_one_run(cfg: Config) -> None:
    x = np.linspace(30, 130, 101)
    many = [np.column_stack([x, np.full_like(x, 30)])]
    one = [np.array([[30, 30], [130, 30]], dtype=np.float64)]
    t_many = preview.estimate_time(G.to_gcode(many, cfg), cfg)
    t_one = preview.estimate_time(G.to_gcode(one, cfg), cfg)
    assert t_many == pytest.approx(t_one, rel=0.01)


def test_sharp_corners_cost_time(cfg: Config) -> None:
    zig = np.array([[30 + i, 30 + (i % 2) * 2] for i in range(50)], dtype=np.float64)
    straight = np.array([[30, 30], [30 + np.hypot(1, 2) * 49, 30]], dtype=np.float64)
    assert preview.estimate_time(G.to_gcode([zig], cfg), cfg) > preview.estimate_time(
        G.to_gcode([straight], cfg), cfg
    )
