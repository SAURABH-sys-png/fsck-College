"""Compose the stages into jobs. ``cli.py`` and the Streamlit app are thin wrappers over this.

Every job goes through :func:`plotter.gcode.to_gcode`, which validates it, and
:func:`send_job` validates again before anything reaches a serial port.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from plotter import paths as geo
from plotter.config import Config
from plotter.fake_grbl import FakeGrbl
from plotter.gcode import to_gcode, validate, write_gcode
from plotter.grbl import Grbl, ProgressCallback, SerialLike, StreamResult
from plotter.images import Image, load_image
from plotter.ocr import MessagesClient, read_text
from plotter.perspective import flatten
from plotter.preview import estimate_time, render
from plotter.text_layout import layout_text
from plotter.types import Drawing, Job
from plotter.vision import image_to_drawing

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- inputs


def prepare_image(image: Image | str | Path, cfg: Config, *, flatten_page: bool = False) -> Image:
    """Load (if a path) and optionally flatten the page in a photo."""
    img = load_image(image) if isinstance(image, (str, Path)) else image
    if flatten_page:
        w, h = cfg.machine.page_size_mm
        img = flatten(img, cfg.camera.warp_px_per_mm, (w, h))
    return img


# --------------------------------------------------------------------------- jobs


def clean_drawing(drawing: Drawing, cfg: Config) -> Drawing:
    """Simplify, drop tiny bits, join touching strokes and order them for little travel."""
    p = cfg.paths
    d = geo.simplify(drawing, p.simplify_tolerance_mm)
    d = geo.dedupe_points(d, p.min_segment_mm)
    d = geo.filter_short(d, p.min_segment_mm)
    d = geo.join_paths(d, p.join_tolerance_mm)
    return geo.order_paths(d, p.optimize, cfg.home_in_page_mm, p.two_opt_max_paths)


def build_job(drawing: Drawing, cfg: Config, name: str, source: str = "") -> Job:
    """Clean a drawing and turn it into a validated G-code job with stats and a time estimate.

    Raises:
        OutOfBoundsError: If the drawing does not fit on the bed.
    """
    d = clean_drawing(drawing, cfg)
    lines = to_gcode(d, cfg)
    job = Job(
        name=name,
        drawing=d,
        gcode=lines,
        stats=geo.stats(d, cfg.home_in_page_mm),
        est_time_s=estimate_time(lines, cfg),
        source=source,
    )
    logger.info(
        "job %s: %d paths, %.2f m pen-down, ~%.1f min",
        name,
        job.stats["paths"],
        job.stats["pen_down_mm"] / 1000,
        job.est_time_s / 60,
    )
    return job


def draw_job(
    image: Image | str | Path,
    cfg: Config,
    *,
    mode: str | None = None,
    flatten_page: bool = False,
    name: str = "drawing",
) -> Job:
    """DRAW mode: image → strokes → job."""
    source = str(image) if isinstance(image, (str, Path)) else name
    img = prepare_image(image, cfg, flatten_page=flatten_page)
    drawing = image_to_drawing(img, cfg, mode)
    return build_job(drawing, cfg, name, source)


def write_jobs(text: str, cfg: Config, *, name: str = "text", source: str = "") -> list[Job]:
    """WRITE mode: text → one job per A4 page."""
    pages = layout_text(text, cfg)
    many = len(pages) > 1
    return [
        build_job(page, cfg, f"{name}_p{i}" if many else name, source)
        for i, page in enumerate(pages, 1)
    ]


def ocr_image(
    image: Image | str | Path,
    cfg: Config,
    *,
    engine: str = "auto",
    flatten_page: bool = True,
    client: MessagesClient | None = None,
) -> str:
    """Read the text on a (photographed) page."""
    img = prepare_image(image, cfg, flatten_page=flatten_page)
    return read_text(img, cfg, engine, client)


def copy_page_jobs(
    image: Image | str | Path,
    cfg: Config,
    *,
    engine: str = "auto",
    flatten_page: bool = True,
    client: MessagesClient | None = None,
    name: str = "copy",
) -> tuple[str, list[Job]]:
    """Copy a page: flatten → OCR → layout → G-code. Returns the text and the page jobs."""
    text = ocr_image(image, cfg, engine=engine, flatten_page=flatten_page, client=client)
    source = str(image) if isinstance(image, (str, Path)) else name
    return text, write_jobs(text, cfg, name=name, source=source)


# --------------------------------------------------------------------------- outputs


def numbered(path: str | Path, n: int) -> list[Path]:
    """``job.gcode`` → ``[job.gcode]`` for one page, else ``job_p1.gcode, job_p2.gcode…``."""
    p = Path(path)
    if n <= 1:
        return [p]
    return [p.with_name(f"{p.stem}_p{i}{p.suffix}") for i in range(1, n + 1)]


def save_job(
    job: Job, cfg: Config, gcode_path: str | Path, preview_path: str | Path | None = None
) -> Path:
    """Write a job's G-code (with header) and optionally its PNG preview."""
    out = write_gcode(
        job.gcode, gcode_path, source=job.source, stats=job.stats, est_time_s=job.est_time_s
    )
    if preview_path is not None:
        render(job.drawing, preview_path, cfg, title=job.name)
    return out


