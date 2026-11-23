"""Image → pen strokes (:data:`~plotter.types.Drawing` in page-mm).

Three modes (CLAUDE.md §7.3):

* ``lineart`` – adaptive threshold → open → skeletonize → skeleton graph tracing. Best for
  drawings, diagrams and handwriting: every line is drawn once, along its centre.
* ``outline`` – Canny edges → contours. Good for shapes and logos (draws both edges of thick
  lines). Pixels already drawn by an earlier contour are skipped, so edges are not doubled.
* ``hatch`` – brightness is quantised into levels; darker levels get more hatch angles.
  Used for photos.

Pixels only exist inside this module; the image is placed in the printable area (aspect ratio
kept, centred) and every result leaves in millimetres.
"""

from __future__ import annotations

import logging
import math

import cv2
import numpy as np
import numpy.typing as npt
from skimage.morphology import skeletonize

from plotter.config import Config
from plotter.images import Image
from plotter.types import Drawing

logger = logging.getLogger(__name__)

_OPEN_KERNEL = np.ones((2, 2), np.uint8)  # removes 1-px speckles, keeps 2-px+ lines
_NEIGHBOUR_KERNEL = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], np.float32)
_HATCH_SAMPLES_PER_PX = 1.5

Area = tuple[float, float, float, float]
FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]


# --------------------------------------------------------------------------- placement


def to_gray(img: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    """Return a single-channel ``uint8`` image from gray, BGR or BGRA input."""
    if img.ndim == 2:
        return img
    code = cv2.COLOR_BGRA2GRAY if img.shape[2] == 4 else cv2.COLOR_BGR2GRAY
    return np.asarray(cv2.cvtColor(img, code), dtype=np.uint8)


def image_placement(shape_hw: tuple[int, int], area: Area) -> tuple[float, float, float]:
    """Fit an ``h × w`` pixel image into ``area`` (mm), keeping aspect, centred.

    Returns:
        ``(mm_per_px, x_offset_mm, y_offset_mm)``.
    """
    h, w = shape_hw
    x0, y0, x1, y1 = area
    s = min((x1 - x0) / w, (y1 - y0) / h)
    return s, x0 + ((x1 - x0) - w * s) / 2, y0 + ((y1 - y0) - h * s) / 2


def px_to_mm(polys_px: list[FloatArray], shape_hw: tuple[int, int], area: Area) -> Drawing:
    """Convert pixel polylines ``(x=col, y=row)`` to page-mm using :func:`image_placement`."""
    s, ox, oy = image_placement(shape_hw, area)
    offset = np.array([ox, oy])
    return [(np.asarray(p, dtype=np.float64) + 0.5) * s + offset for p in polys_px]


# --------------------------------------------------------------------------- lineart


def lineart_mask(gray: npt.NDArray[np.uint8], cfg: Config) -> BoolArray:
    """Binarise dark lines: blur → adaptive threshold (inverted) → morphological open."""
    v = cfg.vision
    blur = cv2.GaussianBlur(gray, (v.blur_ksize, v.blur_ksize), 0) if v.blur_ksize > 1 else gray
    bw = cv2.adaptiveThreshold(
        blur,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        v.adaptive_block,
        v.adaptive_c,
    )
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, _OPEN_KERNEL)
    return np.asarray(bw > 0)


