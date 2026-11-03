"""Slow, unmistakable test signal: toggle RTS and DTR so you can find them on Voltage Recording.

    py line_toggle.py --com COM5

For 20 s: RTS high 1 s / low 1 s, DTR the opposite way round (when RTS is high, DTR is low).
Watch AI1/AI2 in Voltage Recording; a square wave at 0.5 Hz should appear on
whichever input the line is teed into. Ctrl+C stops it and leaves both lines low.
"""

import argparse, time
import serial


def _open_low(port):
    """Open with RTS and DTR low from the start (pyserial asserts both by default)."""
    s = serial.Serial()
    s.port = port
    s.rts = False
    s.dtr = False
    s.open()
    s.rts = False
    s.dtr = False
    return s


ap = argparse.ArgumentParser()
ap.add_argument("--com", default="COM5")
ap.add_argument("--seconds", type=float, default=20)
a = ap.parse_args()
s = _open_low(a.com)
try:
    t_end = time.monotonic() + a.seconds
    state = False
    while time.monotonic() < t_end:
        state = not state
        s.rts = state
        s.dtr = not state
        print(
            f"RTS {'HIGH' if state else 'low '}   DTR {'low ' if state else 'HIGH'}",
            flush=True,
        )
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
finally:
    s.rts = False
    s.dtr = False
    s.close()
    print("both lines low")
