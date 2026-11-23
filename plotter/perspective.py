"""Find a sheet of paper in a photo and flatten it to an A4 rectangle."""

from __future__ import annotations

import logging

import cv2
import numpy as np
import numpy.typing as npt

from plotter.images import Image

logger = logging.getLogger(__name__)

MIN_PAGE_AREA_FRAC = 0.2
"""The page must cover more than this fraction of the photo."""
_BLUR_KSIZE = 5
_CANNY_LO, _CANNY_HI = 50, 150
_APPROX_EPS_FRAC = 0.02

FloatArray = npt.NDArray[np.float64]


def order_corners(pts: npt.ArrayLike) -> FloatArray:
    """Order four points as top-left, top-right, bottom-right, bottom-left (image frame)."""
    p = np.asarray(pts, dtype=np.float64).reshape(4, 2)
    s = p.sum(axis=1)
    d = p[:, 1] - p[:, 0]
    return np.array([p[np.argmin(s)], p[np.argmin(d)], p[np.argmax(s)], p[np.argmax(d)]])


def find_page_corners(img: Image, min_area_frac: float = MIN_PAGE_AREA_FRAC) -> FloatArray | None:
    """Locate the largest four-cornered contour (the sheet).

    Pipeline: grayscale → blur → Canny → dilate → contours → largest contour whose
    ``approxPolyDP`` has 4 convex points and covers more than ``min_area_frac`` of the image.

    Args:
        img: BGR or grayscale photo.
        min_area_frac: Minimum page area as a fraction of the image.

    Returns:
        ``(4, 2)`` corners ordered TL, TR, BR, BL in pixels, or ``None`` if no page is found.
    """
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (_BLUR_KSIZE, _BLUR_KSIZE), 0)
    edges = cv2.Canny(blur, _CANNY_LO, _CANNY_HI)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    min_area = min_area_frac * gray.shape[0] * gray.shape[1]
    for c in sorted(contours, key=cv2.contourArea, reverse=True):
        area = cv2.contourArea(c)
        if area <= min_area:
            break
        approx = cv2.approxPolyDP(c, _APPROX_EPS_FRAC * cv2.arcLength(c, True), True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return order_corners(approx)
    return None


def warp_to_a4(
    img: Image,
    corners: npt.ArrayLike,
    px_per_mm: float = 10.0,
    page_size_mm: tuple[float, float] = (210.0, 297.0),
) -> Image:
    """Perspective-warp the quadrilateral ``corners`` to an upright page image.

    If the quadrilateral is wider than tall (sheet photographed sideways), it is rotated by
    90° so the output is always portrait.

    Args:
        img: Source photo.
        corners: ``(4, 2)`` TL, TR, BR, BL pixel corners.
        px_per_mm: Output resolution (10 → 2100 × 2970 px for A4).
        page_size_mm: Page width and height in mm.

    Returns:
        The flattened page image.
    """
    c = order_corners(corners)
    top = np.linalg.norm(c[1] - c[0]) + np.linalg.norm(c[2] - c[3])
    side = np.linalg.norm(c[3] - c[0]) + np.linalg.norm(c[2] - c[1])
    if top > side:
        logger.info("page looks landscape in the photo; rotating 90°")
        c = np.roll(c, -1, axis=0)
    w = round(page_size_mm[0] * px_per_mm)
    h = round(page_size_mm[1] * px_per_mm)
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(c.astype(np.float32), dst)
    out = cv2.warpPerspective(
        img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )
    return np.asarray(out, dtype=np.uint8)


def flatten(
    img: Image,
    px_per_mm: float = 10.0,
    page_size_mm: tuple[float, float] = (210.0, 297.0),
) -> Image:
    """Detect the page and warp it to A4; return ``img`` unchanged if no page is found.

    Never raises for a missing page — it logs a warning instead.
    """
    corners = find_page_corners(img)
    if corners is None:
        logger.warning("no page found in the photo; using the image as-is")
        return img
    return warp_to_a4(img, corners, px_per_mm, page_size_mm)
