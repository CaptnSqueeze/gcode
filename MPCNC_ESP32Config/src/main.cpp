#include <Arduino.h>

// ─── Pin definitions — must match wiring diagram ──────────────────────────────
const int STEP_PINS[6] = {0, 27, 18, 14, 33,  4};  // index 1-5
const int DIR_PINS[6]  = {0, 26, 19, 13, 32,  5};  // index 1-5

// ─── Axis config ──────────────────────────────────────────────────────────────
// X = motors 1+2, Y = motors 3+4, Z = motor 5 (TB6600)
// If one motor in a pair runs backwards on the frame, flip its invert flag.
const bool INVERT_M1 = false;   // X1
const bool INVERT_M2 = true;    // X2 — mirrored mount, likely needs invert
const bool INVERT_M3 = false;   // Y1
const bool INVERT_M4 = true;    // Y2 — mirrored mount, likely needs invert
const bool INVERT_M5 = false;   // Z  (TB6600)

// Axis motor pairs
const int AXIS_X[2] = {1, 2};
const int AXIS_Y[2] = {3, 4};
const int AXIS_Z[1] = {5};

// ─── Motor state ──────────────────────────────────────────────────────────────
volatile bool     motorJogging[6] = {false, false, false, false, false, false};
volatile bool     motorDir[6]     = {false, true, true, true, true, true};
volatile uint32_t stepDelay[6]    = {0, 500, 500, 500, 500, 1500};

// ─── Apply direction to a motor, respecting invert flag ───────────────────────
void setDir(int m, bool dir) {
  const bool inverts[6] = {false, INVERT_M1, INVERT_M2, INVERT_M3, INVERT_M4, INVERT_M5};
  bool actual = inverts[m] ? !dir : dir;
  motorDir[m] = dir;  // store logical direction
  digitalWrite(DIR_PINS[m], actual ? HIGH : LOW);
}

// ─── Step tasks — one FreeRTOS task per motor ─────────────────────────────────
void stepTask(void *param) {
  int m = (int)param;
  while (true) {
    if (motorJogging[m]) {
      digitalWrite(STEP_PINS[m], HIGH);
      delayMicroseconds(5);
      digitalWrite(STEP_PINS[m], LOW);
      delayMicroseconds(stepDelay[m]);
    } else {
      vTaskDelay(1);
    }
  }
}

// ─── Axis helpers ─────────────────────────────────────────────────────────────
void startAxis(const int* motors, int count, bool dir, uint32_t speed) {
  for (int i = 0; i < count; i++) {
    int m = motors[i];
    stepDelay[m] = speed;
    setDir(m, dir);
    delayMicroseconds(10);
    motorJogging[m] = true;
  }
}

void stopAxis(const int* motors, int count) {
  for (int i = 0; i < count; i++) motorJogging[motors[i]] = false;
}

// ─── Command handler ──────────────────────────────────────────────────────────
void handleCommand(String cmd) {
  cmd.trim();

  // JOG:AXIS:DIR — e.g. JOG:X:1  JOG:Y:0  JOG:Z:1
  if (cmd.startsWith("JOG:")) {
    char axis = cmd.charAt(4);
    int  dir  = cmd.substring(6).toInt();
    if      (axis == 'X') { startAxis(AXIS_X, 2, dir, stepDelay[1]); Serial.println("OK:JOG:X:" + String(dir)); }
    else if (axis == 'Y') { startAxis(AXIS_Y, 2, dir, stepDelay[3]); Serial.println("OK:JOG:Y:" + String(dir)); }
    else if (axis == 'Z') { startAxis(AXIS_Z, 1, dir, stepDelay[5]); Serial.println("OK:JOG:Z:" + String(dir)); }
    else Serial.println("ERROR:INVALID_AXIS");
  }

  // STOP:AXIS or STOP
  else if (cmd.startsWith("STOP")) {
    if (cmd == "STOP") {
      for (int i = 1; i <= 5; i++) motorJogging[i] = false;
      Serial.println("OK:STOP:ALL");
    } else {
      char axis = cmd.charAt(5);
      if      (axis == 'X') { stopAxis(AXIS_X, 2); Serial.println("OK:STOP:X"); }
      else if (axis == 'Y') { stopAxis(AXIS_Y, 2); Serial.println("OK:STOP:Y"); }
      else if (axis == 'Z') { stopAxis(AXIS_Z, 1); Serial.println("OK:STOP:Z"); }
      else Serial.println("ERROR:INVALID_AXIS");
    }
  }

  // SPEED:AXIS:us — e.g. SPEED:X:500
  else if (cmd.startsWith("SPEED:")) {
    char     axis  = cmd.charAt(6);
    uint32_t speed = cmd.substring(8).toInt();
    if (speed < 50) { Serial.println("ERROR:TOO_FAST"); return; }
    if      (axis == 'X') { stepDelay[1] = stepDelay[2] = speed; }
    else if (axis == 'Y') { stepDelay[3] = stepDelay[4] = speed; }
    else if (axis == 'Z') { stepDelay[5] = speed; }
    else { Serial.println("ERROR:INVALID_AXIS"); return; }
    Serial.println("OK:SPEED:" + String(axis) + ":" + String(speed));
  }

  // STATUS
  else if (cmd == "STATUS") {
    Serial.printf("X (M1+M2): %s  DIR=%s  SPEED=%uus\n",
      motorJogging[1] ? "JOGGING" : "STOPPED", motorDir[1] ? "FWD" : "REV", stepDelay[1]);
    Serial.printf("Y (M3+M4): %s  DIR=%s  SPEED=%uus\n",
      motorJogging[3] ? "JOGGING" : "STOPPED", motorDir[3] ? "FWD" : "REV", stepDelay[3]);
    Serial.printf("Z (M5/TB6600): %s  DIR=%s  SPEED=%uus\n",
      motorJogging[5] ? "JOGGING" : "STOPPED", motorDir[5] ? "FWD" : "REV", stepDelay[5]);
    Serial.println("OK:STATUS");
  }

  else {
    Serial.println("ERROR:UNKNOWN_CMD:" + cmd);
  }

  Serial.flush();
}

// ─── Setup ────────────────────────────────────────────────────────────────────
void setup() {
  for (int i = 1; i <= 5; i++) {
    pinMode(STEP_PINS[i], OUTPUT);
    pinMode(DIR_PINS[i],  OUTPUT);
    digitalWrite(STEP_PINS[i], LOW);
    setDir(i, true);
  }

  Serial.begin(115200);
  delay(2000);

  // All step tasks on core 1 — core 0 is reserved for WiFi stack
  for (int i = 1; i <= 5; i++) {
    xTaskCreatePinnedToCore(
      stepTask,
      ("motor" + String(i)).c_str(),
      1024,
      (void *)i,
      1,
      NULL,
      1
    );
  }

  while (Serial.available()) Serial.read();

  Serial.println("READY");
  Serial.println("Axes: X(M1+M2)  Y(M3+M4)  Z(M5/TB6600)");
  Serial.println("Commands: JOG:X/Y/Z:0/1  STOP:X/Y/Z  STOP  SPEED:X/Y/Z:us  STATUS");
  Serial.flush();
}

// ─── Loop ─────────────────────────────────────────────────────────────────────
void loop() {
  static String buf = "";
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      buf.trim();
      if (buf.length() > 0) {
        handleCommand(buf);
        buf = "";
      }
    } else if (buf.length() < 64) {
      buf += c;
    }
  }
  yield();
}