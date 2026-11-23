"""Shared fixtures. Everything here is hardware-, camera- and network-free."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from plotter.config import Config, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def cfg() -> Config:
    """The shipped config/machine.yaml."""
    return load_config(REPO_ROOT / "config" / "machine.yaml")


@pytest.fixture
def lineart_img() -> np.ndarray:
    """White canvas with black strokes: a rectangle, a circle, a cross and a diagonal."""
    img = np.full((400, 300, 3), 255, np.uint8)
    cv2.rectangle(img, (30, 30), (270, 150), (0, 0, 0), 3)
    cv2.circle(img, (150, 270), 70, (0, 0, 0), 3)
    cv2.line(img, (40, 380), (260, 200), (0, 0, 0), 3)
    cv2.line(img, (150, 40), (150, 140), (0, 0, 0), 3)
    return img


@pytest.fixture
def gradient_img() -> np.ndarray:
    """Left-to-right dark→light gradient (for hatching)."""
    row = np.linspace(0, 255, 300).astype(np.uint8)
    gray = np.tile(row, (200, 1))
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


@pytest.fixture
def page_photo() -> tuple[np.ndarray, np.ndarray]:
    """A white 'sheet' (A4 ratio) seen in perspective on a dark table, plus its true corners."""
    img = np.full((800, 700, 3), 40, np.uint8)
    corners = np.array([[150, 90], [560, 120], [610, 700], [110, 680]], np.int32)
    cv2.fillPoly(img, [corners], (235, 235, 235))
    cv2.putText(img, "HELLO", (220, 400), cv2.FONT_HERSHEY_SIMPLEX, 2, (20, 20, 20), 4)
    return img, corners.astype(np.float64)
