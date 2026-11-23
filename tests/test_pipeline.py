from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from plotter import pipeline
from plotter.config import Config
from plotter.errors import GrblError, OutOfBoundsError
from plotter.fake_grbl import FakeGrbl
from plotter.gcode import validate

from .conftest import REPO_ROOT


def test_draw_job_from_array(cfg: Config, lineart_img: np.ndarray) -> None:
    job = pipeline.draw_job(lineart_img, cfg, mode="lineart", name="arr")
    validate(job.gcode, cfg)
    assert job.stats["paths"] > 0
    assert job.est_time_s > 0
    assert job.source == "arr"


def test_clean_drawing_reduces_paths(cfg: Config, lineart_img: np.ndarray) -> None:
    from plotter.vision import image_to_drawing

    raw = image_to_drawing(lineart_img, cfg, "lineart")
    clean = pipeline.clean_drawing(raw, cfg)
    assert len(clean) <= len(raw)
    assert sum(len(p) for p in clean) < sum(len(p) for p in raw)


def test_write_jobs_names(cfg: Config) -> None:
    jobs = pipeline.write_jobs("hello " * 3000, cfg, name="essay")
    assert len(jobs) > 1
    assert [j.name for j in jobs[:2]] == ["essay_p1", "essay_p2"]
    assert pipeline.write_jobs("one line", cfg, name="n")[0].name == "n"
    assert pipeline.write_jobs("", cfg) == []


def test_numbered() -> None:
    assert pipeline.numbered("a/job.gcode", 1) == [Path("a/job.gcode")]
    assert pipeline.numbered("job.gcode", 2) == [Path("job_p1.gcode"), Path("job_p2.gcode")]


def test_send_job_validates_first(cfg: Config) -> None:
    with pytest.raises(OutOfBoundsError):
        pipeline.send_job(["M3 S30", "G0 X999 Y0"], cfg, dry_run=True)


def test_send_job_dry_run_streams_to_emulator(cfg: Config) -> None:
    job = pipeline.write_jobs("Hi", cfg)[0]
    fake = FakeGrbl()
    res = pipeline.send_job(job.gcode, cfg, dry_run=True, serial=fake)
    assert res.acked == len(job.gcode)
    assert fake.received == job.gcode
    assert not fake.is_open


def test_send_job_ctrl_c_aborts(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    job = pipeline.write_jobs("Hi", cfg)[0]
    fake = FakeGrbl()

    def interrupt(acked: int, total: int) -> None:
        if acked == 5:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        pipeline.send_job(job.gcode, cfg, dry_run=True, serial=fake, progress_cb=interrupt)
    assert fake.realtime[-2:] == ["!", "\x18"]


def test_stream_controller_completes(cfg: Config) -> None:
    job = pipeline.write_jobs("Hello", cfg)[0]
    ctl = pipeline.StreamController(job.gcode, cfg, dry_run=True)
    ctl.start()
    ctl.join(10)
    assert not ctl.running
    assert ctl.error is None
    assert ctl.result is not None
    assert ctl.acked == ctl.total == len(job.gcode)


class SlowFake(FakeGrbl):
    def readline(self) -> bytes:
        time.sleep(0.002)
        return super().readline()


def test_stream_controller_pause_resume_abort(cfg: Config) -> None:
    job = pipeline.write_jobs("Hello world " * 20, cfg)[0]
    fake = SlowFake()
    ctl = pipeline.StreamController(job.gcode, cfg, dry_run=True, serial=fake)
    ctl.start()
    time.sleep(0.05)
    ctl.pause()
    ctl.resume()
    ctl.abort()
    ctl.join(10)
    assert ctl.aborted
    assert isinstance(ctl.error, GrblError)  # GRBL's reset banner stops the stream
    assert {"!", "~", "\x18"} <= set(fake.realtime)


def test_copy_page_pipeline(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    import pytesseract

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(pytesseract, "image_to_string", lambda im: "Copied text")
    text, jobs = pipeline.copy_page_jobs(REPO_ROOT / "samples" / "page_photo.jpg", cfg)
    assert text == "Copied text"
    assert len(jobs) == 1
    validate(jobs[0].gcode, cfg)


def test_save_job(tmp_path: Path, cfg: Config) -> None:
    job = pipeline.write_jobs("Hi", cfg)[0]
    out = pipeline.save_job(job, cfg, tmp_path / "x.gcode", tmp_path / "x.png")
    assert out.exists()
    assert (tmp_path / "x.png").exists()
