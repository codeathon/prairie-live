"""Pulse DTR or RTS on a COM port: first pulse after --first s, then every --gap s.

    py dtr_train.py --com COM5 --line rts --n 2 --first 1 --gap 2

Prints each pulse's wall-clock time. Needs: py -m pip install pyserial
"""

import argparse, datetime, time
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
ap.add_argument(
    "--line",
    choices=("dtr", "rts"),
    default="dtr",
    help="line wired to Trig In 1",
)
ap.add_argument("--n", type=int, default=2)
ap.add_argument("--first", type=float, default=1.0, help="s before pulse 1")
ap.add_argument("--gap", type=float, default=2.0, help="s between pulses")
ap.add_argument("--width", type=float, default=0.01)
a = ap.parse_args()
s = _open_low(a.com)


def setl(on):
    if a.line == "rts":
        s.rts = on
    else:
        s.dtr = on


t0 = time.monotonic()
try:
    time.sleep(a.first)
    for i in range(a.n):
        setl(True)
        time.sleep(a.width)
        setl(False)
        print(
            f"{a.line.upper()} pulse {i + 1}/{a.n}  +{time.monotonic() - t0:7.3f}s  {datetime.datetime.now():%H:%M:%S.%f}",
            flush=True,
        )
        if i < a.n - 1:
            time.sleep(a.gap)
finally:
    s.dtr = False
    s.rts = False
    s.close()
