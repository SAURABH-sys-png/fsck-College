# Troubleshooting

**Stop first, debug second:** press the Arduino reset button or unplug the 12 V supply.

## Connection

| Symptom | Likely cause | Fix |
|---|---|---|
| `no Arduino found` | Driver or permissions | CH340 clones need the CH340 driver on Windows/macOS. On Linux add yourself to the serial group (`sudo usermod -aG dialout $USER`, then log out and in). Or pass `--port COM3` / `/dev/ttyACM0` / `/dev/cu.usbmodem…`. |
| `cannot open … Is another program using it?` | Serial Monitor, UGS or CNCjs holds the port | Close the other program. |
| `no GRBL banner` | Wrong port or baud, or GRBL not flashed | Open a serial terminal at 115200 and press reset: you must see `Grbl 1.1h ['$' for help]`. Re-flash per [GRBL_SETUP.md](../firmware/GRBL_SETUP.md). |
| `expected GRBL 1.1` | Old GRBL 0.9 / servo fork | Flash GRBL 1.1 (option B in GRBL_SETUP.md). |
| `GRBL did not answer within 60 s` | USB cable or power glitch, or a very long dwell | Check the cable. If the Arduino resets when the servo moves, power the servo separately ([WIRING.md](WIRING.md)). |
| `GRBL was reset (banner received)` | Arduino rebooted mid-job (brown-out, ESD, someone pressed reset) | Re-home, then re-run the job. Power the servo separately if it keeps happening. |

## Alarms and errors

| Message | Meaning | Fix |
|---|---|---|
| `ALARM:1` | Hard limit hit | Check for obstructions. Noisy switch wires: twist them, add 100 nF, or set `$21=0`. Then `plotter home`. |
| `ALARM:2` | Soft limit: target outside the travel | `plotter unlock`, then check `page_origin_mm`, the margins and `$130/$131`. Home before plotting. |
| `ALARM:3` | Reset during motion; position lost | `plotter home`. |
| `ALARM:8` | Couldn't clear the switch after homing | Increase `$27` (pull-off) or fix the switch wiring. |
| `ALARM:9` | Homing couldn't find a switch | Check the switch wiring and `$23` direction. GRBL must be built to home **X/Y only** (no Z switch), see GRBL_SETUP.md. |
| `error:9` | G-code locked in the alarm state | `plotter home` (or `plotter unlock` if you are sure of the position). |
| `error:20` / `error:22` | Unsupported command / no feed | Only happens with hand-written G-code. Regenerate with `plotter draw/write`. |
| `outside the bed` (before sending) | The job doesn't fit | Fix `page_origin_mm` / margins, or regenerate the job with the current config. The validator stays on. |

## Motion

| Symptom | Likely cause | Fix |
|---|---|---|
| Motor hums but doesn't turn | Coil pairs mixed, Vref too low, no 12 V | Check the coil pairs with a multimeter, set Vref ([WIRING.md](WIRING.md)), check the SMPS. |
| Plot is mirrored or upside-down | Axis direction or origin corner | `jog --x 10` must go right and `--y 10` away from you; fix `$3`. Homing must end at the front-left. |
| Shapes slightly too big or small | steps/mm | 100 mm square test, then adjust `$100/$101` ([CALIBRATION.md](CALIBRATION.md)). |
| Circles come out oval, squares skewed | Frame not square, or loose belt | Square the gantry; tension the belts. |
| Layer shift / drifting position | Skipped steps | Lower `$110/$111` and `$120/$121`, raise Vref slightly, check that the pulley grub screws sit on the shaft flat. |
| Wobbly lines | Belt slack, bearing play, feed too high | Tension the belts, lower `motion.draw_feed_mm_min`. |

## Pen

| Symptom | Likely cause | Fix |
|---|---|---|
| Servo doesn't move | Stock GRBL PWM (~1 kHz), wrong pin, `$32=1` | Flash the servo PWM firmware, use **D11 (Z+ header)**, set `$32=0`. |
| Servo jitters or buzzes | Noisy 5 V, or position at the servo's mechanical end | Separate 5 V supply with a common ground; choose `S` values away from the ends. |
| Starts of strokes missing | Pen lands after motion starts | Raise `pen.settle_s`. |
| Pen drops after a job or an abort | `M5`/reset stops the PWM, so the servo goes limp | Make the lift rest **up** (spring or counterweight). |
| Faint lines, or dragging between strokes | Wrong down/up angle | Recalibrate the pen values ([CALIBRATION.md](CALIBRATION.md) §6). |

## Software

| Symptom | Fix |
|---|---|
| `Tesseract is not installed` | `sudo apt install tesseract-ocr` / `brew install tesseract` / Windows installer (add to `PATH`), or set `ANTHROPIC_API_KEY` for AI OCR. |
| AI OCR not used | `.env` must contain `ANTHROPIC_API_KEY` and `AI_MODEL`, and `ai.enabled: true`. In `auto` mode any API error falls back to Tesseract; run with `-v` to see why. |
| `set AI_MODEL …` | Put the Claude model name in `.env`, e.g. `AI_MODEL=claude-opus-5-5`. |
| `no page found in the photo` (warning) | Darker background, even light, whole sheet in frame. The image is then used as-is. |
| Line art comes out broken or noisy | Increase `vision.blur_ksize`, tune `adaptive_block` / `adaptive_c`, raise `min_contour_px`. Try `--mode outline` for filled shapes. |
| Hatching takes too long | Raise `vision.hatch.spacing_mm`, lower `levels`, or use fewer `angles_deg`. |
| Config error on start | The message names the key. The file is validated against the bed size and margins. |
| GUI won't start | `pip install -e ".[gui]"`, then `make gui`. |
