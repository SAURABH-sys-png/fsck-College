# GRBL 1.1 setup for the pen plotter

The Arduino Uno runs **GRBL 1.1** and only executes G-code; everything else runs on the laptop.
The pen is lifted by an **SG90 servo** driven from GRBL's **spindle PWM**. Stock GRBL makes a
~1 kHz PWM for spindles and lasers, which a hobby servo can't use. A servo needs a ~50 Hz signal
with 0.5–2.5 ms pulses, so you need either a servo fork of GRBL (option A) or a small patch
(option B).

> **Safety:** the software never writes `$` settings for you. Apply them yourself (step 5).
> To stop the machine physically, press the Arduino **reset** button or **unplug the 12 V supply**.
> Before testing, keep your hands away from the belts and make sure nothing blocks the carriage.

---

## 1. Pin facts (CNC Shield V3 on an Uno)

| Function | Uno pin | CNC Shield label |
|---|---|---|
| X step / dir | D2 / D5 | X driver |
| Y step / dir | D3 / D6 | Y driver |
| Stepper enable (all, active LOW) | D8 | EN |
| X-min limit | D9 | X+/X− endstop |
| Y-min limit | D10 | Y+/Y− endstop |
| **Spindle PWM = servo signal** | **D11** | **Z+/Z− endstop** |
| Z limit (moved there by variable spindle) | D12 | SpnEn |

With GRBL 1.1's variable spindle (on by default), the spindle PWM uses **D11**. On the shield
that pin sits on the header labelled **Z+/Z− endstop**, so the servo's signal wire goes there.
Wire the servo's **+5 V** and **GND** to the shield's 5 V/GND pins, **never** to the 12 V input.
Check the silkscreen on your own shield, because clones differ.

---

## 2. Get the firmware

### Option A — a GRBL 1.1 servo fork

Community forks such as **`grbl-servo`** (search GitHub for "grbl servo") ship with the 50 Hz
servo PWM already set up. Before you use one, check that:

1. it is based on **GRBL 1.1** (`$I` must print `[VER:1.1…]`; `plotter` refuses anything else),
2. its README says which `S` values map to which angle (you will calibrate anyway, step 6),
3. homing is configured for **X and Y only** (this machine has no Z switch; see option B's
   `HOMING_CYCLE_0`), otherwise `$H` fails with `ALARM:9`.

If any of these don't hold, use option B.

### Option B — patch stock GRBL 1.1h (recommended)

Download **GRBL v1.1h** from <https://github.com/gnea/grbl/releases> and unzip it. All edits
are inside the `grbl/` folder. Line contents can differ slightly between releases, so search
for the names rather than relying on line numbers.

**`grbl/config.h`**

```c
// Home X and Y together; there is no Z switch on this machine.
#define HOMING_CYCLE_0 ((1<<X_AXIS)|(1<<Y_AXIS))
// #define HOMING_CYCLE_1 ((1<<X_AXIS)|(1<<Y_AXIS))   // comment this one out

// Machine origin (0,0) at the homed front-left corner, positive coordinates —
// matches the G-code this project generates (CLAUDE.md §3).
#define HOMING_FORCE_SET_ORIGIN

// Servo pulse range (Timer2 ticks of 64 µs, see cpu_map.h below):
// 8 × 64 µs ≈ 0.51 ms … 38 × 64 µs ≈ 2.43 ms.
#define SPINDLE_PWM_MIN_VALUE 8
```

Make sure `#define VARIABLE_SPINDLE` stays enabled (it is by default).

**`grbl/cpu_map.h`**, in the `CPU_MAP_ATMEGA328P` section:

```c
#define SPINDLE_PWM_MAX_VALUE     38   // was 255: top of the servo pulse range (≈2.43 ms)
...
// 1/1024 prescaler -> 16 MHz / 1024 / 256 ≈ 61 Hz (servo frame rate)
#define SPINDLE_TCCRB_INIT_MASK   ((1<<CS22) | (1<<CS21) | (1<<CS20))
```

GRBL maps `S` linearly from `$31` to `$30` onto `SPINDLE_PWM_MIN_VALUE…MAX_VALUE`. With
`$31=0` and `$30=255`:

| G-code | Timer value | Pulse |
|---|---|---|
| `M3 S30` (default pen up) | ≈ 11 | ≈ 0.70 ms |
| `M3 S90` (default pen down) | ≈ 18 | ≈ 1.15 ms |
| `M3 S255` | 38 | ≈ 2.43 ms |
| `M5` / `M3 S0` | off | no pulses (servo goes limp) |

Each timer step is about 6° of servo travel, which is plenty of resolution for a pen lift.

> **Limp servo on `M5`, reset or alarm:** with no pulses the SG90 stops holding its position.
> If your pen mechanism falls under gravity, it can touch the paper after a job ends or after
> an abort. Design the lift so the pen *rests* up (a spring or counterweight), or park the
> carriage off the sheet. Generated jobs park at `X0 Y0` (off the page) before `M5`.

---

## 3. Flash it

**Arduino IDE (2.x)**

1. Copy the patched `grbl/` folder into your Arduino `libraries/` folder (Documents/Arduino/libraries).
   Or use *Sketch → Include Library → Add .ZIP Library* on a zip of that folder.
2. *File → Examples → grbl → grblUpload*.
3. *Tools → Board → Arduino Uno*, select the port, and click **Upload**. Ignore the
   "low memory" warning, which is normal for GRBL.

**avrdude** (if you built `grbl.hex` with `make` in the GRBL source folder):

```bash
avrdude -c arduino -p atmega328p -P /dev/ttyACM0 -b 115200 -D -U flash:w:grbl.hex:i
#  Windows: -P COM3     macOS: -P /dev/cu.usbmodem1101     old Nano bootloader: -b 57600
```

---

## 4. Check it

Open a serial terminal at **115200** baud with line ending **Both NL & CR**. On reset you
should see:

```
Grbl 1.1h ['$' for help]
[MSG:'$H'|'$X' to unlock]        ← only once homing is enabled
```

* `$I` should print `[VER:1.1h…]`.
* `$$` lists all settings.
* `plotter status` and `plotter settings` do the same from the laptop (read-only).

---

## 5. Apply the settings (by hand)

Send the lines from [`grbl_settings.txt`](grbl_settings.txt) **one at a time**. Each must answer
`ok`. You can paste whole lines because GRBL 1.1 strips the `; …` comments. Afterwards run `$$`
and compare.

* `$1=255` keeps the motors energised between moves so position is never lost.
* `$23` sets the homing direction per axis (bit0 = X, bit1 = Y). The switches sit at the
  **minimum** (front-left) ends, so start with `$23=3`. If an axis runs *away* from its switch
  during `$H`, press reset and toggle that axis' bit: X → `1`, Y → `2`.
* `$3` inverts motion direction per axis. Set it so `plotter jog --x 10` moves **right** and
  `--y 10` moves **away from you**. Fix this **before** testing homing.
* `$20=1` (soft limits) only works **after** a successful homing cycle (`$22=1`, `$H`).
* `$32=0` is required. Laser mode turns the spindle off during `G0`, which would mess up the
  pen state.

If something goes wrong, `$RST=$` restores GRBL's defaults, and then you re-apply the file.

---

## 6. Calibrate the pen servo

Either:

* flash [`test_sketches/servo_test`](test_sketches/servo_test/servo_test.ino), type angles,
  read the matching `M3 S…` values it prints, then **re-flash GRBL**, or
* with GRBL running, send `M3 S20`, `M3 S30`, … from a terminal and watch the pen.

Pick **up** (pen ~2 mm clear of the paper) and **down** (just touching, without bending the
pen). Write them into `config/machine.yaml`:

```yaml
pen:
  up_cmd: "M3 S30"
  down_cmd: "M3 S90"
  settle_s: 0.15   # raise if the pen bounces or skips the start of strokes
```

Then test with `plotter pen down` and `plotter pen up`.

---

## 7. Home and set the origin

1. `plotter home` runs `$H`. Both axes move to their switches, back off `$27` mm, and the
   machine origin becomes (0, 0) (thanks to `HOMING_FORCE_SET_ORIGIN`).
2. If you flashed a firmware **without** `HOMING_FORCE_SET_ORIGIN`, GRBL's machine coordinates
   are negative after homing. Set the work origin once at the homed position with
   `G10 L20 P1 X0 Y0` (it is stored in EEPROM). Keep soft limits in mind: they check
   machine coordinates.
3. Continue with [`docs/CALIBRATION.md`](../docs/CALIBRATION.md): steps/mm, squareness and
   `page_origin_mm`.
