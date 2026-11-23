"""Image file I/O that works with non-ASCII paths on every OS (cv2.imread does not on Windows)."""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from plotter.errors import PlotterError

logger = logging.getLogger(__name__)

Image = npt.NDArray[np.uint8]


def load_image(path: str | Path) -> Image:
    """Read an image file as BGR ``uint8``.

    Args:
        path: Any format OpenCV can decode (PNG, JPEG, BMP, TIFF, WebP…).

    Returns:
        The image, ``(H, W, 3)``.

    Raises:
        PlotterError: If the file is missing or cannot be decoded.
    """
    p = Path(path)
    if not p.is_file():
        raise PlotterError(f"image not found: {p}")
    data = np.fromfile(str(p), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise PlotterError(f"cannot decode image {p}; is it a PNG/JPEG file?")
    return np.asarray(img, dtype=np.uint8)


def save_image(img: npt.NDArray[np.uint8], path: str | Path) -> Path:
    """Write ``img`` to ``path`` (format from the extension), creating parent folders.

    Raises:
        PlotterError: If encoding fails (e.g. unknown extension).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ext = p.suffix or ".png"
    try:
        ok, buf = cv2.imencode(ext, img)
    except cv2.error as exc:
        raise PlotterError(f"cannot encode image as {ext!r}; use .png or .jpg") from exc
    if not ok:
        raise PlotterError(f"cannot encode image as {ext!r}; use .png or .jpg")
    buf.tofile(str(p))
    logger.info("saved image %s", p)
    return p
