"""Load and validate ``config/machine.yaml`` into immutable dataclasses.

Every tunable lives in the YAML file (CLAUDE.md §6). Missing keys fall back to the defaults
below, which mirror the shipped file. Validation errors raise :class:`ConfigError` with the
offending key and a hint.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from plotter.errors import ConfigError

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config/machine.yaml")

VISION_MODES = ("lineart", "outline", "hatch")
OPTIMIZE_METHODS = ("greedy", "greedy+2opt", "none")


@dataclass(frozen=True)
class Margins:
    """Page margins in mm (page frame)."""

    left: float = 20.0
    right: float = 10.0
    top: float = 15.0
    bottom: float = 15.0


@dataclass(frozen=True)
class MachineConfig:
    """Bed geometry and serial link."""

    serial_port: str = "auto"
    baud: int = 115200
    bed_size_mm: tuple[float, float] = (230.0, 320.0)
    page_size_mm: tuple[float, float] = (210.0, 297.0)
    page_origin_mm: tuple[float, float] = (10.0, 10.0)
    margins_mm: Margins = field(default_factory=Margins)
    connect_wait_s: float = 2.0
    response_timeout_s: float = 60.0


@dataclass(frozen=True)
class MotionConfig:
    """Feeds and acceleration."""

    draw_feed_mm_min: float = 3000.0
    travel_feed_mm_min: float = 5000.0
    accel_mm_s2: float = 500.0
    coord_decimals: int = 2


@dataclass(frozen=True)
class PenConfig:
    """Pen-lift servo commands."""

    up_cmd: str = "M3 S30"
    down_cmd: str = "M3 S90"
    settle_s: float = 0.15


@dataclass(frozen=True)
class PathsConfig:
    """Path clean-up and ordering."""

    min_segment_mm: float = 0.2
    simplify_tolerance_mm: float = 0.08
    join_tolerance_mm: float = 0.2
    optimize: str = "greedy"
    two_opt_max_paths: int = 1500


@dataclass(frozen=True)
class CameraConfig:
    """Camera source and page-flattening resolution."""

    source: int | str = 0
    warp_px_per_mm: float = 10.0


@dataclass(frozen=True)
class HatchConfig:
    """Hatching for photos."""

    levels: int = 4
    spacing_mm: float = 1.2
    angles_deg: tuple[float, ...] = (45.0, -45.0, 0.0, 90.0)


@dataclass(frozen=True)
class VisionConfig:
    """Image → strokes parameters."""

    mode: str = "lineart"
    blur_ksize: int = 3
    adaptive_block: int = 31
    adaptive_c: float = 10.0
    min_contour_px: int = 15
    hatch: HatchConfig = field(default_factory=HatchConfig)


@dataclass(frozen=True)
class JitterConfig:
    """Hand-written look: small seeded random perturbations."""

    enabled: bool = True
    baseline_mm: float = 0.3
    scale_pct: float = 3.0
    rotate_deg: float = 1.5
    seed: int = 42


@dataclass(frozen=True)
class TextConfig:
    """Text layout with a Hershey single-stroke font."""

    font: str = "futural"
    size_mm: float = 5.0
    line_spacing_mm: float = 8.0
    first_line_y_mm: float = 25.0
    jitter: JitterConfig = field(default_factory=JitterConfig)


@dataclass(frozen=True)
class AiConfig:
    """Claude vision OCR settings. Model and key come from environment variables."""

    enabled: bool = True
    model_env: str = "AI_MODEL"
    key_env: str = "ANTHROPIC_API_KEY"
    max_retries: int = 3
    timeout_s: float = 60.0
    max_image_px: int = 2000


@dataclass(frozen=True)
class Config:
    """Complete, validated configuration."""

    machine: MachineConfig = field(default_factory=MachineConfig)
    motion: MotionConfig = field(default_factory=MotionConfig)
    pen: PenConfig = field(default_factory=PenConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    text: TextConfig = field(default_factory=TextConfig)
    ai: AiConfig = field(default_factory=AiConfig)

    @property
    def printable_area(self) -> tuple[float, float, float, float]:
        """Return the printable rectangle ``(x0, y0, x1, y1)`` in page-mm."""
        w, h = self.machine.page_size_mm
        m = self.machine.margins_mm
        return (m.left, m.top, w - m.right, h - m.bottom)

    @property
    def home_in_page_mm(self) -> tuple[float, float]:
        """Return the machine origin (homed position) expressed in the page frame."""
        ox, oy = self.machine.page_origin_mm
        return (-ox, self.machine.page_size_mm[1] + oy)


# --------------------------------------------------------------------------- parsing


def _build(cls: type[Any], data: Any, where: str) -> Any:
    """Recursively build dataclass ``cls`` from a mapping, type-checking each field."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"'{where}' must be a mapping, got {type(data).__name__}")
    known = {f.name: f for f in fields(cls)}
    unknown = sorted(set(data) - set(known))
    if unknown:
        raise ConfigError(f"unknown key(s) in '{where}': {', '.join(unknown)}")
    default = cls()
    kwargs: dict[str, Any] = {}
    for name in known:
        key = f"{where}.{name}" if where else name
        current = getattr(default, name)
        if name not in data:
            kwargs[name] = current
            continue
        kwargs[name] = _coerce(current, data[name], key)
    return cls(**kwargs)


