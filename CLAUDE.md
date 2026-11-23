# CLAUDE.md — ai-pen-plotter

Project memory for Claude Code. Read this whole file at the start of every session and follow it.
If a decision here changes, update this file in the same commit.

---

## 0. Skills — load them before you work (mandatory)

At the start of **every** session and before **every** new task:

1. **Check the skills available in this session** (the Skill tool listing, plus `.claude/skills/` in this repo).
2. **Load every skill relevant to the task** with the Skill tool before writing code. Look especially for skills about:
   - robotics, embedded, Arduino, CNC, GRBL, stepper/servo motion control, mechatronics
   - computer vision, OpenCV, image processing
   - Python engineering, packaging, CLI tools, testing, type checking
   - documentation/README writing, git/GitHub, CI
3. **Always load the two project skills that ship with this repo:**
   | Skill | Load it before touching |
   |---|---|
   | `robotics-engineer` | `firmware/`, `plotter/grbl.py`, `plotter/gcode.py`, motion/feed/bed settings in `config/machine.yaml`, `docs/WIRING.md`, `docs/CALIBRATION.md`, anything that moves the real machine |
   | `python-engineer` | any `.py` file, `tests/`, `pyproject.toml`, CI, CLI, GUI |
   Most tasks need **both** (e.g. G-code generation = Python code + motion rules).
4. **If a project skill is missing**, create it first: write `.claude/skills/<name>/SKILL.md` with the exact content in Appendix A / Appendix B, then load it.
5. If a needed skill doesn't exist anywhere, follow the standards in this file and say so in your summary.
6. In every final summary, list which skills you loaded.

---

## 1. What this project is

A DIY **A4 handwriting and drawing machine**. The user gives an image (a file, or a photo taken with the camera). The software turns it into pen strokes, generates G-code, previews it, and streams it to an Arduino running GRBL. An XY pen plotter then copies it onto an A4 sheet.

Two main modes:
- **DRAW:** copy an image (line art, diagrams, sketches, photos via hatching) onto A4.
- **WRITE:** read the text in a photo of a page (AI vision OCR via the Claude API, with Tesseract as an offline fallback), then write that text on A4 with a single-stroke (Hershey) font on ruled lines.

Everything except the motor control runs on the laptop in Python. The Arduino only executes G-code.

---

## 2. Hardware (source of truth — change here first)

| Item | Spec |
|---|---|
| Mechanics | Cartesian XY gantry; 8 mm smooth rods + LM8UU bearings; GT2 6 mm belt; 20T pulleys |
| Travel | X = 230 mm, Y = 320 mm (A4 is 210 × 297 mm) |
| Motors | 2× NEMA 17 (1.8°, ~1.2–1.7 A rated, ~4 kg·cm) |
| Drivers | 2× A4988 on CNC Shield V3, 1/16 microstepping (all 3 jumpers fitted) |
| Steps/mm | (200 × 16) / (20 teeth × 2 mm) = **80 steps/mm** for X and Y |
| Pen lift | SG90 servo driven by GRBL spindle PWM (GRBL 1.1, 50 Hz servo variant) |
| Controller | Arduino Uno R3 + CNC Shield V3, GRBL 1.1h, 115200 baud |
| Endstops | X-min and Y-min mechanical switches (normally open), used for homing |
| Power | 12 V 5 A SMPS → CNC shield motor input; Arduino powered over USB |
| Camera | USB webcam (OpenCV index) **or** phone over Wi-Fi (DroidCam / IP Webcam URL), mounted above the bed |
| Host | Windows / macOS / Linux laptop, USB serial to the Arduino |

