from __future__ import annotations

import numpy as np
import pytest

from plotter.config import Config
from plotter.gcode import machine_to_page, page_to_machine


def test_page_corners_map_to_machine(cfg: Config) -> None:
    ox, oy = cfg.machine.page_origin_mm
    m = page_to_machine([[0, 0], [210, 297], [0, 297], [210, 0]], cfg)
    assert m[0].tolist() == pytest.approx([ox, oy + 297])  # top-left of sheet: far from user
    assert m[1].tolist() == pytest.approx([ox + 210, oy])  # bottom-right: near the user
    assert m[2].tolist() == pytest.approx([ox, oy])  # bottom-left = page_origin_mm
    assert m[3].tolist() == pytest.approx([ox + 210, oy + 297])


def test_transform_round_trip(cfg: Config) -> None:
    rng = np.random.default_rng(0)
    pts = rng.uniform([0, 0], [210, 297], size=(100, 2))
    assert np.allclose(machine_to_page(page_to_machine(pts, cfg), cfg), pts)


def test_y_axis_flips(cfg: Config) -> None:
    m = page_to_machine([[50, 10], [50, 20]], cfg)
    assert m[1, 1] < m[0, 1]  # moving down the page moves towards the user (-Y)
    assert m[0, 0] == m[1, 0]
