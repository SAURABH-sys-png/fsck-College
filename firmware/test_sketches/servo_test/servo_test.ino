// servo_test.ino — find the pen-up and pen-down positions of the SG90 on D11.
//
// D11 is the pin GRBL 1.1 uses for spindle PWM; on the CNC Shield V3 it is the header
// labelled "Z+ / Z- endstop". Connect: servo signal (orange) → D11 (Z+ signal pin),
// servo + (red) → 5 V, servo − (brown) → GND.
//
// Upload, open the Serial Monitor at 115200 baud ("Newline"), and type an angle 0–180 + Enter.
// The sketch moves the servo and prints the pulse width plus the matching GRBL "S" value
// for the patched firmware described in firmware/GRBL_SETUP.md (61 Hz, OCR2A = 8…38 for
// S = $31…$30 = 0…255). Put those S values into config/machine.yaml:
//     pen.up_cmd:   "M3 S<up>"
//     pen.down_cmd: "M3 S<down>"
// …and confirm them on GRBL with `plotter pen up` / `plotter pen down`.
//
// Afterwards re-flash GRBL (this sketch replaces it).

#include <Servo.h>

const int SERVO_PIN = 11;
const float TICK_US = 64.0;     // Timer2, 16 MHz / 1024 prescaler
const float OCR_MIN = 8.0;      // SPINDLE_PWM_MIN_VALUE in the patched GRBL
const float OCR_MAX = 38.0;     // SPINDLE_PWM_MAX_VALUE in the patched GRBL
const float S_MAX = 255.0;      // GRBL $30

Servo pen;

void report(int angle) {
  int us = pen.readMicroseconds();
  float s = (us / TICK_US - OCR_MIN) * S_MAX / (OCR_MAX - OCR_MIN);
  Serial.print(F("angle "));
  Serial.print(angle);
  Serial.print(F("  pulse "));
  Serial.print(us);
  Serial.print(F(" us  ->  GRBL M3 S"));
  Serial.println((int)(s + 0.5));
}

void setup() {
  Serial.begin(115200);
  pen.attach(SERVO_PIN);
  Serial.println(F("servo_test: sweeping once…"));
  for (int a = 0; a <= 180; a += 5) { pen.write(a); delay(40); }
  for (int a = 180; a >= 90; a -= 5) { pen.write(a); delay(40); }
  Serial.println(F("Type an angle 0-180 and press Enter. Find: pen just clear of the paper"));
  Serial.println(F("(UP, add ~2 mm) and pen lightly touching (DOWN, no bending)."));
  report(90);
}

void loop() {
  if (Serial.available()) {
    long a = Serial.parseInt();
    while (Serial.available()) Serial.read();  // drop the line ending
    a = constrain(a, 0, 180);
    pen.write((int)a);
    delay(300);
    report((int)a);
  }
}