def _coerce(default: Any, value: Any, key: str) -> Any:
    """Coerce ``value`` to the type of ``default``; raise ConfigError on mismatch."""
    if is_dataclass(default) and not isinstance(default, type):
        return _build(type(default), value, key)
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"'{key}' must be true or false, got {value!r}")
        return value
    if isinstance(default, int) and not isinstance(default, bool) and key != "camera.source":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"'{key}' must be an integer, got {value!r}")
        return value
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"'{key}' must be a number, got {value!r}")
        if not math.isfinite(value):
            raise ConfigError(f"'{key}' must be finite, got {value!r}")
        return float(value)
    if isinstance(default, tuple):
        if not isinstance(value, (list, tuple)) or not value:
            raise ConfigError(f"'{key}' must be a non-empty list of numbers, got {value!r}")
        if len(default) == 2 and len(value) != 2:
            raise ConfigError(f"'{key}' must have exactly 2 numbers [x, y], got {value!r}")
        out = []
        for v in value:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ConfigError(f"'{key}' must contain only numbers, got {v!r}")
            out.append(float(v))
        return tuple(out)
    if key == "camera.source":
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ConfigError(f"'{key}' must be a camera index or a URL, got {value!r}")
        return value
    if isinstance(default, str):
        if not isinstance(value, str):
            raise ConfigError(f"'{key}' must be a string, got {value!r}")
        return value
    raise ConfigError(f"'{key}': unsupported value {value!r}")  # pragma: no cover


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise ConfigError(message)


