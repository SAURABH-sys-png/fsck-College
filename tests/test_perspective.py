from __future__ import annotations

import numpy as np

from plotter import perspective as PS


def test_order_corners() -> None:
    pts = np.array([[10, 90], [90, 10], [10, 10], [90, 90]])
    out = PS.order_corners(pts)
    assert out.tolist() == [[10, 10], [90, 10], [90, 90], [10, 90]]


def test_find_page_corners(page_photo: tuple[np.ndarray, np.ndarray]) -> None:
    img, truth = page_photo
    corners = PS.find_page_corners(img)
    assert corners is not None
    assert np.abs(corners - truth).max() < 8


def test_warp_to_a4_shape_and_content(page_photo: tuple[np.ndarray, np.ndarray]) -> None:
    img, truth = page_photo
    out = PS.warp_to_a4(img, truth, px_per_mm=10)
    assert out.shape == (2970, 2100, 3)
    # the warped sheet is mostly white paper
    assert out.mean() > 180
    small = PS.warp_to_a4(img, truth, px_per_mm=2)
    assert small.shape[:2] == (594, 420)


def test_landscape_quad_is_rotated_to_portrait() -> None:
    img = np.full((400, 600, 3), 30, np.uint8)
    img[100:300, 50:550] = 240
    corners = np.array([[50, 100], [549, 100], [549, 299], [50, 299]], float)
    out = PS.warp_to_a4(img, corners, px_per_mm=1)
    assert out.shape[:2] == (297, 210)
    assert out.mean() > 200


def test_flatten_without_page_returns_input() -> None:
    blank = np.full((200, 200, 3), 128, np.uint8)
    assert PS.find_page_corners(blank) is None
    assert PS.flatten(blank) is blank


def test_flatten_with_page(page_photo: tuple[np.ndarray, np.ndarray]) -> None:
    img, _ = page_photo
    out = PS.flatten(img, px_per_mm=3)
    assert out.shape[:2] == (891, 630)
