"""Shared geometry type aliases and the Job record.

Geometry convention (CLAUDE.md §3): a ``Path`` is an ``(N, 2)`` float64 numpy array of
page-frame millimetres with ``N >= 2``; a ``Drawing`` is a list of paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias, TypedDict

import numpy as np
import numpy.typing as npt

Path: TypeAlias = npt.NDArray[np.float64]
"""One continuous pen-down stroke: ``(N, 2)`` float64, page-mm, ``N >= 2``."""

Polyline: TypeAlias = Path
"""Alias of :data:`Path` for modules that also use :class:`pathlib.Path`."""

Drawing: TypeAlias = list[Path]
"""A list of strokes in drawing order."""


class DrawingStats(TypedDict):
    """Summary of a drawing's pen motion."""

    paths: int
    pen_down_mm: float
    pen_up_mm: float


def as_path(points: npt.ArrayLike) -> Path:
    """Convert ``points`` to a contiguous ``(N, 2)`` float64 array.

    Args:
        points: Anything numpy can turn into an ``(N, 2)`` array.

    Returns:
        The points as a float64 array.

    Raises:
        ValueError: If the shape is not ``(N, 2)``.
    """
    arr = np.ascontiguousarray(points, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError(f"a path must have shape (N, 2), got {arr.shape}")
    return arr


@dataclass
class Job:
    """A ready-to-plot job: geometry, G-code and its summary."""

    name: str
    drawing: Drawing
    gcode: list[str]
    stats: DrawingStats
    est_time_s: float
    source: str = ""
    meta: dict[str, str] = field(default_factory=dict)
