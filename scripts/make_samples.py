"""Generate the synthetic sample inputs in samples/ (reproducible, no personal photos).

Run from the repo root:  python scripts/make_samples.py
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

OUT = Path(__file__).resolve().parents[1] / "samples"

SAMPLE_TEXT = """\
Dear plotter,

This page was typed on a laptop and written by a pen held by two stepper motors and a
tiny servo. Every letter is a single stroke from the Hershey fonts, so the pen draws each
line exactly once - no outlines, no fills.

The quick brown fox jumps over the lazy dog. 0123456789
Pack my box with five dozen liquor jugs!

  - DRAW mode copies line art, diagrams and photos (by hatching).
  - WRITE mode reads a page with AI vision OCR and copies the text.

Calibrate first: draw a 100 x 100 mm square, measure it, and adjust $100 / $101.
"""


def lineart() -> np.ndarray:
    """A small robot + sun line drawing, black on white."""
    img = np.full((1100, 800, 3), 255, np.uint8)
    k = (0, 0, 0)
    t = 6
    cv2.rectangle(img, (250, 300), (550, 620), k, t)  # body
    cv2.rectangle(img, (300, 120), (500, 300), k, t)  # head
    cv2.circle(img, (355, 200), 28, k, t)  # eyes
    cv2.circle(img, (445, 200), 28, k, t)
    cv2.line(img, (360, 260), (440, 260), k, t)  # mouth
    cv2.line(img, (400, 120), (400, 60), k, t)  # antenna
    cv2.circle(img, (400, 45), 15, k, t)
    cv2.line(img, (250, 360), (130, 500), k, t)  # arms
    cv2.line(img, (550, 360), (670, 500), k, t)
    cv2.line(img, (330, 620), (330, 820), k, t)  # legs
    cv2.line(img, (470, 620), (470, 820), k, t)
    cv2.line(img, (280, 820), (330, 820), k, t)
    cv2.line(img, (470, 820), (520, 820), k, t)
    for i in range(3):  # buttons
        cv2.circle(img, (400, 380 + i * 70), 18, k, t)
    cv2.circle(img, (130, 130), 60, k, t)  # sun
    for a in np.linspace(0, 2 * np.pi, 12, endpoint=False):
        p1 = (int(130 + 80 * np.cos(a)), int(130 + 80 * np.sin(a)))
        p2 = (int(130 + 110 * np.cos(a)), int(130 + 110 * np.sin(a)))
        cv2.line(img, p1, p2, k, t)
    pts = np.array([[60, 1000], [200, 900], [340, 1000], [480, 900], [620, 1000], [740, 930]])
    cv2.polylines(img, [pts.reshape(-1, 1, 2)], False, k, t)  # ground
    cv2.putText(img, "HELLO", (240, 1060), cv2.FONT_HERSHEY_SIMPLEX, 2.4, k, t)
    return img


def photo() -> np.ndarray:
    """A 'photo': shaded sphere on a gradient backdrop with a shadow."""
    h, w = 900, 700
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    bg = 200 - 120 * (yy / h)  # backdrop darkens towards the bottom
    img = bg.copy()
    shadow = ((xx - 380) / 230) ** 2 + ((yy - 760) / 60) ** 2 < 1
    img[shadow] *= 0.45
    cx, cy, r = 340, 450, 220
    d2 = (xx - cx) ** 2 + (yy - cy) ** 2
    sphere = d2 < r * r
    z = np.sqrt(np.clip(r * r - d2, 0, None)) / r
    nx, ny = (xx - cx) / r, (yy - cy) / r
    light = np.clip(-0.55 * nx - 0.6 * ny + 0.58 * z, 0, 1)
    img[sphere] = (25 + 225 * light)[sphere]
    rng = np.random.default_rng(0)
    img += rng.normal(0, 3, img.shape)
    gray = np.clip(img, 0, 255).astype(np.uint8)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def page_photo() -> np.ndarray:
    """A printed page photographed at an angle on a dark desk."""
    page = np.full((1188, 840, 3), 245, np.uint8)  # A4 at 4 px/mm
    y = 140
    for line in SAMPLE_TEXT.splitlines()[:10]:
        cv2.putText(page, line[:52], (70, y), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (25, 25, 25), 2)
        y += 52
    canvas = np.full((1300, 1100, 3), (52, 46, 40), np.uint8)
    src = np.float32([[0, 0], [839, 0], [839, 1187], [0, 1187]])
    dst = np.float32([[180, 90], [930, 140], [1000, 1200], [110, 1160]])
    m = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(page, m, (1100, 1300))
    mask = cv2.warpPerspective(np.full(page.shape[:2], 255, np.uint8), m, (1100, 1300)) > 0
    canvas[mask] = warped[mask]
    return cv2.GaussianBlur(canvas, (3, 3), 0)


def main() -> None:
    """Write all samples."""
    OUT.mkdir(exist_ok=True)
    cv2.imwrite(str(OUT / "lineart.png"), lineart())
    cv2.imwrite(str(OUT / "photo.jpg"), photo(), [cv2.IMWRITE_JPEG_QUALITY, 90])
    cv2.imwrite(str(OUT / "page_photo.jpg"), page_photo(), [cv2.IMWRITE_JPEG_QUALITY, 90])
    (OUT / "sample_text.txt").write_text(SAMPLE_TEXT, encoding="utf-8")
    print(f"samples written to {OUT}")


if __name__ == "__main__":
    main()