def validate_config(cfg: Config) -> Config:
    """Check ranges and cross-field consistency.

    Args:
        cfg: The configuration to check.

    Returns:
        The same configuration, for chaining.

    Raises:
        ConfigError: On the first problem found, with a hint on how to fix it.
    """
    m = cfg.machine
    bw, bh = m.bed_size_mm
    pw, ph = m.page_size_mm
    ox, oy = m.page_origin_mm
    mg = m.margins_mm
    _check(m.baud > 0, "machine.baud must be positive (GRBL 1.1 uses 115200)")
    _check(bw > 0 and bh > 0, "machine.bed_size_mm must be positive")
    _check(pw > 0 and ph > 0, "machine.page_size_mm must be positive")
    _check(ox >= 0 and oy >= 0, "machine.page_origin_mm must be >= 0 (machine frame)")
    _check(
        ox + pw <= bw and oy + ph <= bh,
        f"page ({pw}x{ph} mm) at origin ({ox}, {oy}) does not fit on the bed ({bw}x{bh} mm); "
        "reduce machine.page_origin_mm or check bed_size_mm",
    )
    for side in ("left", "right", "top", "bottom"):
        _check(getattr(mg, side) >= 0, f"machine.margins_mm.{side} must be >= 0")
    _check(mg.left + mg.right < pw, "left + right margins leave no printable width")
    _check(mg.top + mg.bottom < ph, "top + bottom margins leave no printable height")
    _check(m.connect_wait_s >= 0, "machine.connect_wait_s must be >= 0")
    _check(m.response_timeout_s > 0, "machine.response_timeout_s must be > 0")

    mo = cfg.motion
    _check(mo.draw_feed_mm_min > 0, "motion.draw_feed_mm_min must be > 0")
    _check(mo.travel_feed_mm_min > 0, "motion.travel_feed_mm_min must be > 0")
    _check(mo.accel_mm_s2 > 0, "motion.accel_mm_s2 must be > 0")
    _check(0 <= mo.coord_decimals <= 4, "motion.coord_decimals must be 0..4 (2 is plenty)")

    p = cfg.pen
    for name in ("up_cmd", "down_cmd"):
        cmd = getattr(p, name).strip()
        _check(bool(cmd), f"pen.{name} must not be empty")
        _check(len(cmd) < 40, f"pen.{name} is too long for a GRBL line")
        _check(not cmd.startswith("$"), f"pen.{name} must not be a $ setting")
    _check(p.up_cmd.strip() != p.down_cmd.strip(), "pen.up_cmd and pen.down_cmd must differ")
    _check(0 <= p.settle_s <= 5, "pen.settle_s must be between 0 and 5 seconds")

    pa = cfg.paths
    _check(pa.min_segment_mm >= 0, "paths.min_segment_mm must be >= 0")
    _check(pa.simplify_tolerance_mm >= 0, "paths.simplify_tolerance_mm must be >= 0")
    _check(pa.join_tolerance_mm >= 0, "paths.join_tolerance_mm must be >= 0")
    _check(
        pa.optimize in OPTIMIZE_METHODS,
        f"paths.optimize must be one of {', '.join(OPTIMIZE_METHODS)}, got {pa.optimize!r}",
    )
    _check(pa.two_opt_max_paths >= 0, "paths.two_opt_max_paths must be >= 0")

    c = cfg.camera
    _check(c.warp_px_per_mm > 0, "camera.warp_px_per_mm must be > 0")
    if isinstance(c.source, int):
        _check(c.source >= 0, "camera.source index must be >= 0")

    v = cfg.vision
    _check(v.mode in VISION_MODES, f"vision.mode must be one of {', '.join(VISION_MODES)}")
    _check(v.blur_ksize >= 1 and v.blur_ksize % 2 == 1, "vision.blur_ksize must be odd and >= 1")
    _check(
        v.adaptive_block >= 3 and v.adaptive_block % 2 == 1,
        "vision.adaptive_block must be odd and >= 3",
    )
    _check(v.min_contour_px >= 0, "vision.min_contour_px must be >= 0")
    _check(2 <= v.hatch.levels <= 16, "vision.hatch.levels must be 2..16")
    _check(v.hatch.spacing_mm > 0, "vision.hatch.spacing_mm must be > 0")

    t = cfg.text
    j = t.jitter
    _check(t.size_mm > 0, "text.size_mm must be > 0")
    _check(t.line_spacing_mm > 0, "text.line_spacing_mm must be > 0")
    _check(
        j.baseline_mm >= 0 and j.scale_pct >= 0 and j.rotate_deg >= 0,
        "text.jitter values must be >= 0",
    )
    _check(j.scale_pct < 50 and j.rotate_deg < 30, "text.jitter is too large to stay legible")
    _check(
        t.first_line_y_mm - t.size_mm >= mg.top,
        f"text.first_line_y_mm ({t.first_line_y_mm}) minus the cap height ({t.size_mm}) "
        f"crosses the top margin ({mg.top}); increase first_line_y_mm",
    )
    _check(
        t.first_line_y_mm + t.size_mm <= ph - mg.bottom,
        "text.first_line_y_mm leaves no room for a single line above the bottom margin",
    )

    a = cfg.ai
    _check(bool(a.model_env) and bool(a.key_env), "ai.model_env and ai.key_env must be set")
    _check(a.max_retries >= 0, "ai.max_retries must be >= 0")
    _check(a.timeout_s > 0, "ai.timeout_s must be > 0")
    _check(a.max_image_px >= 256, "ai.max_image_px must be >= 256")
    return cfg


def config_from_dict(data: dict[str, Any] | None) -> Config:
    """Build and validate a :class:`Config` from a parsed YAML mapping.

    Args:
        data: Mapping with any subset of the sections in ``machine.yaml``.

    Returns:
        A validated, immutable configuration.

    Raises:
        ConfigError: If a key is unknown, a value has the wrong type, or a check fails.
    """
    cfg = _build(Config, data, "")
    assert isinstance(cfg, Config)
    return validate_config(cfg)


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
    """Load ``machine.yaml`` from ``path``.

    Args:
        path: Path to the YAML file.

    Returns:
        A validated configuration.

    Raises:
        ConfigError: If the file is missing, is not valid YAML, or fails validation.
    """
    p = Path(path)
    if not p.is_file():
        raise ConfigError(
            f"config file not found: {p}. Run from the repo root or pass --config PATH "
            "(the default is config/machine.yaml)."
        )
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{p} is not valid YAML: {exc}") from exc
    if data is not None and not isinstance(data, dict):
        raise ConfigError(f"{p} must contain a mapping of sections at the top level")
    try:
        cfg = config_from_dict(data)
    except ConfigError as exc:
        raise ConfigError(f"{p}: {exc}") from exc
    logger.debug("loaded config from %s", p)
    return cfg
