from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np
import pytest

from plotter import paths as P
from plotter import vision as V
from plotter.config import Config


def _inside(drawing: list[np.ndarray], area: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = area
    pts = np.vstack(drawing)
    eps = 1e-6
    return bool(
        (pts[:, 0] >= x0 - eps).all()
        and (pts[:, 0] <= x1 + eps).all()
        and (pts[:, 1] >= y0 - eps).all()
        and (pts[:, 1] <= y1 + eps).all()
    )


def test_image_placement_centres() -> None:
    s, ox, oy = V.image_placement((100, 200), (0, 0, 100, 100))
    assert s == pytest.approx(0.5)
    assert ox == pytest.approx(0)
    assert oy == pytest.approx(25)
    d = V.px_to_mm([np.array([[0.0, 0.0], [199.0, 99.0]])], (100, 200), (0, 0, 100, 100))
    assert d[0][0].tolist() == pytest.approx([0.25, 25.25])
    assert d[0][1].tolist() == pytest.approx([99.75, 74.75])


def test_to_gray_variants() -> None:
    g = np.zeros((4, 4), np.uint8)
    assert V.to_gray(g) is g
    assert V.to_gray(np.zeros((4, 4, 3), np.uint8)).shape == (4, 4)
    assert V.to_gray(np.zeros((4, 4, 4), np.uint8)).shape == (4, 4)


def test_trace_plus_sign() -> None:
    sk = np.zeros((21, 21), bool)
    sk[10, 2:19] = True
    sk[2:19, 10] = True
    polys = V.trace_skeleton(sk, min_px=0)
    assert len(polys) == 4  # four arms from the junction
    total = sum(P.path_length(p) for p in polys)
    assert total == pytest.approx(32, abs=1)
    # every arm starts or ends at the junction
    assert all(([10, 10] in p.tolist()) for p in polys)


def test_trace_closed_loop_and_speck() -> None:
    sk = np.zeros((30, 30), bool)
    sk[5, 5:20] = True
    sk[19, 5:20] = True
    sk[5:20, 5] = True
    sk[5:20, 19] = True
    sk[25, 25] = True  # isolated pixel
    sk[27, 2:5] = True  # 3-px speck
    polys = V.trace_skeleton(sk, min_px=5)
    assert len(polys) == 1
    loop = polys[0]
    assert np.allclose(loop[0], loop[-1])  # closed
    assert P.path_length(loop) == pytest.approx(56 - 4 * (2 - np.sqrt(2)), abs=1)  # cut corners


def test_trace_drops_short_spur_keeps_link() -> None:
    sk = np.zeros((40, 40), bool)
    sk[20, 2:38] = True  # long horizontal
    sk[17:20, 20] = True  # 3-px spur up from the middle
    polys = V.trace_skeleton(sk, min_px=6)
    lengths = sorted(P.path_length(p) for p in polys)
    assert len(polys) == 2  # spur pruned, the two halves of the line remain
    assert sum(lengths) == pytest.approx(36, abs=1)


def test_trace_empty() -> None:
    assert V.trace_skeleton(np.zeros((5, 5), bool)) == []


def test_lineart_on_synthetic(cfg: Config, lineart_img: np.ndarray) -> None:
    d = V.image_to_drawing(lineart_img, cfg, "lineart")
    assert d
    assert all(p.shape[1] == 2 and p.dtype == np.float64 for p in d)
    assert _inside(d, cfg.printable_area)
    s, _, _ = V.image_placement(lineart_img.shape[:2], cfg.printable_area)
    expected_px = 2 * (240 + 120) + 2 * np.pi * 70 + np.hypot(220, 180) + 100
    total = sum(P.path_length(p) for p in d)
    assert total == pytest.approx(expected_px * s, rel=0.2)


def test_lineart_blank_image_gives_nothing(cfg: Config) -> None:
    blank = np.full((100, 100, 3), 255, np.uint8)
    assert V.image_to_drawing(blank, cfg, "lineart") == []


def test_outline_not_doubled(cfg: Config) -> None:
    img = np.full((300, 300, 3), 255, np.uint8)
    cv2.circle(img, (150, 150), 80, (0, 0, 0), -1)
    c = replace(cfg, vision=replace(cfg.vision, blur_ksize=1))
    d = V.image_to_drawing(img, c, "outline")
    assert _inside(d, c.printable_area)
    s, _, _ = V.image_placement(img.shape[:2], c.printable_area)
    total = sum(P.path_length(p) for p in d) / s
    circumference = 2 * np.pi * 80
    assert circumference * 0.9 < total < circumference * 1.5


def test_outline_open_line_traced_once(cfg: Config) -> None:
    img = np.full((200, 200), 255, np.uint8)
    cv2.line(img, (20, 100), (180, 100), 0, 9)
    d = V.image_to_drawing(img, cfg, "outline")
    s, _, _ = V.image_placement(img.shape[:2], cfg.printable_area)
    total = sum(P.path_length(p) for p in d) / s
    assert 300 < total < 420  # two long edges + caps, not four


def test_hatch_density_follows_darkness(cfg: Config, gradient_img: np.ndarray) -> None:
    d = V.image_to_drawing(gradient_img, cfg, "hatch")
    assert d
    assert _inside(d, cfg.printable_area)
    s, ox, _ = V.image_placement(gradient_img.shape[:2], cfg.printable_area)
    third = gradient_img.shape[1] * s / 3

    def ink_in(xa: float, xb: float) -> float:
        tot = 0.0
        for p in d:
            mid = p.mean(axis=0)[0]
            if xa <= mid < xb:
                tot += P.path_length(p)
        return tot

    dark = ink_in(ox, ox + third)
    light = ink_in(ox + 2 * third, ox + 3 * third)
    assert dark > 2 * light
    # brightest quarter receives no hatching at all
    xs = np.vstack(d)[:, 0]
    assert xs.max() < ox + gradient_img.shape[1] * s * 0.8


def test_hatch_segments_are_straight(cfg: Config, gradient_img: np.ndarray) -> None:
    d = V.image_to_drawing(gradient_img, cfg, "hatch")
    assert all(len(p) == 2 for p in d)
    angles = {round(float(np.degrees(np.arctan2(*(p[1] - p[0])[::-1]))) % 180) for p in d}
    assert angles <= {45, 135, 0, 90}


def test_unknown_mode(cfg: Config, lineart_img: np.ndarray) -> None:
    with pytest.raises(ValueError, match="unknown vision mode"):
        V.image_to_drawing(lineart_img, cfg, "pointillism")
    with pytest.raises(ValueError, match="empty"):
        V.image_to_drawing(np.zeros((0, 0, 3), np.uint8), cfg)
