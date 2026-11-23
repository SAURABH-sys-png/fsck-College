"""Camera capture. This is the only module that opens a camera (CLAUDE.md §4)."""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from plotter.errors import CameraError
from plotter.images import Image, save_image

logger = logging.getLogger(__name__)


def capture(source: int | str, warmup_frames: int = 5) -> Image:
    """Grab one frame from a USB webcam index or a network stream URL.

    The first ``warmup_frames`` frames are discarded so auto-exposure and white balance can
    settle.

    Args:
        source: OpenCV camera index (``0``, ``1``…) or a URL such as a DroidCam / IP Webcam
            stream (``http://192.168.1.5:4747/video``).
        warmup_frames: Frames to discard before the one that is returned.

    Returns:
        A BGR ``uint8`` frame.

    Raises:
        CameraError: If the camera cannot be opened or returns no frame.
    """
    src: int | str = int(source) if isinstance(source, str) and source.isdigit() else source
    cap = cv2.VideoCapture(src)
    try:
        if not cap.isOpened():
            hint = (
                "check the phone and laptop are on the same Wi-Fi and the URL opens in a browser"
                if isinstance(src, str)
                else "check it is plugged in, not used by another app, or try another index"
            )
            raise CameraError(f"cannot open camera {src!r}: {hint} (camera.source in config).")
        frame = None
        for _ in range(max(0, warmup_frames) + 1):
            ok, frame = cap.read()
            if not ok:
                frame = None
        if frame is None:
            raise CameraError(f"camera {src!r} opened but returned no frame; try again")
        logger.info("captured %dx%d frame from %r", frame.shape[1], frame.shape[0], src)
        return np.asarray(frame, dtype=np.uint8)
    finally:
        cap.release()


def save(frame: Image, path: str | Path) -> Path:
    """Save a captured frame (format from the file extension)."""
    return save_image(frame, path)
