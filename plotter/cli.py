"""``plotter`` command-line interface (Typer + rich). A thin wrapper over :mod:`plotter.pipeline`.

Every command accepts ``--config`` (default ``config/machine.yaml``). Commands that move the
machine ask for confirmation unless ``--yes`` is given, and accept ``--dry-run`` to talk to
the in-memory GRBL emulator instead of the serial port.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from plotter import __version__, camera, pipeline
from plotter.config import DEFAULT_CONFIG_PATH, Config, load_config
from plotter.errors import PlotterError
from plotter.fake_grbl import FakeGrbl
from plotter.gcode import job_distances, read_gcode, validate
from plotter.grbl import Grbl
from plotter.images import save_image
from plotter.preview import estimate_time, render
from plotter.types import Job

app = typer.Typer(
    name="plotter",
    help="A4 pen plotter: images and handwriting → G-code → GRBL.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
console = Console()
err_console = Console(stderr=True)
logger = logging.getLogger("plotter.cli")

STOP_HINT = (
    "Physical stop: press the Arduino reset button or unplug the 12 V supply. "
    "Software stop: Ctrl+C (feed hold + soft reset)."
)


class Mode(str, Enum):
    """Vision modes."""

    lineart = "lineart"
    outline = "outline"
    hatch = "hatch"


class Engine(str, Enum):
    """OCR engines."""

    auto = "auto"
    ai = "ai"
    tesseract = "tesseract"


class PenState(str, Enum):
    """Pen positions."""

    up = "up"
    down = "down"


ConfigOpt = Annotated[
    Path, typer.Option("--config", "-c", help="Machine config YAML.", show_default=True)
]
PortOpt = Annotated[
    str | None, typer.Option("--port", "-p", help="Serial port, or 'auto' (default: config).")
]
DryRunOpt = Annotated[
    bool, typer.Option("--dry-run", help="Use the built-in GRBL emulator; nothing is sent.")
]
YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")]
PreviewOpt = Annotated[Path | None, typer.Option("--preview", help="Also render a PNG preview.")]


# --------------------------------------------------------------------------- helpers


@contextmanager
def _errors() -> Iterator[None]:
    """Turn expected errors into a red message and exit code 1."""
    try:
        yield
    except PlotterError as exc:
        err_console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(1) from exc
    except KeyboardInterrupt as exc:
        err_console.print("[yellow]Interrupted.[/]")
        raise typer.Exit(130) from exc


def _load(path: Path) -> Config:
    return load_config(path)


def _job_table(job: Job, out: Path) -> Table:
    t = Table(show_header=False, box=None, pad_edge=False)
    t.add_row("[bold]G-code[/]", str(out))
    t.add_row("paths", str(job.stats["paths"]))
    t.add_row("pen-down", f"{job.stats['pen_down_mm'] / 1000:.2f} m")
    t.add_row("travel", f"{job.stats['pen_up_mm'] / 1000:.2f} m")
    t.add_row("lines", str(len(job.gcode)))
    t.add_row("est. time", f"{job.est_time_s / 60:.1f} min")
    return t


def _save_jobs(jobs: list[Job], cfg: Config, out: Path, preview: Path | None) -> None:
    outs = pipeline.numbered(out, len(jobs))
    previews = pipeline.numbered(preview, len(jobs)) if preview else [None] * len(jobs)
    for job, o, pv in zip(jobs, outs, previews, strict=True):
        pipeline.save_job(job, cfg, o, pv)
        console.print(_job_table(job, o))
        if pv:
            console.print(f"[bold]preview[/]  {pv}")


def _open(cfg: Config, port: str | None, dry_run: bool) -> Grbl:
    if dry_run:
        console.print("[cyan]dry-run:[/] using the GRBL emulator, nothing is sent")
        return Grbl(cfg, "dry-run", serial=FakeGrbl(), sleep=lambda _s: None)
    return Grbl(cfg, port)


def _confirm(question: str, yes: bool, dry_run: bool) -> None:
    if yes or dry_run:
        return
    console.print(f"[yellow]{STOP_HINT}[/]")
    if not typer.confirm(question, default=False):
        raise typer.Exit(1)


# --------------------------------------------------------------------------- app


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")] = False,
) -> None:
    """A4 pen plotter: images and handwriting → G-code → GRBL."""
    load_dotenv()
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=err_console, show_path=False)],
        force=True,
    )


@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"ai-pen-plotter {__version__}")


@app.command()
def capture(
    out: Annotated[Path, typer.Option("--out", "-o", help="Where to save the photo.")],
    source: Annotated[
        str | None, typer.Option("--source", help="Camera index or URL (default: config).")
    ] = None,
    config: ConfigOpt = DEFAULT_CONFIG_PATH,
) -> None:
    """Take a photo with the webcam or phone camera."""
    with _errors():
        cfg = _load(config)
        frame = camera.capture(source if source is not None else cfg.camera.source)
        camera.save(frame, out)
        console.print(f"saved {frame.shape[1]}×{frame.shape[0]} photo to {out}")


@app.command(name="flatten")
def flatten_cmd(
    image: Annotated[Path, typer.Argument(help="Photo of a page.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Flattened A4 image.")],
    config: ConfigOpt = DEFAULT_CONFIG_PATH,
) -> None:
    """Find the sheet in a photo and warp it to a flat A4 image."""
    with _errors():
        cfg = _load(config)
        flat = pipeline.prepare_image(image, cfg, flatten_page=True)
        save_image(flat, out)
        console.print(f"saved {flat.shape[1]}×{flat.shape[0]} flattened page to {out}")


@app.command()
def draw(
    image: Annotated[Path, typer.Argument(help="Image to draw.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output G-code file.")],
    mode: Annotated[
        Mode | None, typer.Option("--mode", "-m", help="Vision mode (default: config).")
    ] = None,
    preview: PreviewOpt = None,
    flatten: Annotated[
        bool, typer.Option("--flatten", help="Flatten a photographed page first.")
    ] = False,
    config: ConfigOpt = DEFAULT_CONFIG_PATH,
) -> None:
    """DRAW: turn an image into G-code (lineart, outline or hatch)."""
    with _errors():
        cfg = _load(config)
        job = pipeline.draw_job(
            image, cfg, mode=mode.value if mode else None, flatten_page=flatten, name=image.stem
        )
        _save_jobs([job], cfg, out, preview)


@app.command()
def ocr(
    image: Annotated[Path, typer.Argument(help="Photo or scan of a page.")],
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Write the text here.")] = None,
    engine: Annotated[Engine, typer.Option("--engine", "-e")] = Engine.auto,
    flatten: Annotated[
        bool, typer.Option("--flatten/--no-flatten", help="Flatten the page first.")
    ] = True,
    config: ConfigOpt = DEFAULT_CONFIG_PATH,
) -> None:
    """Read the text in a photo (Claude vision, Tesseract fallback)."""
    with _errors():
        cfg = _load(config)
        text = pipeline.ocr_image(image, cfg, engine=engine.value, flatten_page=flatten)
        if out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text + "\n", encoding="utf-8")
            console.print(f"wrote {len(text)} characters to {out}")
        else:
            console.print(text, markup=False, highlight=False)


@app.command()
def write(
    text_file: Annotated[Path, typer.Argument(help="UTF-8 text file to write by hand.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output G-code (pages get _pN).")],
    preview: PreviewOpt = None,
    config: ConfigOpt = DEFAULT_CONFIG_PATH,
) -> None:
    """WRITE: lay text out in a single-stroke font on ruled lines."""
    with _errors():
        cfg = _load(config)
        if not text_file.is_file():
            raise PlotterError(f"text file not found: {text_file}")
        text = text_file.read_text(encoding="utf-8")
        jobs = pipeline.write_jobs(text, cfg, name=text_file.stem, source=str(text_file))
        if not jobs:
            raise PlotterError(f"{text_file} contains no text")
        _save_jobs(jobs, cfg, out, preview)


@app.command(name="copy-page")
def copy_page(
    image: Annotated[Path, typer.Argument(help="Photo of a page to copy.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output G-code (pages get _pN).")],
    preview: PreviewOpt = None,
    engine: Annotated[Engine, typer.Option("--engine", "-e")] = Engine.auto,
    text_out: Annotated[
        Path | None, typer.Option("--text-out", help="Also save the recognised text.")
    ] = None,
    config: ConfigOpt = DEFAULT_CONFIG_PATH,
) -> None:
    """Copy a page: flatten → OCR → layout → G-code."""
    with _errors():
        cfg = _load(config)
        text, jobs = pipeline.copy_page_jobs(image, cfg, engine=engine.value, name=out.stem)
        if text_out:
            text_out.parent.mkdir(parents=True, exist_ok=True)
            text_out.write_text(text + "\n", encoding="utf-8")
        if not jobs:
            raise PlotterError("no text was recognised on the page")
        _save_jobs(jobs, cfg, out, preview)


@app.command(name="preview")
def preview_cmd(
    gcode_file: Annotated[Path, typer.Argument(help="G-code job.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="PNG to write.")],
    config: ConfigOpt = DEFAULT_CONFIG_PATH,
) -> None:
    """Render a G-code file and print its time and distances."""
    with _errors():
        cfg = _load(config)
        lines = read_gcode(gcode_file)
        validate(lines, cfg)
        render(lines, out, cfg, title=gcode_file.name)
        st = job_distances(lines, cfg)
        secs = estimate_time(lines, cfg)
        console.print(
            f"{gcode_file}: {st['paths']} strokes · {st['pen_down_mm'] / 1000:.2f} m pen-down · "
            f"{st['pen_up_mm'] / 1000:.2f} m travel · ~{secs / 60:.1f} min → {out}"
        )


@app.command()
def send(
    gcode_file: Annotated[Path, typer.Argument(help="G-code job to plot.")],
    port: PortOpt = None,
    dry_run: DryRunOpt = False,
    yes: YesOpt = False,
    config: ConfigOpt = DEFAULT_CONFIG_PATH,
) -> None:
    """Stream a job to the plotter (asks first unless --yes; try --dry-run)."""
    with _errors():
        cfg = _load(config)
        lines = read_gcode(gcode_file)
        validate(lines, cfg)
        secs = estimate_time(lines, cfg)
        target = "the GRBL emulator" if dry_run else (port or cfg.machine.serial_port)
        _confirm(
            f"Plot {gcode_file.name} ({len(lines)} lines, ~{secs / 60:.1f} min) on {target}?",
            yes,
            dry_run,
        )
        if dry_run:
            console.print("[cyan]dry-run:[/] streaming to the GRBL emulator, nothing is sent")
        with Progress(
            TextColumn("[bold]plotting"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as bar:
            task = bar.add_task("plot", total=None)

            def progress(acked: int, total: int) -> None:
                bar.update(task, completed=acked, total=total)

            res = pipeline.send_job(lines, cfg, port=port, dry_run=dry_run, progress_cb=progress)
        console.print(
            f"[green]done:[/] {res.acked}/{res.lines} lines acknowledged "
            f"(peak {res.max_in_flight} bytes in GRBL's buffer)"
        )


@app.command()
def home(
    port: PortOpt = None,
    dry_run: DryRunOpt = False,
    yes: YesOpt = False,
    config: ConfigOpt = DEFAULT_CONFIG_PATH,
) -> None:
    """Run the homing cycle ($H)."""
    with _errors():
        cfg = _load(config)
        _confirm("Home the machine now (X and Y will move to the switches)?", yes, dry_run)
        with _open(cfg, port, dry_run) as g:
            g.home()
        console.print("[green]homed[/]")


@app.command()
def unlock(
    port: PortOpt = None, dry_run: DryRunOpt = False, config: ConfigOpt = DEFAULT_CONFIG_PATH
) -> None:
    """Clear an alarm without homing ($X). Home as soon as possible afterwards."""
    with _errors():
        cfg = _load(config)
        with _open(cfg, port, dry_run) as g:
            g.unlock()
        console.print("[yellow]unlocked[/] — position may be wrong until you home")


@app.command()
def status(
    port: PortOpt = None, dry_run: DryRunOpt = False, config: ConfigOpt = DEFAULT_CONFIG_PATH
) -> None:
    """Show GRBL's state and machine position."""
    with _errors():
        cfg = _load(config)
        with _open(cfg, port, dry_run) as g:
            st = g.status()
        pos = ", ".join(f"{v:.3f}" for v in (st.mpos or st.wpos or ()))
        console.print(f"[bold]{st.state}[/]  position: {pos}  ({g.banner})")


