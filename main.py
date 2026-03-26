import serial
import threading
import time
import sys

# ─── Config ───────────────────────────────────────────────────────────────────
SERIAL_PORT   = '/dev/ttyUSB0'   # Windows: 'COM3' etc
BAUD_RATE     = 115200

# Default speeds per axis (µs between steps — higher = slower)
DEFAULT_SPEED = {
    'X': 500,
    'Y': 500,
    'Z': 1500,   # TB6600 on Z — conservative
}

# Z minimum speed (TB6600 — don't push it as hard as TMC2209s)
Z_MIN_SPEED = 200

# ─── Serial setup ─────────────────────────────────────────────────────────────
ser = None

def connect():
    global ser
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
        time.sleep(2)
        while ser.in_waiting:
            print(ser.readline().decode('utf-8', errors='ignore').strip())
        print(f"Connected on {SERIAL_PORT}\n")
        return True
    except Exception as e:
        print(f"Connection failed: {e}")
        return False

def send(cmd):
    if ser and ser.is_open:
        try:
            ser.write((cmd + '\n').encode())
            time.sleep(0.05)
            responses = []
            while ser.in_waiting:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    responses.append(line)
            for r in responses:
                print(f"  ← {r}")
            return responses
        except Exception as e:
            print(f"Send error: {e}")
    return []

# ─── Serial reader thread ──────────────────────────────────────────────────────
def reader_thread():
    while True:
        if ser and ser.is_open and ser.in_waiting:
            try:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    print(f"\n  ESP32: {line}")
            except:
                pass
        time.sleep(0.01)

# ─── Help ─────────────────────────────────────────────────────────────────────
HELP = """
─────────────────────────────────────────────
  AXIS TEST CONSOLE
─────────────────────────────────────────────
  Axes:  X = M1+M2    Y = M3+M4    Z = M5 (TB6600)

  x / y / z          jog axis forward
  rx / ry / rz       jog axis reverse
  sx / sy / sz       stop axis
  s                  stop all axes
  fx / fy / fz       faster (-50µs)
  lx / ly / lz       slower (+50µs)
  sp X 500           set axis speed in µs
  st                 status all axes
  h                  help
  q                  quit
─────────────────────────────────────────────
  If a motor pair fights itself on the frame,
  flip its INVERT flag in firmware (top of main.cpp)
  Z is TB6600 — starts conservative at 1500µs
─────────────────────────────────────────────
"""

# ─── State ────────────────────────────────────────────────────────────────────
speeds  = dict(DEFAULT_SPEED)
dirs    = {'X': 1, 'Y': 1, 'Z': 1}
jogging = {'X': False, 'Y': False, 'Z': False}

def axis_from_char(c):
    return c.upper() if c.upper() in ('X', 'Y', 'Z') else None

def min_speed(axis):
    return Z_MIN_SPEED if axis == 'Z' else 50

def jog(axis, dir):
    send(f"SPEED:{axis}:{speeds[axis]}")
    send(f"JOG:{axis}:{dir}")
    dirs[axis] = dir
    jogging[axis] = True
    label = " [TB6600]" if axis == 'Z' else ""
    print(f"  {axis}{label} jogging {'FWD' if dir else 'REV'} at {speeds[axis]}µs/step")

def stop(axis):
    send(f"STOP:{axis}")
    jogging[axis] = False

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    if not connect():
        sys.exit(1)

    threading.Thread(target=reader_thread, daemon=True).start()
    print(HELP)

    while True:
        try:
            raw = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if not raw:
            continue

        if raw == 'q':
            break

        elif raw == 'h':
            print(HELP)

        elif raw == 'st':
            send("STATUS")

        elif raw == 's':
            send("STOP")
            for a in jogging: jogging[a] = False
            print("  All axes stopped")

        # jog forward: x y z
        elif len(raw) == 1 and (a := axis_from_char(raw)):
            jog(a, 1)

        # jog reverse: rx ry rz
        elif len(raw) == 2 and raw[0] == 'r' and (a := axis_from_char(raw[1])):
            jog(a, 0)

        # stop axis: sx sy sz
        elif len(raw) == 2 and raw[0] == 's' and (a := axis_from_char(raw[1])):
            stop(a)
            print(f"  {a} stopped")

        # faster: fx fy fz
        elif len(raw) == 2 and raw[0] == 'f' and (a := axis_from_char(raw[1])):
            speeds[a] = max(min_speed(a), speeds[a] - 50)
            send(f"SPEED:{a}:{speeds[a]}")
            floor_note = f" [min {min_speed(a)}µs]" if speeds[a] == min_speed(a) else ""
            print(f"  {a} speed → {speeds[a]}µs/step{floor_note}")

        # slower: lx ly lz
        elif len(raw) == 2 and raw[0] == 'l' and (a := axis_from_char(raw[1])):
            speeds[a] = min(5000, speeds[a] + 50)
            send(f"SPEED:{a}:{speeds[a]}")
            print(f"  {a} speed → {speeds[a]}µs/step")

        # set speed: sp X 500
        elif raw.startswith('sp '):
            parts = raw.split()
            if len(parts) == 3 and (a := axis_from_char(parts[1])) and parts[2].isdigit():
                us = int(parts[2])
                if us >= min_speed(a):
                    speeds[a] = us
                    send(f"SPEED:{a}:{us}")
                    print(f"  {a} speed set to {us}µs/step")
                else:
                    print(f"  Min speed for {a} is {min_speed(a)}µs")
            else:
                print("  Usage: sp <axis> <µs>  e.g. sp X 300")

        else:
            print(f"  Unknown: {raw}  (h for help)")

    print("\nStopping all axes...")
    send("STOP")
    if ser and ser.is_open:
        ser.close()
    print("Done")

if __name__ == '__main__':
    main()