CNC Shield V3 pin facts (verify against the board's silkscreen):
- X step D2, Y step D3, X dir D5, Y dir D6, stepper enable D8 (active LOW).
- With GRBL 1.1 variable spindle enabled, **spindle PWM is on D11**, which the shield labels **Z+/Z− endstop**, so the servo signal goes there. The Z-limit moves to D12 (labelled SpnEn). Z is unused in this project.
- A4988 current: `I_max = Vref / (8 × Rsense)`. Check the sense resistor (R100 = 0.1 Ω, R068 = 0.068 Ω, R050 = 0.05 Ω). Target ~70% of motor rated current; a plotter carries almost no load.

---

## 3. Coordinate systems and units (do not deviate)

- **Units:** millimetres everywhere inside the code. Pixels exist only inside `vision.py` / `perspective.py` and are converted before leaving those modules.
- **Page frame** (all drawing/text logic): origin at the **top-left corner of the sheet** as the user sees it (portrait), +x to the right, +y **down** (same as images). Range x ∈ [0, 210], y ∈ [0, 297].
- **Machine frame** (G-code only): origin = homed position, **front-left** (near the user), +X right, +Y **away** from the user.
- The sheet sits in portrait with its **bottom-left corner at `page_origin_mm`** (machine coords) and its top edge away from the user.
- **Transform** (only in `gcode.py`): `X = ox + x`, `Y = oy + (297 − y)`. This is the only place the y-flip happens, and it is unit-tested.
- **Geometry types:** `Path = np.ndarray` of shape `(N, 2)`, `float64`, page-mm, N ≥ 2. `Drawing = list[Path]`. Never use Python lists of tuples for geometry in hot paths.

---

## 4. Architecture

```
[camera / image file] → capture → perspective (flatten to A4)
        │
        ├─ DRAW:  vision (lineart | outline | hatch) → polylines (px) → mm
        └─ WRITE: ocr (Claude vision → Tesseract fallback) → text → text_layout (Hershey strokes)
                                   │
                                   ▼
                    paths (fit to page, simplify, join, order)
                                   ▼
                    gcode (page→machine transform, pen up/down, bounds check)
                                   ▼
                    preview (PNG + time/distance estimate)
                                   ▼
                    grbl (stream over serial, char-counting, status, abort)
                                   ▼
                    Arduino Uno + GRBL → A4988 → NEMA 17 ×2 + SG90 pen lift
```

Rules:
- Each stage is a **pure function** where possible (input → output, no hidden state), so it can be tested without hardware.
- Only `grbl.py` talks to the serial port. Only `camera.py` talks to the camera. Only `ocr.py` talks to the network.
- `pipeline.py` composes the stages; `cli.py` and `app/streamlit_app.py` are thin wrappers over `pipeline.py`.

---

## 5. Repository layout

```
ai-pen-plotter/
├── CLAUDE.md
├── README.md                    # overview, BOM + costs, wiring summary, setup, quick start, examples
├── pyproject.toml               # package + entry point `plotter = plotter.cli:app`; ruff/mypy/pytest config
├── requirements.txt             # pinned runtime deps (mirrors pyproject)
├── requirements-dev.txt         # pytest, pytest-cov, ruff, mypy, pre-commit, types-*
├── .env.example                 # ANTHROPIC_API_KEY=   AI_MODEL=
├── .gitignore                   # .env, output/, __pycache__/, .venv/, *.gcode outside samples/
├── .pre-commit-config.yaml      # ruff (lint+format), mypy
├── Makefile                     # install, dev, test, lint, typecheck, preview-sample, gui, clean
├── .github/workflows/ci.yml     # ubuntu + windows, py3.10–3.12: ruff, mypy, pytest --cov
├── .claude/skills/
│   ├── robotics-engineer/SKILL.md
│   └── python-engineer/SKILL.md
├── config/machine.yaml
├── plotter/
│   ├── __init__.py              # __version__
│   ├── config.py                # dataclasses + YAML load + validation (clear errors)
│   ├── types.py                 # Path, Drawing aliases; Job dataclass
│   ├── camera.py
│   ├── perspective.py
│   ├── vision.py
│   ├── ocr.py
│   ├── text_layout.py
│   ├── fonts/                   # bundled Hershey single-stroke data if not using a package
│   ├── paths.py
│   ├── gcode.py
│   ├── preview.py
│   ├── grbl.py
│   ├── grbl_codes.py            # error:N and ALARM:N → human messages
│   ├── fake_grbl.py             # FakeGrbl emulator (tests + `send --dry-run`)
│   ├── images.py                # unicode-safe image load/save
│   ├── errors.py
│   ├── pipeline.py
│   └── cli.py
├── app/streamlit_app.py
├── firmware/
│   ├── GRBL_SETUP.md
│   ├── grbl_settings.txt
│   └── test_sketches/
│       ├── servo_test/servo_test.ino
│       └── stepper_test/stepper_test.ino
├── docs/
│   ├── ARCHITECTURE.md          # includes a mermaid flowchart of section 4
│   ├── WIRING.md
│   ├── CALIBRATION.md
│   ├── TROUBLESHOOTING.md
│   └── ASSUMPTIONS.md
├── samples/                     # lineart.png, photo.jpg, page_photo.jpg, sample_text.txt (generate synthetically if needed)
│                                #   + calibration_square.gcode, origin_mark.gcode
├── scripts/                     # make_samples.py, make_calibration.py, requirements-dev.in
├── output/                      # generated .gcode / .png (git-ignored)
└── tests/
    ├── conftest.py              # fixtures: default config, synthetic images, FakeGrbl serial
    ├── test_config.py
    ├── test_paths.py
    ├── test_gcode.py
    ├── test_transform.py
    ├── test_vision.py
    ├── test_perspective.py
    ├── test_text_layout.py
    ├── test_ocr.py              # AI client mocked; fallback path tested
    ├── test_grbl.py
    ├── test_camera.py · test_preview.py · test_pipeline.py
    ├── test_gui.py              # Streamlit AppTest (skipped if streamlit is missing)
    └── test_cli.py              # Typer CliRunner, --dry-run only
```

---

## 6. Configuration — `config/machine.yaml`

All tunables live here. No magic numbers in code. Validate on load (types, ranges, bed ⊇ page + origin).

```yaml
machine:
  serial_port: auto            # "auto" = detect Arduino (VID/PID or description), else e.g. COM3, /dev/ttyACM0
  baud: 115200
  bed_size_mm: [230, 320]
  page_size_mm: [210, 297]     # A4 portrait
  page_origin_mm: [10, 10]     # machine coords of the sheet's bottom-left corner
  margins_mm: {left: 20, right: 10, top: 15, bottom: 15}
  connect_wait_s: 2.0          # wait after waking GRBL (Uno resets on port open)
  response_timeout_s: 60       # max wait for an 'ok' (G4 waits for the planner to drain)
motion:
  draw_feed_mm_min: 3000
  travel_feed_mm_min: 5000
  accel_mm_s2: 500             # mirror of $120/$121; used for time estimates only
  coord_decimals: 2
pen:
  up_cmd: "M3 S30"             # depends on servo firmware; calibrate with servo_test.ino
  down_cmd: "M3 S90"
  settle_s: 0.15               # emitted as G4 P0.15 after every pen move
paths:
  min_segment_mm: 0.2
  simplify_tolerance_mm: 0.08
  join_tolerance_mm: 0.2
  optimize: greedy             # greedy | greedy+2opt | none
  two_opt_max_paths: 1500      # skip the O(n²) 2-opt pass above this many paths
camera:
  source: 0                    # int index or URL, e.g. "http://192.168.1.5:4747/video"
  warp_px_per_mm: 10
vision:
  mode: lineart                # lineart | outline | hatch
  blur_ksize: 3
  adaptive_block: 31
  adaptive_c: 10
  min_contour_px: 15
  hatch: {levels: 4, spacing_mm: 1.2, angles_deg: [45, -45, 0, 90]}
text:
  font: futural                # Hershey single-stroke
  size_mm: 5.0                 # cap height
  line_spacing_mm: 8.0         # ruled-notebook spacing
  first_line_y_mm: 25.0        # page frame
  jitter: {enabled: true, baseline_mm: 0.3, scale_pct: 3, rotate_deg: 1.5, seed: 42}
ai:
  enabled: true
  model_env: AI_MODEL          # model name is read from this env var — never hardcode it
  key_env: ANTHROPIC_API_KEY
  max_retries: 3
  timeout_s: 60
  max_image_px: 2000           # long side of the image sent to the vision model
```

---

## 7. Module specifications

### 7.1 `camera.py`
- `capture(source, warmup_frames=5) -> np.ndarray (BGR)`. Accepts an int index or a URL. Discard warm-up frames, raise `CameraError` with a helpful message if it can't open.
- `save(frame, path)`.

### 7.2 `perspective.py`
- `find_page_corners(img) -> np.ndarray (4,2) | None`: grayscale → blur → Canny → dilate → contours → largest contour whose `approxPolyDP` has 4 points and covers more than 20% of the image area. Order the corners TL, TR, BR, BL.
- `warp_to_a4(img, corners, px_per_mm=10) -> np.ndarray` (2100 × 2970 px for A4).
- If no page is found, return the input unchanged and log a warning. Never crash.

### 7.3 `vision.py` — image → `Drawing` (page-mm)
- **lineart:** grayscale → Gaussian blur → adaptive threshold (inverted) → morphological open → `skimage.morphology.skeletonize` → **skeleton graph tracing** into polylines (walk 8-connected pixels, split at junctions with ≥3 neighbours and at endpoints) → drop short fragments.
- **outline:** Canny → `findContours(RETR_LIST, CHAIN_APPROX_NONE)` → filter by length.
- **hatch** (photos): quantise brightness into `levels`. Darker levels get more hatch angles from `angles_deg`. Generate parallel lines at `spacing_mm`, clip them to each level's mask, and keep the runs inside the mask as segments.
- Convert px → mm by fitting the image into the printable area (page minus margins), preserving aspect ratio and centring.

### 7.4 `ocr.py`
- `read_text(image, engine="auto") -> str`. `auto` = Claude if `ai.enabled` and the key env var is set, else Tesseract.
- Claude path: `anthropic` SDK, `messages.create` with a base64 image block + instruction: *"Transcribe all text exactly as written. Preserve line breaks and paragraph breaks. Output plain text only, no commentary."* Model comes from `os.environ[cfg.ai.model_env]`. Retry with exponential backoff on rate-limit/5xx; time out per config.
- Tesseract path: `pytesseract.image_to_string` on a cleaned (grayscale + Otsu) image.
- Never log the API key or the full image payload.

### 7.5 `text_layout.py`
- `layout_text(text, cfg) -> list[Drawing]` (**one Drawing per page**; overflow creates new pages).
- Use the Hershey single-stroke glyphs (e.g. the `Hershey-Fonts` package, or bundled public-domain Hershey data in `plotter/fonts/`; check the package exists before depending on it). Scale so the cap height = `size_mm`.
- Word wrap by **measured glyph advance widths** within the left/right margins. Preserve blank lines. Hyphenate only when a single word is wider than the line.
- Baselines start at `first_line_y_mm` and step by `line_spacing_mm`.
- Optional jitter (seeded RNG, reproducible): baseline offset, per-glyph scale, small rotation — within config limits.

### 7.6 `paths.py`
- `fit_to_area(drawing, area_mm) -> Drawing`
- `simplify(drawing, tol)`: Ramer–Douglas–Peucker, vectorised numpy.
- `filter_short(drawing, min_len)`
- `join_paths(drawing, tol)`: merge paths whose endpoints are within `tol` (consider reversal).
- `order_paths(drawing, method)`: greedy nearest-neighbour from (0,0), allowing reversal; optional 2-opt pass on the visiting order. Must reduce total pen-up travel versus the input order (tested).
- `stats(drawing) -> {paths, pen_down_mm, pen_up_mm}`

### 7.7 `gcode.py`
- `to_gcode(drawing, cfg) -> list[str]`.
- Header: `G21` `G90` `G17` `G94`, pen up, settle.
- Per path: `G0` to the start (pen is already up) → pen down → `G4 P{settle}` → `G1 ... F{draw}` through the points → pen up → `G4 P{settle}`.
- Footer: pen up, `G0 X0 Y0`, `M5`.
- Page→machine transform from section 3. Round to `coord_decimals`. Every line < 70 chars (GRBL's line buffer is 80). No arcs.
- `validate(lines, cfg)`: parse every X/Y and **raise `OutOfBoundsError`** if any falls outside the bed. Also check that no G0 move happens while the pen is down.
- `write_gcode(lines, path)` adds a comment header: source, date, stats, estimated time.

### 7.8 `preview.py`
- `render(drawing_or_gcode, out_png)`: A4 outline, margin box, pen-down strokes in solid dark, travel moves in thin dashed light red, 150 dpi.
- `estimate_time(lines, cfg) -> seconds` using feeds + a simple trapezoidal acceleration model (`accel_mm_s2` from config, default 500) + pen settle dwells.

### 7.9 `grbl.py`
- `class Grbl` (context manager): `connect()` opens the port, sends `\r\n\r\n`, waits 2 s, flushes, reads the `Grbl 1.1x` banner (raise if missing).
- `stream(lines, progress_cb=None, dry_run=False)`: **character-counting protocol**. Track the bytes of unacknowledged lines and keep the total ≤ 127 (GRBL RX buffer = 128). Pop on `ok`. On `error:N` stop, pen up, and raise with the message from `grbl_codes.py`.
- Real-time commands (not counted in the buffer): `status()` sends `?` and parses `<Idle|MPos:…|…>`; `hold()` = `!`, `resume()` = `~`, `reset()` = `0x18`.
- Also `home()` (`$H`), `unlock()` (`$X`), `settings()` (`$$`, read-only), `jog(dx, dy, feed)` (`$J=G91 …`), `pen(up: bool)`.
- `ALARM:N` → stop streaming, raise `GrblAlarm` with an explanation and the fix (usually: home or unlock).
- `FakeGrbl` (in `plotter/fake_grbl.py`; used by tests and by `send --dry-run`) emulates the banner, `ok` responses, status reports, buffer limits and injected errors.
- Auto-detect the port with `serial.tools.list_ports` (Arduino VID 0x2341, CH340 VID 0x1A86).

### 7.10 `pipeline.py` and `cli.py`
Typer app `plotter`, rich output, every command has `--config` (default `config/machine.yaml`):
```
plotter capture --out page.jpg
plotter flatten page.jpg --out flat.png
plotter draw IMAGE --mode lineart|outline|hatch --out job.gcode [--preview p.png] [--flatten]
plotter ocr IMAGE [--engine auto|ai|tesseract] --out text.txt
plotter write TEXT_FILE --out job.gcode [--preview p.png]      # multiple pages → job_p1.gcode, job_p2.gcode…
plotter copy-page IMAGE --out job.gcode [--preview p.png]      # flatten → OCR → layout → gcode
plotter preview job.gcode --out preview.png                     # prints time + distances
plotter send job.gcode [--port auto] [--dry-run] [--yes]        # asks for confirmation unless --yes
plotter home | unlock | status | settings
plotter pen up|down
plotter jog --x 10 --y 0 [--feed 1000]
```

### 7.11 `app/streamlit_app.py`
Upload or capture → optional flatten → choose DRAW/WRITE → tweak the main parameters → preview image + stats → Send button (with a confirm checkbox) → progress bar + Pause / Resume / Abort.

---

## 8. Firmware and GRBL

- `firmware/GRBL_SETUP.md` covers: flashing GRBL 1.1 with servo pen-lift. Document both options: (a) a GRBL 1.1 servo fork such as `grbl-servo`, (b) patching `spindle_control.c` for 50 Hz PWM. Then: the Arduino IDE / `avrdude` steps, checking with `$I`, applying settings, and calibrating `pen.up_cmd` / `pen.down_cmd`.
- `firmware/grbl_settings.txt` (starting values, with a comment on each):
  ```
  $0=10    $1=255   $2=0     $3=0     $4=0     $5=0     $6=0
  $10=1    $11=0.010 $12=0.002 $13=0
  $20=1    $21=1    $22=1    $23=3    $24=100  $25=1000 $26=250 $27=2.000
  $30=255  $31=0    $32=0
  $100=80  $101=80  $102=80
  $110=5000 $111=5000 $112=500
  $120=500 $121=500 $122=100
  $130=230 $131=320 $132=10
  ```
  `$1=255` keeps the motors energised so position isn't lost. `$23` depends on which end each switch is mounted, so document how to change it. `$20=1` (soft limits) only works after homing.
- `test_sketches/servo_test.ino`: Servo library on D11. Sweep and read angles from the serial monitor to find the pen up/down values.
- `test_sketches/stepper_test.ino`: drive X/Y back and forth with the CNC shield pins from section 2, without GRBL, to check wiring and direction.

---

## 9. Python standards (summary — details in the `python-engineer` skill)

- Python ≥ 3.10. `from __future__ import annotations`. Type hints everywhere; `mypy --strict` must pass on `plotter/`.
- `ruff` for lint + format (line length 100). Docstrings (Google style) on every public function.
- Use `logging` (module-level `logger = logging.getLogger(__name__)`), never `print` in library code; `rich` only in the CLI/GUI.
- Custom exceptions in `plotter/errors.py` (all derive from `PlotterError`): `ConfigError`, `CameraError`, `OcrError`, `GcodeError` (unsafe/malformed G-code) → `OutOfBoundsError`, `GrblError` → `GrblAlarm`.
- No global mutable state. Pass `Config` explicitly.
- Cross-platform paths via `pathlib`. Never hardcode a serial port or a camera index outside the config.

---

## 10. Testing

- `pytest` with `pytest-cov`. Coverage ≥ 85% on `paths`, `gcode`, `text_layout`, `grbl`, `config`.
- **All tests run with no hardware, no camera and no network.** Use synthetic images (draw shapes with OpenCV in fixtures), `FakeGrbl`, and a mocked Anthropic client.
- Must-have tests:
  - y-flip transform: page (0,0) → machine (ox, oy+297); page (210,297) → (ox+210, oy).
  - every generated job passes `validate()`. An out-of-bounds drawing raises `OutOfBoundsError`.
  - pen is up before every `G0`; the job starts and ends with the pen up.
  - every G-code line < 70 chars.
  - character-counting never exceeds 127 bytes in flight (FakeGrbl asserts this).
  - `error:N` mid-stream stops streaming and lifts the pen.
  - text never crosses the margins; long text produces multiple pages; jitter with the same seed is deterministic.
  - `order_paths` reduces pen-up travel on a shuffled grid of segments.
  - OCR falls back to Tesseract when the key is missing.

---

## 11. Hard safety rules for Claude

1. **Never stream G-code to real hardware** (`plotter send` without `--dry-run`, or any serial write) unless the user explicitly asks for it **in the current conversation**. Default to `--dry-run`.
2. Never write GRBL `$` settings to the device automatically. Print them and let the user apply them.
3. Never disable `validate()` or the bounds check, not even "temporarily".
4. Never commit `.env`, API keys, or personal photos. Keep `output/` git-ignored.
5. Before any change that affects motion (feeds, accel, transform, pen commands), run the full test suite and render a preview.
6. Document the physical stop: unplug the 12 V supply or press reset. The software abort is `plotter` Ctrl+C → feed hold + soft reset.

---

## 12. Commands

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e . -r requirements-dev.txt
pre-commit install
make test        # pytest --cov
make lint        # ruff check . && ruff format --check .
make typecheck   # mypy plotter
make preview-sample   # renders samples → output/*.png
make gui         # streamlit run app/streamlit_app.py
plotter --help
```
System dependency for the OCR fallback: Tesseract (`apt install tesseract-ocr`, `brew install tesseract`, or the Windows installer). Document this in the README.

---

## 13. Git and GitHub workflow

- Default branch `main`. Conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`, `ci:`).
- Commit per module or phase, not one giant commit. Never commit failing tests.
- If `gh` is installed and authenticated, create the public repo `ai-pen-plotter` and push. Otherwise print the exact commands for the user to run.

---

## 14. First session: bootstrap the whole repo in one go

If the repo is empty or incomplete, build everything without stopping to ask questions. Record any assumptions in `docs/ASSUMPTIONS.md`. Work in this order, running tests after each phase:

0. **Skills:** do section 0. Create `.claude/skills/robotics-engineer/` and `.claude/skills/python-engineer/` from the appendices if they're missing, then load them.
1. **Scaffold:** pyproject, requirements, Makefile, .gitignore, .env.example, pre-commit, `config/machine.yaml`, `config.py` + `types.py` + `errors.py` + tests.
2. **Geometry core:** `paths.py`, `gcode.py` (with transform + `validate`), `preview.py` + tests.
3. **Vision:** `camera.py`, `perspective.py`, `vision.py` (all three modes) + tests with synthetic images.
4. **Text:** `ocr.py` (Claude + Tesseract), `text_layout.py` + Hershey font data + tests.
5. **Machine I/O:** `grbl.py`, `grbl_codes.py`, `FakeGrbl` + tests.
6. **Interfaces:** `pipeline.py`, `cli.py`, `app/streamlit_app.py` + CLI tests.
7. **Firmware:** `GRBL_SETUP.md`, `grbl_settings.txt`, both `.ino` sketches.
8. **Docs:** README (with BOM in ₹, wiring summary, quick start, screenshots of previews), ARCHITECTURE (mermaid), WIRING, CALIBRATION (steps/mm check with a 100 mm square, squareness, pen height, servo angles, page origin, camera alignment), TROUBLESHOOTING.
9. **CI and finish:** GitHub Actions workflow. Run `ruff`, `mypy` and `pytest` and fix everything. Generate `output/lineart_preview.png` and `output/text_preview.png` from the samples. `git init`, commit by phase, push (see section 13).

End with a summary: what was built, the skills used, how to run a first dry-run job, and the first five calibration steps on the real machine.

## 15. Definition of done (every task)

- [ ] Relevant skills loaded (section 0)
- [ ] `ruff`, `mypy`, `pytest` all pass; coverage target met
- [ ] New behaviour has tests; motion-affecting changes have a preview rendered
- [ ] Config, docs and this file are updated if behaviour or settings changed
- [ ] No secrets, no hardware commands sent without explicit permission

---

## Appendix A — `.claude/skills/robotics-engineer/SKILL.md`

````markdown
---
name: robotics-engineer
description: Robotics/mechatronics engineer for the ai-pen-plotter. Use before touching GRBL firmware or settings, G-code generation, motion/feed/bed config, serial streaming, wiring, calibration docs, or anything that moves the real machine.
---

# Robotics engineer — pen plotter

Think like an engineer who has to stand next to the machine when it runs.

## Before changing anything
1. Re-read CLAUDE.md sections 2 (hardware), 3 (coordinates), 8 (GRBL) and 11 (safety).
2. Identify what moves: X, Y, or the pen. State the expected physical result in mm and seconds.

## Motion rules
- Work in mm in the page frame. The machine frame appears only in gcode.py, through the single page→machine transform.
- Steps/mm = (motor steps × microsteps) / (pulley teeth × belt pitch). For this machine that's 80. Recompute if any part changes.
- Pen up before every travel (G0), pen down only at the start of a stroke, and a G4 settle after every pen move.
- Feeds: drawing ≤ 3000 mm/min for clean lines, travel ≤ 5000 mm/min. Acceleration about 500 mm/s². Raise these only after a test print.
- Bounds: every coordinate inside the bed with page + margins; soft limits on after homing. Never remove validate().
- No arcs (G2/G3); keep G-code lines < 70 chars; 2 decimals is enough (0.01 mm vs 0.0125 mm per step).

## GRBL knowledge
- 128-byte RX buffer → character-counting streaming (≤127 bytes in flight); real-time commands ?, !, ~, 0x18 are not counted.
- error:N = bad line (stop, pen up, report). ALARM:N = position lost or limit hit (needs $H or $X; explain which).
- $1=255 keeps motors locked between moves. $20 needs $22 + successful homing. $23 sets the homing direction per axis.
- GRBL 1.1 variable spindle: PWM on D11 (CNC Shield Z+ header) = servo signal; Z-limit moves to D12.

## Electrical
- A4988: I_max = Vref / (8 × Rsense). Set ~70% of rated motor current; check Rsense on the board.
- Never connect or disconnect motors while the drivers are powered. Common ground between the SMPS and the Arduino.
- Heatsinks on the drivers. 12 V only into the shield's motor input, never into the Arduino 5 V.

## Calibration workflow (document it, and use it when debugging)
1. stepper_test.ino → check directions. 2. Flash GRBL, apply settings. 3. Home.
4. Draw a 100 × 100 mm square and measure; adjust $100/$101 = old × (commanded / measured).
5. Draw a diagonal cross → check squareness. 6. servo_test.ino → pen up/down values.
7. Set page_origin_mm by jogging to the sheet's bottom-left corner. 8. Test print a sample.

## Safety
- Default to dry-run. Real streaming only when the user explicitly asks in this conversation.
- Never write $ settings to the device automatically. Print them.
- Always explain how to stop the machine (power off or reset; software feed hold + soft reset).
````

## Appendix B — `.claude/skills/python-engineer/SKILL.md`

````markdown
---
name: python-engineer
description: Senior Python engineer standards for the ai-pen-plotter. Use before writing or editing any Python file, tests, packaging (pyproject), CLI (Typer), GUI (Streamlit), or CI.
---

# Python engineer — ai-pen-plotter

## Workflow for every change
1. Read the relevant CLAUDE.md module spec (section 7) and its tests.
2. Write or adjust tests first for new behaviour (pytest; hardware-free).
3. Implement the smallest clean change.
4. Run: `ruff check . && ruff format . && mypy plotter && pytest -q --cov=plotter`.
5. Fix every failure before moving on. Never skip or xfail a test to get green.

## Code standards
- Python ≥ 3.10, `from __future__ import annotations`, full type hints, mypy --strict clean.
- Geometry = numpy float64 arrays of shape (N, 2) in mm. Vectorise; no per-point Python loops in hot paths when numpy can do it.
- Pure functions for pipeline stages; side effects (camera, serial, network, files) are isolated in their own modules.
- Config is an immutable dataclass passed explicitly. No globals, no singletons.
- Errors: raise the specific exceptions from plotter/errors.py with a message that tells the user how to fix it.
- logging in library code; rich only in cli.py and the GUI. Never print secrets.
- pathlib for paths; no OS-specific assumptions (Windows COM ports, macOS /dev/cu.*, Linux /dev/ttyACM*).
- Google-style docstrings with Args/Returns/Raises on public functions.
- Keep modules under ~400 lines; split helpers out when they grow.

## Dependencies
- Add a dependency only if it saves real work. Pin it in requirements.txt and declare it in pyproject.
- Check that a package actually exists on PyPI and works for Python 3.10–3.12 before using it.
- The Anthropic SDK is used only in ocr.py. Read the model name and key from env vars; never hardcode either.

## Testing
- Fixtures in conftest.py: default config, synthetic images drawn with OpenCV, FakeGrbl, mocked AI client.
- Test behaviour and invariants (bounds, pen-up-before-travel, buffer ≤ 127 bytes, determinism with seeds), not implementation details.
- CLI tests use typer.testing.CliRunner with --dry-run.
- Coverage ≥ 85% on core modules.

## Packaging and CI
- pyproject.toml with the console script `plotter = plotter.cli:app`; tool configs for ruff, mypy, pytest live there.
- GitHub Actions matrix: ubuntu-latest + windows-latest, Python 3.10/3.11/3.12. Install tesseract on ubuntu for the OCR test path, or mock it.
- Conventional commits; one logical change per commit.
````
