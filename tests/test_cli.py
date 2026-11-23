from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from plotter import cli
from plotter.config import load_config
from plotter.gcode import read_gcode, validate

from .conftest import REPO_ROOT

runner = CliRunner()
CFG = str(REPO_ROOT / "config" / "machine.yaml")
SAMPLES = REPO_ROOT / "samples"


def run(*args: str, input: str | None = None) -> tuple[int, str]:
    res = runner.invoke(cli.app, [*args], input=input)
    return res.exit_code, res.output


def test_help_and_version() -> None:
    code, out = run("--help")
    assert code == 0
    for cmd in ("draw", "write", "copy-page", "send", "jog", "pen", "preview"):
        assert cmd in out
    code, out = run("version")
    assert code == 0
    assert "ai-pen-plotter" in out


def test_draw_writes_valid_gcode_and_preview(tmp_path: Path) -> None:
    out, png = tmp_path / "job.gcode", tmp_path / "p.png"
    code, text = run(
        "draw",
        str(SAMPLES / "lineart.png"),
        "--mode",
        "lineart",
        "--out",
        str(out),
        "--preview",
        str(png),
        "--config",
        CFG,
    )
    assert code == 0, text
    validate(read_gcode(out), load_config(CFG))
    assert png.stat().st_size > 10_000
    assert "est. time" in text


@pytest.mark.parametrize("mode", ["outline", "hatch"])
def test_draw_other_modes(tmp_path: Path, mode: str) -> None:
    out = tmp_path / f"{mode}.gcode"
    code, text = run("draw", str(SAMPLES / "photo.jpg"), "-m", mode, "-o", str(out), "-c", CFG)
    assert code == 0, text
    assert out.exists()


def test_write_multi_page_naming(tmp_path: Path) -> None:
    txt = tmp_path / "long.txt"
    txt.write_text(("word " * 40 + "\n") * 60, encoding="utf-8")
    out = tmp_path / "job.gcode"
    code, text = run(
        "write", str(txt), "--out", str(out), "--preview", str(tmp_path / "pv.png"), "-c", CFG
    )
    assert code == 0, text
    assert not out.exists()
    assert (tmp_path / "job_p1.gcode").exists()
    assert (tmp_path / "job_p2.gcode").exists()
    assert (tmp_path / "pv_p2.png").exists()


def test_write_single_page_and_empty(tmp_path: Path) -> None:
    out = tmp_path / "t.gcode"
    code, _ = run("write", str(SAMPLES / "sample_text.txt"), "-o", str(out), "-c", CFG)
    assert code == 0
    assert out.exists()
    empty = tmp_path / "e.txt"
    empty.write_text("  \n", encoding="utf-8")
    code, text = run("write", str(empty), "-o", str(out), "-c", CFG)
    assert code == 1
    assert "no text" in text
    code, text = run("write", str(tmp_path / "missing.txt"), "-o", str(out), "-c", CFG)
    assert code == 1


def test_preview_and_send_dry_run(tmp_path: Path) -> None:
    out = tmp_path / "job.gcode"
    assert run("draw", str(SAMPLES / "lineart.png"), "-o", str(out), "-c", CFG)[0] == 0
    code, text = run("preview", str(out), "--out", str(tmp_path / "pv.png"), "-c", CFG)
    assert code == 0, text
    assert "min" in text
    code, text = run("send", str(out), "--dry-run", "-c", CFG)
    assert code == 0, text
    assert "dry-run" in text
    assert "done" in text


def test_send_refuses_out_of_bounds(tmp_path: Path) -> None:
    bad = tmp_path / "bad.gcode"
    bad.write_text("M3 S30\nG0 X500 Y10\n", encoding="utf-8")
    code, text = run("send", str(bad), "--dry-run", "-c", CFG)
    assert code == 1
    assert "outside the bed" in text


def test_send_real_requires_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "job.gcode"
    assert run("draw", str(SAMPLES / "lineart.png"), "-o", str(out), "-c", CFG)[0] == 0

    def must_not_send(*a: object, **k: object) -> None:
        raise AssertionError("send_job must not run when the user says no")

    monkeypatch.setattr(cli.pipeline, "send_job", must_not_send)
    code, text = run("send", str(out), "-c", CFG, input="n\n")
    assert code == 1
    assert "Physical stop" in text


@pytest.mark.parametrize(
    "args",
    [
        ["home", "--dry-run"],
        ["unlock", "--dry-run"],
        ["status", "--dry-run"],
        ["settings", "--dry-run"],
        ["pen", "up", "--dry-run"],
        ["pen", "down", "--dry-run"],
        ["jog", "--x", "10", "--y", "-5", "--dry-run"],
    ],
)
def test_machine_commands_dry_run(args: list[str]) -> None:
    code, text = run(*args, "-c", CFG)
    assert code == 0, text
    assert "dry-run" in text


def test_status_prints_state() -> None:
    _, text = run("status", "--dry-run", "-c", CFG)
    assert "Idle" in text


def test_ocr_and_copy_page_tesseract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pytesseract

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(pytesseract, "image_to_string", lambda im: "Hello plotter\nline two\n")
    out = tmp_path / "t.txt"
    code, text = run(
        "ocr", str(SAMPLES / "page_photo.jpg"), "--engine", "auto", "-o", str(out), "-c", CFG
    )
    assert code == 0, text
    assert out.read_text(encoding="utf-8").startswith("Hello plotter")
    code, text = run("ocr", str(SAMPLES / "page_photo.jpg"), "--no-flatten", "-c", CFG)
    assert "line two" in text
    g = tmp_path / "copy.gcode"
    code, text = run(
        "copy-page",
        str(SAMPLES / "page_photo.jpg"),
        "-o",
        str(g),
        "--text-out",
        str(tmp_path / "copy.txt"),
        "-c",
        CFG,
    )
    assert code == 0, text
    validate(read_gcode(g), load_config(CFG))
    assert (tmp_path / "copy.txt").exists()


def test_copy_page_with_no_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pytesseract

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(pytesseract, "image_to_string", lambda im: "  \n")
    code, text = run(
        "copy-page", str(SAMPLES / "page_photo.jpg"), "-o", str(tmp_path / "c.gcode"), "-c", CFG
    )
    assert code == 1
    assert "no text" in text


def test_capture_and_flatten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = np.full((60, 80, 3), 200, np.uint8)
    monkeypatch.setattr(cli.camera, "capture", lambda src: frame)
    code, text = run("capture", "--out", str(tmp_path / "c.jpg"), "-c", CFG)
    assert code == 0, text
    assert (tmp_path / "c.jpg").exists()
    code, text = run(
        "flatten", str(SAMPLES / "page_photo.jpg"), "-o", str(tmp_path / "f.png"), "-c", CFG
    )
    assert code == 0, text
    assert "2100×2970" in text


def test_bad_config_and_missing_image(tmp_path: Path) -> None:
    bad = tmp_path / "m.yaml"
    bad.write_text("motion: {draw_feed_mm_min: -1}\n", encoding="utf-8")
    code, text = run(
        "draw", str(SAMPLES / "lineart.png"), "-o", str(tmp_path / "x.gcode"), "-c", str(bad)
    )
    assert code == 1
    assert "draw_feed_mm_min" in text
    code, text = run("draw", str(tmp_path / "nope.png"), "-o", str(tmp_path / "x.gcode"), "-c", CFG)
    assert code == 1
    assert "not found" in text
