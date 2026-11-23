"""Generate the calibration G-code jobs in samples/ (validated like every other job).

Run from the repo root:  python scripts/make_calibration.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from plotter.config import load_config
from plotter.gcode import to_gcode, write_gcode
from plotter.preview import estimate_time

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    cfg = load_config(ROOT / "config" / "machine.yaml")
    x0, y0 = 55.0, 98.5  # 100 mm square centred on the A4 page (page frame)
    x1, y1 = x0 + 100, y0 + 100
    square = [
        np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]),
        np.array([[x0, y0], [x1, y1]]),  # diagonals: equal length = square machine
        np.array([[x1, y0], [x0, y1]]),
    ]
    h = cfg.machine.page_size_mm[1]
    corner = [np.array([[0.0, h - 15], [0.0, h], [15.0, h]])]  # L at the sheet's bottom-left
    for name, drawing, note in [
        ("calibration_square", square, "100x100 mm square + diagonals"),
        ("origin_mark", corner, "L mark where the sheet's bottom-left corner must sit"),
    ]:
        lines = to_gcode([p.astype(np.float64) for p in drawing], cfg)
        write_gcode(
            lines,
            ROOT / "samples" / f"{name}.gcode",
            source=note,
            est_time_s=estimate_time(lines, cfg),
        )
        print(f"samples/{name}.gcode — {note}")


if __name__ == "__main__":
    main()
