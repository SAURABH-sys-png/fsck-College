"""Exception hierarchy for ai-pen-plotter.

Every error message should tell the user what went wrong *and* how to fix it.
"""

from __future__ import annotations


class PlotterError(Exception):
    """Base class for all expected, user-facing errors."""


class ConfigError(PlotterError):
    """The configuration file is missing, malformed or inconsistent."""


class CameraError(PlotterError):
    """The camera could not be opened or returned no frame."""


class OcrError(PlotterError):
    """Text recognition failed (AI or Tesseract)."""


class GcodeError(PlotterError):
    """Generated or loaded G-code is unsafe or malformed."""


class OutOfBoundsError(GcodeError):
    """A G-code coordinate falls outside the machine bed."""


class GrblError(PlotterError):
    """GRBL rejected a command (error:N) or the serial link failed."""


class GrblAlarm(GrblError):  # noqa: N818 - name fixed by the spec (CLAUDE.md §9)
    """GRBL entered an ALARM state (position lost or limit hit)."""
