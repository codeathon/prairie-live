"""Run the Mark Points series tests (A, B, C, D, F, bench) against PrairieView.

Put this file in the same folder as lab3groups.gpl, A_/B_/C_/F_*.xml,
Spots_Monaco.gpl/.xml (for bench). Then, on the scope PC with PrairieView open:

    pip install pyserial
    python pv_series_test.py A          # power 0, no trigger
    python pv_series_test.py B          # low power, no trigger, 3 s apart
    python pv_series_test.py C          # low power, ONE start pulse
    python pv_series_test.py D          # = B, pauses so you can start Voltage Recording
    python pv_series_test.py F --first 5 --gap 3   # one DTR per group
    python pv_series_test.py bench      # Bruker 400-mask benchmark (power 0)

Every PV reply and every DTR pulse is printed with a time since -MarkPoints was sent.
Close the prairie-live GUI / relay first so nothing else drives PrairieView.
"""

from __future__ import annotations

import argparse
import os
import socket
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SOH = "\x01"

TESTS = {
    "A": ("lab3groups.gpl", "A_power0_notrigger.xml", 0),
    "B": ("lab3groups.gpl", "B_low_notrigger.xml", 0),
    "C": ("lab3groups.gpl", "C_low_trigin1_start.xml", 1),
    "D": ("lab3groups.gpl", "B_low_notrigger.xml", 0),
    "F": ("lab3groups.gpl", "F_low_trig_each.xml", 3),
    "bench": ("Spots_Monaco.gpl", "Spots_Monaco.xml", 0),
    # Whatever is loaded in the Mark Points window right now (forces --no-load).
    "run": (None, None, 0),
}


class PV:
    def __init__(self, host: str, port: int, password: str) -> None:
        self.sock = socket.create_connection((host, port), timeout=10)
        self.t0 = time.monotonic()
        self.buf = b""
        self._send(password)

    def _send(self, text: str) -> None:
        self.sock.sendall((text + "\r\n").encode("ascii"))

    def stamp(self) -> str:
        return f"[{time.monotonic() - self.t0:8.3f}s]"

    def _lines(self, wait_s: float):
        """Yield complete reply lines arriving within wait_s (keeps partial data)."""
        end = time.monotonic() + wait_s
        while True:
            while b"\r\n" in self.buf:
                line, self.buf = self.buf.split(b"\r\n", 1)
                yield line.decode("ascii", "replace").strip()
            left = end - time.monotonic()
            if left <= 0:
                return
            self.sock.settimeout(max(left, 0.05))
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                return
            if not chunk:
                raise ConnectionError("PrairieView closed the connection")
            self.buf += chunk

    def drain(self) -> None:
        """Print and discard late replies from earlier commands (keeps replies in sync)."""
        for txt in self._lines(0.3):
            print(
                f"{self.stamp()} << {txt}   (late reply from an earlier command)",
                flush=True,
            )

    def cmd(
        self, *parts: str, timeout: float = 60.0, drain: bool = True
    ) -> str:
        if drain:
            self.drain()
        print(f"{self.stamp()} >> {' '.join(parts)}", flush=True)
        self._send(SOH.join(parts))
        for txt in self._lines(timeout):
            print(f"{self.stamp()} << {txt}", flush=True)
            if txt == "DONE":
                return "DONE"
        print(f"{self.stamp()} !! no DONE within {timeout:g}s", flush=True)
        raise TimeoutError(
            f"PrairieView did not finish {parts[0]} within {timeout:g}s — check for an "
            "error dialog in PrairieView. Stopping so replies don't get out of sync."
        )

    def close(self) -> None:
        try:
            self._send("-Exit")
            time.sleep(0.3)
        except OSError:
            pass
        self.sock.close()


def open_serial(com: str):
    """Open the DTR port up front so a missing port fails before -MarkPoints."""
    try:
        import serial  # pyserial
        from serial.tools import list_ports
    except ImportError:
        raise SystemExit("pyserial missing: py -m pip install pyserial")
    try:
        s = serial.Serial()
        s.port = com
        s.rts = (
            False  # set before open: pyserial otherwise asserts both lines
        )
        s.dtr = False
        s.open()
    except Exception as exc:
        ports = [
            f"{p.device} ({p.description})" for p in list_ports.comports()
        ]
        raise SystemExit(
            f"cannot open {com}: {exc}\nSerial ports on this PC: "
            + (", ".join(ports) if ports else "NONE")
            + "\nPick the one wired to Trig In 1 with --com COMx."
        )
    s.dtr = False
    s.rts = False
    return s


def _set_line(s, line: str, on: bool) -> None:
    if line == "rts":
        s.rts = on
    else:
        s.dtr = on


