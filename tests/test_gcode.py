from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from plotter import gcode as G
from plotter.config import Config
from plotter.errors import GcodeError, OutOfBoundsError


def _drawing() -> list[np.ndarray]:
    sq = np.array([[30, 30], [180, 30], [180, 260], [30, 260], [30, 30]], dtype=np.float64)
    t = np.linspace(0, 2 * np.pi, 60)
    circle = np.column_stack([105 + 40 * np.cos(t), 150 + 40 * np.sin(t)])
    return [sq, circle, np.array([[100.0, 100.0]])]


def _pen_states(lines: list[str], cfg: Config) -> list[tuple[str, bool]]:
    state = None
    out = []
    for line in lines:
        if line == cfg.pen.up_cmd:
            state = False
        elif line == cfg.pen.down_cmd:
            state = True
        elif line.startswith(("G0", "G1")) and " X" in f" {line}":
            assert state is not None, "motion before pen state known"
            out.append((line[:2], state))
    return out


def test_structure_header_footer(cfg: Config) -> None:
    lines = G.to_gcode(_drawing(), cfg)
    assert lines[:6] == ["G21", "G90", "G17", "G94", cfg.pen.up_cmd, "G4 P0.15"]
    assert lines[-4:] == [cfg.pen.up_cmd, "G4 P0.15", "G0 X0 Y0", "M5"]
    assert sum(1 for line in lines if line == cfg.pen.down_cmd) == 3


def test_pen_up_before_every_g0(cfg: Config) -> None:
    lines = G.to_gcode(_drawing(), cfg)
    for kind, down in _pen_states(lines, cfg):
        if kind == "G0":
            assert not down
    # first pen command is up, last is up
    pens = [line for line in lines if line in (cfg.pen.up_cmd, cfg.pen.down_cmd)]
    assert pens[0] == cfg.pen.up_cmd
    assert pens[-1] == cfg.pen.up_cmd


def test_settle_after_every_pen_move(cfg: Config) -> None:
    lines = G.to_gcode(_drawing(), cfg)
    for i, line in enumerate(lines):
        if line in (cfg.pen.up_cmd, cfg.pen.down_cmd):
            assert lines[i + 1] == "G4 P0.15"


def test_lines_short_and_no_arcs(cfg: Config) -> None:
    lines = G.to_gcode(_drawing(), cfg)
    assert all(len(line) < 70 for line in lines)
    assert not any(re.match(r"G0?[23](\D|$)", line) for line in lines)


def test_feed_and_rounding(cfg: Config) -> None:
    lines = G.to_gcode([np.array([[20.004, 15.0], [20.333333, 15.0]])], cfg)
    g1 = [line for line in lines if line.startswith("G1 ")]
    assert g1 == ["G1 X30.33 Y292 F3000"]
    g0 = [line for line in lines if line.startswith("G0")]
    assert g0[0] == "G0 X30 Y292"


def test_duplicate_points_collapsed_and_dot(cfg: Config) -> None:
    p = np.array([[50, 50], [50.001, 50.001], [60, 50]], dtype=np.float64)
    lines = G.to_gcode([p], cfg)
    assert sum(1 for line in lines if line.startswith("G1 ")) == 1
    dot = G.to_gcode([np.array([[70.0, 70.0], [70.001, 70.0]])], cfg)
    assert cfg.pen.down_cmd in dot
    assert not any(line.startswith("G1 ") for line in dot)


def test_every_generated_job_validates(cfg: Config) -> None:
    G.validate(G.to_gcode(_drawing(), cfg), cfg)
    G.validate(G.to_gcode([], cfg), cfg)


def test_out_of_bounds_raises(cfg: Config) -> None:
    with pytest.raises(OutOfBoundsError, match="outside the bed"):
        G.to_gcode([np.array([[0.0, 0.0], [250.0, 0.0]])], cfg)
    with pytest.raises(OutOfBoundsError):
        G.to_gcode([np.array([[0.0, -40.0], [10.0, 0.0]])], cfg)