# --------------------------------------------------------------------------- streaming


def send_job(
    lines: list[str],
    cfg: Config,
    *,
    port: str | None = None,
    dry_run: bool = True,
    progress_cb: ProgressCallback | None = None,
    serial: SerialLike | None = None,
) -> StreamResult:
    """Validate and stream a job. Dry-run streams to an in-memory :class:`FakeGrbl`.

    Ctrl+C during a real job triggers feed hold + soft reset before re-raising.

    Raises:
        OutOfBoundsError, GcodeError: If the job fails validation (nothing is sent).
        GrblError, GrblAlarm: From the machine.
    """
    validate(lines, cfg)
    if dry_run:
        serial = serial or FakeGrbl()
    grbl = Grbl(cfg, port, serial=serial, sleep=_no_sleep if dry_run else time.sleep)
    with grbl:
        try:
            return grbl.stream(lines, progress_cb)
        except KeyboardInterrupt:
            grbl.abort()
            raise


def _no_sleep(_seconds: float) -> None:
    """Dry-runs talk to FakeGrbl, which needs no wake-up delay."""


@dataclass
class StreamController:
    """Run :func:`send_job`-style streaming in a background thread (for the GUI).

    ``pause``/``resume``/``abort`` send GRBL real-time commands from the caller's thread.
    """

    lines: list[str]
    cfg: Config
    port: str | None = None
    dry_run: bool = True
    serial: SerialLike | None = None
    acked: int = 0
    total: int = 0
    result: StreamResult | None = None
    error: BaseException | None = None
    aborted: bool = False
    _grbl: Grbl | None = field(default=None, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)

    def start(self) -> None:
        """Validate, connect and start streaming in a daemon thread."""
        validate(self.lines, self.cfg)
        serial = self.serial or (FakeGrbl() if self.dry_run else None)
        self._grbl = Grbl(
            self.cfg, self.port, serial=serial, sleep=_no_sleep if self.dry_run else time.sleep
        )
        self._grbl.connect()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        assert self._grbl is not None
        try:
            self.result = self._grbl.stream(self.lines, self._progress)
        except Exception as exc:  # reported to the GUI via .error
            self.error = exc
        finally:
            self._grbl.close()

    def _progress(self, acked: int, total: int) -> None:
        self.acked, self.total = acked, total

    @property
    def running(self) -> bool:
        """True while the streaming thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def pause(self) -> None:
        """Feed hold."""
        if self._grbl is not None and self.running:
            self._grbl.hold()

    def resume(self) -> None:
        """Resume after a hold."""
        if self._grbl is not None and self.running:
            self._grbl.resume()

    def abort(self) -> None:
        """Feed hold + soft reset; the streaming thread then stops with an error."""
        if self._grbl is not None and self.running:
            self.aborted = True
            self._grbl.abort()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the streaming thread."""
        if self._thread is not None:
            self._thread.join(timeout)
