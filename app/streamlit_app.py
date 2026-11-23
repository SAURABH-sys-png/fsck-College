"""Streamlit GUI — a thin wrapper over plotter.pipeline.

Run:  streamlit run app/streamlit_app.py      (or: make gui)
"""

from __future__ import annotations

import tempfile
import time
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import streamlit as st
from dotenv import load_dotenv

from plotter import camera, pipeline
from plotter.config import DEFAULT_CONFIG_PATH, Config, load_config
from plotter.errors import PlotterError
from plotter.preview import render
from plotter.types import Job

load_dotenv()
st.set_page_config(page_title="AI Pen Plotter", page_icon="✏️", layout="wide")
TMP = Path(tempfile.gettempdir()) / "ai-pen-plotter-gui"
TMP.mkdir(exist_ok=True)
ss = st.session_state
ss.setdefault("jobs", [])
ss.setdefault("text", "")
ss.setdefault("controller", None)


def decode(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise PlotterError("could not decode the image")
    return img


@st.cache_data(show_spinner=False)
def preview_png(gcode: tuple[str, ...], cfg_key: str, _cfg: Config, title: str) -> bytes:
    out = TMP / f"preview_{abs(hash((gcode, cfg_key)))}.png"
    render(list(gcode), out, _cfg, title=title)
    return out.read_bytes()


# ----------------------------------------------------------------------------- sidebar
st.sidebar.title("✏️ AI Pen Plotter")
cfg_path = st.sidebar.text_input("Config file", str(DEFAULT_CONFIG_PATH))
try:
    base_cfg = load_config(cfg_path)
except PlotterError as exc:
    st.error(str(exc))
    st.stop()

mode = st.sidebar.radio("Mode", ["DRAW", "WRITE"], horizontal=True)
with st.sidebar.expander("Path optimisation"):
    optimize = st.selectbox(
        "Ordering",
        ["greedy", "greedy+2opt", "none"],
        index=["greedy", "greedy+2opt", "none"].index(base_cfg.paths.optimize),
    )
    simplify = st.slider("Simplify tolerance (mm)", 0.0, 0.5, base_cfg.paths.simplify_tolerance_mm)
cfg = replace(
    base_cfg,
    paths=replace(base_cfg.paths, optimize=optimize, simplify_tolerance_mm=simplify),
)

# ----------------------------------------------------------------------------- input
st.header("1 · Input")
left, right = st.columns(2)
img: np.ndarray | None = None
with left:
    up = st.file_uploader("Upload an image", type=["png", "jpg", "jpeg", "bmp", "webp"])
    shot = st.camera_input("…or take a photo in the browser")
    if st.button("…or capture from the configured camera"):
        try:
            ss["captured"] = camera.capture(cfg.camera.source)
        except PlotterError as exc:
            st.error(str(exc))
    if up is not None:
        img = decode(up.getvalue())
    elif shot is not None:
        img = decode(shot.getvalue())
    elif "captured" in ss:
        img = ss["captured"]
    do_flatten = st.checkbox("Flatten the page (photo of a sheet)", value=False)
with right:
    if img is not None:
        shown = pipeline.prepare_image(img, cfg, flatten_page=do_flatten)
        st.image(cv2.cvtColor(shown, cv2.COLOR_BGR2RGB), caption="input", width="stretch")

# ----------------------------------------------------------------------------- parameters
st.header("2 · Parameters")
if mode == "DRAW":
    c1, c2, c3 = st.columns(3)
    vmode = c1.selectbox("Vision mode", ["lineart", "outline", "hatch"])
    block = c2.slider("Adaptive block (odd)", 3, 101, cfg.vision.adaptive_block, step=2)
    c_val = c3.slider("Adaptive C", 0.0, 30.0, float(cfg.vision.adaptive_c))
    spacing = c1.slider("Hatch spacing (mm)", 0.5, 4.0, cfg.vision.hatch.spacing_mm)
    levels = c2.slider("Hatch levels", 2, 8, cfg.vision.hatch.levels)
    min_px = c3.slider("Min fragment (px)", 0, 100, cfg.vision.min_contour_px)
    cfg = replace(
        cfg,
        vision=replace(
            cfg.vision,
            mode=vmode,
            adaptive_block=block,
            adaptive_c=c_val,
            min_contour_px=min_px,
            hatch=replace(cfg.vision.hatch, spacing_mm=spacing, levels=levels),
        ),
    )
    if st.button("Generate drawing", type="primary", disabled=img is None):
        with st.spinner("tracing…"):
            try:
                ss["jobs"] = [pipeline.draw_job(img, cfg, flatten_page=do_flatten, name="gui")]
            except (PlotterError, ValueError) as exc:
                st.error(str(exc))
else:
    c1, c2, c3 = st.columns(3)
    engine = c1.selectbox("OCR engine", ["auto", "ai", "tesseract"])
    if c1.button("Read text from the image", disabled=img is None):
        with st.spinner("reading…"):
            try:
                ss["text"] = pipeline.ocr_image(img, cfg, engine=engine, flatten_page=do_flatten)
            except PlotterError as exc:
                st.error(str(exc))
    size = c2.slider("Cap height (mm)", 2.0, 12.0, cfg.text.size_mm, 0.5)
    spacing = c2.slider("Line spacing (mm)", 4.0, 20.0, cfg.text.line_spacing_mm, 0.5)
    jitter = c3.checkbox("Hand-written jitter", cfg.text.jitter.enabled)
    seed = c3.number_input("Jitter seed", value=cfg.text.jitter.seed, step=1)
    ss["text"] = st.text_area("Text to write", ss["text"], height=220)
    cfg = replace(
        cfg,
        text=replace(
            cfg.text,
            size_mm=size,
            line_spacing_mm=spacing,
            first_line_y_mm=max(cfg.text.first_line_y_mm, cfg.machine.margins_mm.top + size * 1.3),
            jitter=replace(cfg.text.jitter, enabled=jitter, seed=int(seed)),
        ),
    )
    if st.button("Lay out text", type="primary", disabled=not ss["text"].strip()):
        try:
            ss["jobs"] = pipeline.write_jobs(ss["text"], cfg, name="gui")
        except PlotterError as exc:
            st.error(str(exc))

# ----------------------------------------------------------------------------- preview
jobs: list[Job] = ss["jobs"]
st.header("3 · Preview")
if not jobs:
    st.info("Generate a drawing or lay out text to see the preview.")
    st.stop()
tabs = st.tabs([j.name for j in jobs])
for tab, job in zip(tabs, jobs, strict=True):
    with tab:
        a, b = st.columns([2, 1])
        a.image(preview_png(tuple(job.gcode), repr(cfg), cfg, job.name), width="stretch")
        b.metric("Paths", job.stats["paths"])
        b.metric("Pen-down", f"{job.stats['pen_down_mm'] / 1000:.2f} m")
        b.metric("Travel", f"{job.stats['pen_up_mm'] / 1000:.2f} m")
        b.metric("Estimated time", f"{job.est_time_s / 60:.1f} min")
        b.download_button(
            "Download G-code", "\n".join(job.gcode) + "\n", file_name=f"{job.name}.gcode"
        )

# ----------------------------------------------------------------------------- send
st.header("4 · Plot")
st.caption(
    "Physical stop: press the Arduino reset button or unplug the 12 V supply. "
    "Abort below sends feed hold + soft reset."
)
which = st.selectbox("Page", range(len(jobs)), format_func=lambda i: jobs[i].name)
c1, c2 = st.columns(2)
dry = c1.checkbox("Dry run (GRBL emulator, nothing is sent)", value=True)
port = c2.text_input("Serial port", cfg.machine.serial_port, disabled=dry)
ready = st.checkbox(
    "I am next to the machine: it is homed, paper and pen are in place.", disabled=dry
)
ctl: pipeline.StreamController | None = ss["controller"]
busy = ctl is not None and ctl.running
if st.button("Send", type="primary", disabled=busy or (not dry and not ready)):
    ctl = pipeline.StreamController(jobs[which].gcode, cfg, port=port, dry_run=dry)
    try:
        ctl.start()
        ss["controller"] = ctl
    except PlotterError as exc:
        st.error(str(exc))
        ctl = None

if ctl is not None:
    total = max(ctl.total, 1)
    st.progress(ctl.acked / total, text=f"{ctl.acked}/{ctl.total} lines")
    p1, p2, p3 = st.columns(3)
    if p1.button("Pause", disabled=not ctl.running):
        ctl.pause()
    if p2.button("Resume", disabled=not ctl.running):
        ctl.resume()
    if p3.button("Abort", disabled=not ctl.running):
        ctl.abort()
    if ctl.running:
        time.sleep(0.5)
        st.rerun()
    elif ctl.error is not None:
        st.error(f"stopped: {ctl.error}")
    elif ctl.result is not None:
        how = "dry run, emulator" if ctl.dry_run else "plotted"
        st.success(f"done — {ctl.result.acked} lines ({how})")