@pytest.mark.parametrize(
    ("lines", "err", "match"),
    [
        (["M3 S30", "G4 P0.15", "M3 S90", "G0 X10 Y10", "M3 S30"], GcodeError, "pen is down"),
        (["G0 X10 Y10"], GcodeError, "before the pen"),
        (["M3 S30", "G2 X10 Y10 I5 J0"], GcodeError, "arcs"),
        (["M3 S30", "G91", "G0 X10"], GcodeError, "relative"),
        (["M3 S30", "G20"], GcodeError, "inch"),
        (["M3 S30", "M3 S90", "G1 X10 Y10 F100"], GcodeError, "ends with the pen down"),
        (["M3 S30", "G1 X10 Y10 F0"], GcodeError, "feed"),
        (["M3 S30", "M3 S50"], GcodeError, "spindle"),
        (["M3 S30", "$H"], GcodeError, "system"),
        (["M3 S30", "G0 X10 Y10 Z5"], GcodeError, "Z moves"),
        (["M3 S30", "HELLO"], GcodeError, "cannot parse"),
        (["M3 S30", "G1 X1 Y1 " + "X1 " * 25], GcodeError, "< 70"),
        (["M3 S30", "G0 X10 Y400"], OutOfBoundsError, "outside"),
        (["M3 S30", "G0 X-1 Y10"], OutOfBoundsError, "outside"),
        (["M3 S30", "G4 P-1"], GcodeError, "dwell"),
    ],
)
def test_validate_rejects(cfg: Config, lines: list[str], err: type[Exception], match: str) -> None:
    with pytest.raises(err, match=match):
        G.validate(lines, cfg)


def test_validate_accepts_modal_moves_and_comments(cfg: Config) -> None:
    lines = [
        "; header comment",
        "G21 G90",
        "M3  s30 (pen up, odd spacing)",
        "G0 X10 Y10",
        "M3 S90",
        "G1 X20 F1000",
        "Y20",
        "M3 S30",
        "G0 X0 Y0",
        "M5",
        "",
    ]
    G.validate(lines, cfg)
    moves, dwell = G.interpret(lines, cfg)
    assert dwell == 0
    assert [m.pen_down for m in moves] == [False, True, True, False]
    assert moves[2].x1 == 20
    assert moves[2].y1 == 20


def test_custom_pen_commands_respected(cfg: Config) -> None:
    c = replace(cfg, pen=replace(cfg.pen, up_cmd="M3 S10", down_cmd="M3 S120"))
    lines = G.to_gcode(_drawing(), c)
    G.validate(lines, c)
    with pytest.raises(GcodeError):
        G.validate(lines, cfg)


def test_job_distances(cfg: Config) -> None:
    lines = G.to_gcode([np.array([[30.0, 30.0], [40.0, 30.0]])], cfg)
    st = G.job_distances(lines, cfg)
    assert st["paths"] == 1
    assert st["pen_down_mm"] == pytest.approx(10.0)
    assert st["pen_up_mm"] > 0


def test_write_and_read(tmp_path: Path, cfg: Config) -> None:
    lines = G.to_gcode(_drawing(), cfg)
    out = G.write_gcode(
        lines,
        tmp_path / "sub" / "job.gcode",
        source="test.png",
        stats={"paths": 3, "pen_down_mm": 1000.0, "pen_up_mm": 200.0},
        est_time_s=125,
    )
    text = out.read_text(encoding="utf-8")
    assert text.startswith("; ai-pen-plotter")
    assert "; source: test.png" in text
    assert "estimated time: 2.1 min" in text
    back = G.read_gcode(out)
    G.validate(back, cfg)
    assert back[-len(lines) :] == lines
    with pytest.raises(GcodeError, match="not found"):
        G.read_gcode(tmp_path / "missing.gcode")


def test_fmt() -> None:
    assert G.fmt(1.0, 2) == "1"
    assert G.fmt(1.50, 2) == "1.5"
    assert G.fmt(-0.001, 2) == "0"
    assert G.fmt(12.345, 0) == "12"
