from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pytest

from plotter import camera
from plotter.errors import CameraError, PlotterError
from plotter.images import load_image, save_image


class FakeCapture:
    instances: ClassVar[list[FakeCapture]] = []

    def __init__(self, src: Any, opened: bool = True, frames: int = 100) -> None:
        self.src = src
        self.opened = opened
        self.frames = frames
        self.reads = 0
        self.released = False
        FakeCapture.instances.append(self)

    def isOpened(self) -> bool:  # noqa: N802 - mirrors cv2 API
        return self.opened

    def read(self) -> tuple[bool, np.ndarray | None]:
        self.reads += 1
        if self.reads > self.frames:
            return False, None
        return True, np.full((48, 64, 3), self.reads, np.uint8)

    def release(self) -> None:
        self.released = True


def test_capture_discards_warmup(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeCapture.instances.clear()
    monkeypatch.setattr(camera.cv2, "VideoCapture", lambda src: FakeCapture(src))
    frame = camera.capture("1", warmup_frames=5)
    cap = FakeCapture.instances[-1]
    assert cap.src == 1  # digit string becomes an index
    assert cap.reads == 6
    assert frame[0, 0, 0] == 6
    assert cap.released


def test_capture_url_unopened(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(camera.cv2, "VideoCapture", lambda src: FakeCapture(src, opened=False))
    with pytest.raises(CameraError, match="Wi-Fi"):
        camera.capture("http://192.168.1.5:4747/video")
    with pytest.raises(CameraError, match="plugged in"):
        camera.capture(3)


def test_capture_no_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(camera.cv2, "VideoCapture", lambda src: FakeCapture(src, frames=0))
    with pytest.raises(CameraError, match="no frame"):
        camera.capture(0, warmup_frames=0)


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    img = np.zeros((10, 12, 3), np.uint8)
    img[2:5, 3:6] = (10, 200, 30)
    p = camera.save(img, tmp_path / "ünïcode dir" / "frame.png")
    back = load_image(p)
    assert np.array_equal(back, img)


def test_image_io_errors(tmp_path: Path) -> None:
    with pytest.raises(PlotterError, match="not found"):
        load_image(tmp_path / "nope.png")
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(PlotterError, match="decode"):
        load_image(bad)
    with pytest.raises(PlotterError, match="encode"):
        save_image(np.zeros((2, 2, 3), np.uint8), tmp_path / "x.unknownext")