def _remove_corner_pixels(pad: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    """Delete redundant L-corner pixels (two 4-neighbours that touch each other diagonally).

    Such pixels make their neighbours look like junctions; removing them keeps connectivity.
    """
    c = pad[1:-1, 1:-1].astype(bool)
    n, s = pad[:-2, 1:-1].astype(bool), pad[2:, 1:-1].astype(bool)
    w, e = pad[1:-1, :-2].astype(bool), pad[1:-1, 2:].astype(bool)
    deg = cv2.filter2D(pad, cv2.CV_16S, _NEIGHBOUR_KERNEL, borderType=cv2.BORDER_CONSTANT)
    corner = c & (deg[1:-1, 1:-1] == 2) & ((n & e) | (e & s) | (s & w) | (w & n))
    out: npt.NDArray[np.uint8] = pad.copy()
    out[1:-1, 1:-1][corner] = 0
    return out


def trace_skeleton(skel: BoolArray, min_px: int = 0) -> list[FloatArray]:
    """Trace a one-pixel-wide skeleton into polylines.

    Pixels with exactly two 8-neighbours are "path" pixels; all others are nodes (endpoints
    have one neighbour, junctions three or more). Walks run node → node along path pixels and
    are extended to the centroid of the junction cluster they touch, so strokes meeting at a
    junction share an exact endpoint. Closed loops without nodes are traced separately.
    Spurs and specks (a free end and fewer than ``min_px`` pixels) are dropped;
    junction-to-junction links are always kept.

    Args:
        skel: Boolean skeleton image.
        min_px: Minimum pixel count for paths that have a free end.

    Returns:
        Polylines in pixel coordinates ``(x=col, y=row)``, each with ≥ 2 points.
    """
    w = skel.shape[1]
    pad = _remove_corner_pixels(np.pad(skel.astype(np.uint8), 1))
    width = w + 2
    deg_img = cv2.filter2D(pad, cv2.CV_16S, _NEIGHBOUR_KERNEL, borderType=cv2.BORDER_CONSTANT)
    flat_np = pad.ravel().astype(bool)
    deg_np = deg_img.ravel() * flat_np
    junction = (deg_np >= 3).reshape(pad.shape).astype(np.uint8)
    n_lab, labels_img = cv2.connectedComponents(junction, connectivity=8)
    labels = labels_img.ravel()
    jidx = np.flatnonzero(labels)
    counts = np.maximum(np.bincount(labels[jidx], minlength=n_lab), 1)
    cx = np.bincount(labels[jidx], weights=jidx % width - 1, minlength=n_lab) / counts
    cy = np.bincount(labels[jidx], weights=jidx // width - 1, minlength=n_lab) / counts

    on = flat_np.tolist()
    deg = deg_np.tolist()
    is_node = (flat_np & (deg_np != 2)).tolist()
    visited = [False] * len(on)
    offsets = [dy * width + dx for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx]

    def walk(a: int, b: int) -> list[int]:
        path = [a, b]
        prev, cur = a, b
        while not is_node[cur]:
            visited[cur] = True
            nxt = -1
            for o in offsets:
                c = cur + o
                if c != prev and on[c] and (is_node[c] or not visited[c]):
                    nxt = c
                    break
            if nxt < 0:
                break
            path.append(nxt)
            prev, cur = cur, nxt
        return path

    traced: list[tuple[list[int], bool]] = []  # (pixels, has_free_end)
    pixels = np.flatnonzero(flat_np).tolist()
    for a in pixels:
        if not is_node[a] or deg[a] == 0:
            continue
        for o in offsets:
            b = a + o
            if on[b] and not is_node[b] and not visited[b]:
                path = walk(a, b)
                free = deg[path[0]] < 3 or deg[path[-1]] < 3 or not is_node[path[-1]]
                traced.append((path, free))
    for s in pixels:  # closed loops with no node on them
        if visited[s] or is_node[s]:
            continue
        visited[s] = True
        nbrs = [s + o for o in offsets if on[s + o]]
        path = walk(s, nbrs[0])
        path.append(s)
        traced.append((path, len(path) < 4))

    out: list[FloatArray] = []
    for path, free in traced:
        if len(path) < 2 or (free and len(path) < min_px):
            continue
        idx = np.asarray(path)
        pts = np.column_stack([idx % width - 1, idx // width - 1]).astype(np.float64)
        head, tail = labels[path[0]], labels[path[-1]]
        if head:
            pts = np.vstack([[cx[head], cy[head]], pts])
        if tail:
            pts = np.vstack([pts, [cx[tail], cy[tail]]])
        out.append(pts)
    logger.debug("traced %d skeleton paths (%d px wide image)", len(out), w)
    return out


def lineart_paths_px(img: npt.NDArray[np.uint8], cfg: Config) -> list[FloatArray]:
    """Run the lineart pipeline and return pixel polylines."""
    mask = lineart_mask(to_gray(img), cfg)
    skel: BoolArray = np.asarray(skeletonize(mask), dtype=np.bool_)  # type: ignore[no-untyped-call]
    return trace_skeleton(skel, cfg.vision.min_contour_px)


# --------------------------------------------------------------------------- outline


def _true_runs(mask: BoolArray) -> list[tuple[int, int]]:
    """Return ``[start, end)`` index pairs of consecutive True values."""
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist(), strict=True))


def outline_paths_px(img: npt.NDArray[np.uint8], cfg: Config) -> list[FloatArray]:
    """Canny edges → ``findContours(RETR_LIST, CHAIN_APPROX_NONE)`` → de-duplicated polylines."""
    v = cfg.vision
    gray = to_gray(img)
    blur = cv2.GaussianBlur(gray, (v.blur_ksize, v.blur_ksize), 0) if v.blur_ksize > 1 else gray
    otsu, _ = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    hi = max(float(otsu), 20.0)
    edges = cv2.Canny(blur, hi / 2, hi)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    used = np.zeros(edges.size, dtype=bool)
    width = edges.shape[1]
    out: list[FloatArray] = []
    for c in sorted(contours, key=len, reverse=True):
        pts = c.reshape(-1, 2)
        lin = pts[:, 1] * width + pts[:, 0]
        new = np.zeros(len(pts), dtype=bool)
        new[np.unique(lin, return_index=True)[1]] = True
        new &= ~used[lin]
        used[lin] = True
        runs = _true_runs(new)
        if len(runs) == 1 and runs[0] == (0, len(pts)) and len(pts) > 2:
            run_pts = np.vstack([pts, pts[:1]])  # untouched closed contour: close it
            if len(run_pts) >= v.min_contour_px:
                out.append(run_pts.astype(np.float64))
            continue
        for s, e in runs:
            if e - s < v.min_contour_px:
                continue
            s0 = s - 1 if s > 0 else s  # reconnect to the already-drawn neighbour pixel
            out.append(pts[s0:e].astype(np.float64))
    return out


# --------------------------------------------------------------------------- hatch


def _hatch_angle(
    mask: BoolArray, angle_deg: float, spacing_mm: float, placement: tuple[float, float, float]
) -> Drawing:
    """Parallel lines at ``angle_deg`` clipped to ``mask``; returns page-mm segments."""
    s, ox, oy = placement
    h, w = mask.shape
    theta = math.radians(angle_deg)
    u = np.array([math.cos(theta), math.sin(theta)])
    n = np.array([-math.sin(theta), math.cos(theta)])
    corners = np.array([[ox, oy], [ox + w * s, oy], [ox + w * s, oy + h * s], [ox, oy + h * s]])
    cn, cu = corners @ n, corners @ u
    offsets = np.arange(cn.min() + spacing_mm / 2, cn.max(), spacing_mm)
    step = s / _HATCH_SAMPLES_PER_PX
    ts = np.arange(cu.min(), cu.max() + step, step)
    if offsets.size == 0 or ts.size < 2:
        return []
    pts = offsets[:, None, None] * n + ts[None, :, None] * u  # (lines, samples, 2)
    col = np.floor((pts[..., 0] - ox) / s).astype(np.int64)
    row = np.floor((pts[..., 1] - oy) / s).astype(np.int64)
    inside = (col >= 0) & (col < w) & (row >= 0) & (row < h)
    hit = np.zeros(inside.shape, dtype=bool)
    hit[inside] = mask[row[inside], col[inside]]
    d = np.diff(np.pad(hit.astype(np.int8), ((0, 0), (1, 1))), axis=1)
    li, starts = np.nonzero(d == 1)
    _, ends = np.nonzero(d == -1)
    out: Drawing = []
    for line, a, b in zip(li.tolist(), starts.tolist(), ends.tolist(), strict=True):
        if b - a < 2:
            continue
        seg = np.array([pts[line, a], pts[line, b - 1]])
        out.append(seg[::-1].copy() if line % 2 else seg)  # serpentine
    return out


def hatch_drawing(img: npt.NDArray[np.uint8], cfg: Config, area: Area) -> Drawing:
    """Hatch a photo: darker brightness levels receive more hatch directions.

    Args:
        img: BGR or grayscale image.
        cfg: Configuration (``vision.hatch``).
        area: Target rectangle in page-mm.

    Returns:
        Straight hatch segments in page-mm.
    """
    v = cfg.vision
    gray = to_gray(img)
    if v.blur_ksize > 1:
        gray = np.asarray(cv2.GaussianBlur(gray, (v.blur_ksize, v.blur_ksize), 0), np.uint8)
    gray = np.asarray(cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX), dtype=np.uint8)
    levels = v.hatch.levels
    angles = v.hatch.angles_deg
    q = np.minimum(gray.astype(np.int32) * levels // 256, levels - 1)  # 0 = darkest
    n_angles = np.rint(len(angles) * (levels - 1 - np.arange(levels)) / (levels - 1)).astype(int)
    placement = image_placement(gray.shape[:2], area)
    out: Drawing = []
    for j, angle in enumerate(angles):
        mask = n_angles[q] > j
        if mask.any():
            out.extend(_hatch_angle(mask, angle, v.hatch.spacing_mm, placement))
    return out


# --------------------------------------------------------------------------- dispatch


def image_to_drawing(img: Image, cfg: Config, mode: str | None = None) -> Drawing:
    """Convert an image to strokes in page-mm, placed in the printable area.

    Args:
        img: BGR or grayscale ``uint8`` image.
        cfg: Configuration.
        mode: ``lineart``, ``outline`` or ``hatch``; defaults to ``cfg.vision.mode``.

    Returns:
        The drawing (unordered, not simplified — see :mod:`plotter.pipeline`).

    Raises:
        ValueError: For an unknown mode or an empty image.
    """
    mode = mode or cfg.vision.mode
    if img.size == 0:
        raise ValueError("empty image")
    area = cfg.printable_area
    shape = (int(img.shape[0]), int(img.shape[1]))
    if mode == "lineart":
        return px_to_mm(lineart_paths_px(img, cfg), shape, area)
    if mode == "outline":
        return px_to_mm(outline_paths_px(img, cfg), shape, area)
    if mode == "hatch":
        return hatch_drawing(img, cfg, area)
    raise ValueError(f"unknown vision mode {mode!r}; use lineart, outline or hatch")