def pulse_train(
    s,
    n: int,
    first: float,
    gap: float,
    width: float,
    pv: PV,
    line: str = "dtr",
) -> None:
    try:
        time.sleep(first)
        for i in range(n):
            _set_line(s, line, True)
            time.sleep(width)
            _set_line(s, line, False)
            print(
                f"{pv.stamp()} ** {line.upper()} pulse {i + 1}/{n}",
                flush=True,
            )
            if i < n - 1:
                time.sleep(gap)
    finally:
        s.dtr = False
        s.close()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="PrairieView Mark Points series tests"
    )
    ap.add_argument("test", choices=list(TESTS))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=1236)
    ap.add_argument("--password", default="0000")
    ap.add_argument("--com", default="COM3")
    ap.add_argument(
        "--first",
        type=float,
        default=5.0,
        help="s from -MarkPoints to first DTR",
    )
    ap.add_argument(
        "--gap", type=float, default=3.0, help="s between DTR pulses (F)"
    )
    ap.add_argument(
        "--width", type=float, default=0.01, help="TTL pulse width s"
    )
    ap.add_argument(
        "--line",
        choices=("dtr", "rts"),
        default="dtr",
        help="serial line wired to Trig In 1 (dtr or rts)",
    )
    ap.add_argument("--live", choices=("on", "off"), default="on")
    ap.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="s to wait for -MarkPoints DONE",
    )
    ap.add_argument(
        "--pulses",
        type=int,
        default=0,
        help="run: DTR pulses to send (one per triggered line); default 0",
    )
    ap.add_argument(
        "--no-dtr",
        action="store_true",
        help="don't pulse from this PC (trigger cable is on another PC: run dtr_train.py there)",
    )
    ap.add_argument(
        "--no-load",
        action="store_true",
        help="skip -LoadMarkPoints: use the point list + series you loaded by hand in the GUI",
    )
    a = ap.parse_args()

    gpl_name, xml_name, n_pulses = TESTS[a.test]
    if a.test == "run":
        a.no_load = True
        n_pulses = max(int(a.pulses or 0), 0)
    gpl = os.path.join(HERE, gpl_name) if gpl_name else None
    xml = os.path.join(HERE, xml_name) if xml_name else None
    if not a.no_load:
        for p in (gpl, xml):
            if not os.path.isfile(p):
                raise SystemExit(f"missing file next to this script: {p}")

    if n_pulses and a.no_dtr:
        gap = a.gap if a.test in ("F", "run") else 0.0
        print(
            f"--no-dtr: this test needs {n_pulses} DTR pulse(s) from the other PC.\n"
            f"  Right after you press Enter here, run there:\n"
            f"  py .\\dtr_train.py --com COM3 --n {n_pulses} --first {a.first:g} --gap {gap:g}\n"
        )
        n_pulses = 0
    ser = open_serial(a.com) if n_pulses else None
    pv = PV(a.host, a.port, a.password)
    try:
        pv.cmd("-MarkPointsMetadata", "True")
        if not a.no_load:
            pv.cmd("-LoadMarkPoints", gpl, "True")
            pv.cmd("-LoadMarkPoints", xml)
        pv.cmd("-SetFileName", "MarkPoints", f"test{a.test}", "addDateTime")
        pv.cmd("-LiveScan", a.live)
        input(
            "\nCheck the Mark Points window (points/groups/triggers look right?)"
            + (
                "\nNow press START in the Voltage Recording tab."
                if a.test in ("D", "F")
                else ""
            )
            + "\nPress Enter to send -MarkPoints … "
        )
        pv.drain()
        pv.t0 = time.monotonic()  # times below are relative to -MarkPoints
        pulser = None
        if n_pulses:
            gap = a.gap if a.test in ("F", "run") else 0.0
            pulser = threading.Thread(
                target=pulse_train,
                args=(ser, n_pulses, a.first, gap, a.width, pv, a.line),
                daemon=True,
            )
            print(
                f"   will pulse DTR on {a.com}: {n_pulses}x, first at {a.first:g}s"
                + (f", every {gap:g}s" if n_pulses > 1 else ""),
                flush=True,
            )
            pulser.start()
        res = pv.cmd("-MarkPoints", timeout=a.timeout, drain=False)
        if pulser is not None:
            pulser.join()
        print(f"\n-MarkPoints result: {res} at {pv.stamp()}")
        print(
            "Note what burned, and (D/F) save the Voltage Recording CSV → python vr_edges.py <csv>"
        )
    except TimeoutError as exc:
        print(f"\nSTOPPED: {exc}")
    except KeyboardInterrupt:
        print("\ninterrupted — sending -Abort")
        try:
            pv.cmd("-Abort", timeout=5)
        except Exception:
            pass
    finally:
        pv.close()


if __name__ == "__main__":
    main()
