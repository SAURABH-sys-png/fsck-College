# Wiring

> **Never connect or disconnect a motor while the drivers are powered.** That destroys A4988s.
> Power off the 12 V supply first. To stop the machine at any time, unplug the 12 V supply or
> press the Arduino reset button.

```
                 ┌──────────────────────── CNC Shield V3 on Arduino Uno ─────────────────────────┐
 12 V 5 A SMPS ──┤ + / −  motor power (screw terminal; check polarity!)                          │
                 │                                                                               │
 NEMA 17 (X) ────┤ X driver header (A4988, 1/16: MS1+MS2+MS3 jumpers fitted)                     │
 NEMA 17 (Y) ────┤ Y driver header (A4988, 1/16)                                                 │
                 │                                                                               │
 X-min switch ───┤ X+/X− endstop  (D9)   signal + GND, normally-open                             │
 Y-min switch ───┤ Y+/Y− endstop  (D10)  signal + GND, normally-open                             │
 SG90 signal  ───┤ Z+/Z− endstop  (D11 = GRBL spindle PWM)                                       │
 SG90 + / −   ───┤ 5 V / GND pins                                                                │
                 └───────────────────────────────────┬───────────────────────────────────────────┘
                                                     │ USB (data + Arduino 5 V)
                                                  laptop
```

## Power

- **12 V goes only into the shield's motor-power terminal**, never into the Arduino's 5 V pin
  or barrel jack.
- The Arduino is powered over USB. The shield ties the grounds together. If you power anything
  from a separate supply (for example the servo), **connect its ground to the Arduino ground**.
- The SG90 draws up to ~500 mA when it stalls. If the Arduino resets when the pen moves (USB
  brown-out), power the servo from a separate 5 V regulator, with a common ground.
- Fit the heatsinks to the A4988s. Turn the SMPS on **after** USB, and off **before** unplugging
  anything.

## Motors

- Each NEMA 17 has two coils. Identify them with a multimeter: a pair with continuity (a few
  ohms) is one coil. Plug one coil into `1A/1B` and the other into `2A/2B`.
- If an axis moves the wrong way, power off and **flip one coil** (or reverse the connector),
  or set the bit in GRBL `$3` later.
- Test with [`stepper_test.ino`](../firmware/test_sketches/stepper_test/stepper_test.ino)
  before flashing GRBL. +X should move **right** and +Y **away from you**.

## Driver current (Vref)

```
I_max = Vref / (8 × Rsense)     →     Vref = I_target × 8 × Rsense
```

Target about **70% of the motor's rated current**. A plotter carries almost no load, so a
lower current means cooler motors and drivers. Read the sense resistors next to the chip:
`R100` = 0.1 Ω, `R068` = 0.068 Ω, `R050` = 0.05 Ω.

| Motor rating | 70% target | Vref (R100) | Vref (R068) | Vref (R050) |
|---|---|---|---|---|
| 1.2 A | 0.84 A | 0.67 V | 0.46 V | 0.34 V |
| 1.5 A | 1.05 A | 0.84 V | 0.57 V | 0.42 V |
| 1.7 A | 1.19 A | 0.95 V | 0.65 V | 0.48 V |

To measure: with 12 V on and the motors **disconnected or idle**, put the multimeter between the
trimmer's metal wiper and GND, and turn it with a ceramic or insulated screwdriver.

Signs of a wrong setting:

- motors miss steps on fast moves: raise Vref a little;
- drivers or motors too hot to touch: lower it.

## Endstops

- Mechanical **normally-open** switches go between the signal pin and GND. GRBL uses internal
  pull-ups, so `$5=0`.
- Mount them at the **minimum** ends: X at the left, Y at the front (near you). The homed
  position is then the front-left corner, which is the machine origin.
- Long unshielded switch wires pick up noise. If you see false `ALARM:1` (hard limit), twist
  the wires, add a 100 nF capacitor across each switch, or set `$21=0`.

## Pen-lift servo

- Signal goes to **D11**. With GRBL 1.1's variable spindle that pin is the spindle PWM, and it
  is on the header labelled **Z+/Z− endstop**. The Z limit moves to D12 (`SpnEn`); Z is not
  used.
- Firmware: you need the 50 Hz / 61 Hz servo PWM from
  [firmware/GRBL_SETUP.md](../firmware/GRBL_SETUP.md). Stock GRBL's ~1 kHz PWM makes a servo
  buzz or ignore the signal.
- With no pulses (`M5`, reset, alarm) the servo goes limp. Design the lift so the pen rests
  **up**.

## Camera

Mount a USB webcam or a phone above the bed, looking straight down, with the whole sheet in
view. Even, diffuse light and a darker background around the white sheet help page detection.
For a phone, use DroidCam or IP Webcam and set `camera.source` to the stream URL.
