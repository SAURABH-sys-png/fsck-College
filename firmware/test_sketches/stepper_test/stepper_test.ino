// stepper_test.ino — drive X and Y on a CNC Shield V3 WITHOUT GRBL.
//
// Purpose: check motor wiring, driver current and direction before flashing GRBL.
// Upload, open the Serial Monitor at 115200 baud and type a command + Enter:
//
//   x / X   move X +10 mm / -10 mm      (+X = carriage moves RIGHT)
//   y / Y   move Y +10 mm / -10 mm      (+Y = carriage moves AWAY from you)
//   s       draw a 40 mm square (X+, Y+, X-, Y-)
//   e / d   enable / disable the drivers (disabled = motors can be turned by hand)
//
// There are NO limit switches here: start with the carriage in the middle of the bed.
// Stop: press the Arduino reset button or unplug the 12 V supply.
// Afterwards re-flash GRBL (see firmware/GRBL_SETUP.md).

const int X_STEP = 2;   // CNC Shield V3 pin map (check the silkscreen)
const int Y_STEP = 3;
const int X_DIR = 5;
const int Y_DIR = 6;
const int ENABLE = 8;   // active LOW for all drivers

const long STEPS_PER_MM = 80;       // (200 × 16) / (20 × 2)
const unsigned int HALF_PERIOD_US = 400;  // 1250 steps/s ≈ 15.6 mm/s — gentle
const float MOVE_MM = 10.0;

void stepAxis(int stepPin, int dirPin, float mm) {
  digitalWrite(dirPin, mm >= 0 ? HIGH : LOW);
  delayMicroseconds(5);  // direction setup time for the A4988
  long steps = (long)(abs(mm) * STEPS_PER_MM);
  for (long i = 0; i < steps; i++) {
    digitalWrite(stepPin, HIGH);
    delayMicroseconds(HALF_PERIOD_US);
    digitalWrite(stepPin, LOW);
    delayMicroseconds(HALF_PERIOD_US);
  }
}

void help() {
  Serial.println(F("stepper_test: x/X y/Y = +/-10 mm, s = 40 mm square, e/d = enable/disable"));
  Serial.println(F("Expected: +X moves RIGHT, +Y moves AWAY from you."));
  Serial.println(F("If reversed: power off, flip that motor's connector, or set GRBL $3 later."));
}

void setup() {
  pinMode(X_STEP, OUTPUT);
  pinMode(Y_STEP, OUTPUT);
  pinMode(X_DIR, OUTPUT);
  pinMode(Y_DIR, OUTPUT);
  pinMode(ENABLE, OUTPUT);
  digitalWrite(ENABLE, LOW);  // drivers on
  Serial.begin(115200);
  help();
}

void loop() {
  if (!Serial.available()) return;
  char c = Serial.read();
  switch (c) {
    case 'x': Serial.println(F("X +10 mm")); stepAxis(X_STEP, X_DIR, MOVE_MM); break;
    case 'X': Serial.println(F("X -10 mm")); stepAxis(X_STEP, X_DIR, -MOVE_MM); break;
    case 'y': Serial.println(F("Y +10 mm")); stepAxis(Y_STEP, Y_DIR, MOVE_MM); break;
    case 'Y': Serial.println(F("Y -10 mm")); stepAxis(Y_STEP, Y_DIR, -MOVE_MM); break;
    case 's':
      Serial.println(F("40 mm square"));
      stepAxis(X_STEP, X_DIR, 40);
      stepAxis(Y_STEP, Y_DIR, 40);
      stepAxis(X_STEP, X_DIR, -40);
      stepAxis(Y_STEP, Y_DIR, -40);
      break;
    case 'e': digitalWrite(ENABLE, LOW); Serial.println(F("drivers enabled")); break;
    case 'd': digitalWrite(ENABLE, HIGH); Serial.println(F("drivers disabled")); break;
    case '\r': case '\n': break;
    default: help();
  }
}