@app.command()
def settings(
    port: PortOpt = None, dry_run: DryRunOpt = False, config: ConfigOpt = DEFAULT_CONFIG_PATH
) -> None:
    """Print GRBL's $$ settings (read-only; apply changes yourself)."""
    with _errors():
        cfg = _load(config)
        with _open(cfg, port, dry_run) as g:
            values = g.settings()
        t = Table("setting", "value")
        for k, v in sorted(values.items()):
            t.add_row(f"${k}", v)
        console.print(t)
        console.print("Compare with firmware/grbl_settings.txt; type changes in a serial terminal.")


@app.command()
def pen(
    state: Annotated[PenState, typer.Argument(help="up or down")],
    port: PortOpt = None,
    dry_run: DryRunOpt = False,
    config: ConfigOpt = DEFAULT_CONFIG_PATH,
) -> None:
    """Raise or lower the pen."""
    with _errors():
        cfg = _load(config)
        with _open(cfg, port, dry_run) as g:
            g.pen(up=state is PenState.up)
        console.print(f"pen {state.value}")


@app.command()
def jog(
    x: Annotated[float, typer.Option("--x", help="Relative X move in mm.")] = 0.0,
    y: Annotated[float, typer.Option("--y", help="Relative Y move in mm.")] = 0.0,
    feed: Annotated[float, typer.Option("--feed", help="Feed in mm/min.")] = 1000.0,
    port: PortOpt = None,
    dry_run: DryRunOpt = False,
    yes: YesOpt = False,
    config: ConfigOpt = DEFAULT_CONFIG_PATH,
) -> None:
    """Jog the carriage by a relative amount (machine frame: +X right, +Y away from you)."""
    with _errors():
        cfg = _load(config)
        _confirm(f"Jog X{x:+g} Y{y:+g} mm at F{feed:g}?", yes, dry_run)
        with _open(cfg, port, dry_run) as g:
            g.pen(up=True)
            g.jog(x, y, feed)
        console.print(f"jogged X{x:+g} Y{y:+g}")


if __name__ == "__main__":  # pragma: no cover
    app()
