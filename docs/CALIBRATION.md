# Calibration

Do these steps in order on a new machine, and again after any mechanical change.

> **Stopping:** press the Arduino **reset** button or **unplug the 12 V supply**. In software,
> Ctrl+C during `plotter send` sends feed hold + soft reset.
>
> Rehearse each job first with `--dry-run`. Keep fingers away from belts and pulleys.

## 1. Directions (before GRBL)

1. Flash [`stepper_test.ino`](../firmware/test_sketches/stepper_test/stepper_test.ino) and
   open the Serial Monitor at 115200.
2. With the carriage in the middle of the bed, type `x`. The carriage should move **right**
   by 10 mm. Then type `y`: it should move **away from you**.
3. If an axis is reversed, power off and flip that motor's connector. If a motor only buzzes,
   check the coil pairing and Vref ([WIRING.md](WIRING.md)).

## 2. Flash GRBL and apply the settings

Follow [firmware/GRBL_SETUP.md](../firmware/GRBL_SETUP.md). Paste
[`grbl_settings.txt`](../firmware/grbl_settings.txt) line by line, then check with
`plotter settings`. The software never writes settings itself.

## 3. Home

1. `plotter unlock`, then `plotter jog --x 10` and `plotter jog --y 10`. Check that +X is
   right and +Y is away from you. If not, change `$3`.
2. `plotter home`. Both axes must move **towards** their switches (front-left). If one moves
   away, press reset and toggle its bit in `$23` (X = 1, Y = 2).
3. `plotter status` should now show `Idle` near `MPos:0,0`.

## 4. Steps/mm (100 mm square)

1. Tape a sheet down and put a fine pen in the holder.
2. Dry-run first, then plot for real:
   ```bash
   plotter send samples/calibration_square.gcode --dry-run
   plotter send samples/calibration_square.gcode
   ```
3. Measure the X side and the Y side with a steel rule or calipers. Correct each axis with:
   ```
   new $100 = old $100 × (commanded 100 / measured X)
   new $101 = old $101 × (commanded 100 / measured Y)
   ```
   For example, X measures 99.2 mm, so `$100 = 80 × 100 / 99.2 = 80.65`. Type `$100=80.65` in
   a serial terminal.
4. Plot again until both sides are within ±0.3 mm.

## 5. Squareness

The square has both **diagonals**. Measure them. They should be equal (141.4 mm). If they
differ by more than ~0.5 mm, the X rail is not perpendicular to Y. Loosen the frame or rod
clamps, square the gantry against a set square, tighten, and re-plot. Software can't fix a
skewed frame.

## 6. Pen height and servo values

1. Flash [`servo_test.ino`](../firmware/test_sketches/servo_test/servo_test.ino), or with GRBL
   running send `M3 S20`, `M3 S30`, … from a terminal.
2. **Down:** the tip just touches the paper, with no bending, for an even line.
   **Up:** about 2 mm clear, enough to never drag and small enough to be quick.
3. Put the values into `config/machine.yaml`:
   ```yaml
   pen: {up_cmd: "M3 S30", down_cmd: "M3 S90", settle_s: 0.15}
   ```
4. Re-flash GRBL if you used the sketch. Then run `plotter pen down` / `plotter pen up`.
5. If strokes start late or show blobs, raise `settle_s` (0.2–0.3 s) or lower the feed.

## 7. Page origin

The software places the sheet's **bottom-left corner** (near you, on the left) at
`machine.page_origin_mm` (default `[10, 10]`).

1. `plotter home`, then `plotter send samples/origin_mark.gcode`. It draws an "L" whose corner
   is exactly at `page_origin_mm`, on a scrap sheet or straight on the bed.
2. Tape the A4 sheet so its bottom-left corner sits on the corner of the L, with the sheet's
   edges along the L's arms.
3. Alternatively, jog the pen tip to the corner (`plotter jog …`), read `plotter status`, and
   write the X/Y into `page_origin_mm`. The config loader refuses an origin that doesn't fit
   on the bed.

## 8. Camera alignment (for `capture` / `copy-page`)

1. Mount the camera above the bed, pointing straight down, with the whole sheet in frame.
2. `plotter capture --out output/cam.jpg`, then `plotter flatten output/cam.jpg --out output/flat.png`.
3. Open `flat.png`. It should be the sheet only, upright, and not stretched. If the page isn't
   found (you get the photo back unchanged), improve contrast: a dark surface around the sheet,
   even light, no glare.
4. For phones, set `camera.source` to the DroidCam / IP Webcam URL
   (e.g. `http://192.168.1.5:4747/video`).

## 9. Test print

```bash
plotter draw samples/lineart.png --out output/robot.gcode --preview output/robot.png
plotter send output/robot.gcode
plotter write samples/sample_text.txt --out output/letter.gcode --preview output/letter.png
plotter send output/letter.gcode
```

Compare the plots with the previews. Shifted plots usually mean the page origin is off; skewed
plots mean squareness; wobbly lines mean belt tension or a feed that is too high. See
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).
