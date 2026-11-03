"""Serial DTR/RTS TTL pulses (PackIO / System Trigger sync).

Default wiring: DTR = photostim (Bruker Trig In 1), RTS = visual onset.
``trigger_line="rts"`` swaps them for rigs where Trig In 1 is cabled to RTS:
``pulse_dtr`` / ``set_dtr`` always drive the **photostim** line and
``pulse_rts`` / ``set_rts`` the **visual** line, whichever pins those are.

The port is opened with both lines already low. pyserial's default asserts
RTS and DTR on open, which put a rising edge on Trig In 1 the moment a Run
started (seen on Voltage Recording, Nov 3 2026) — a spurious trigger.
"""

from __future__ import annotations

import time


def open_serial_low(port: str, baudrate: int = 9600):
	"""Open ``port`` with RTS and DTR low from the start (no edge on open)."""
	import serial  # type: ignore

	s = serial.Serial()
	s.port = port
	s.baudrate = baudrate
	s.timeout = 1
	# Set before open(): pyserial applies these instead of asserting both lines.
	s.rts = False
	s.dtr = False
	s.open()
	s.rts = False
	s.dtr = False
	return s


class SerialTtl:
	"""Pulse or hold DTR (photostim) and/or RTS (visual) on a serial port."""

	def __init__(self, port: str, baudrate: int = 9600, trigger_line: str = "dtr"):
		try:
			import serial  # type: ignore
		except ImportError as e:
			raise ImportError(
				"pyserial is required for --serial TTL. "
				"pip install pyserial"
			) from e
		line = str(trigger_line or "dtr").lower()
		if line not in ("dtr", "rts"):
			raise ValueError(f"trigger_line must be dtr or rts, got {trigger_line!r}")
		self.trigger_line = line
		self._ser = open_serial_low(port, baudrate=baudrate)

	def _write(self, pin: str, on: bool) -> None:
		if pin == "rts":
			self._ser.rts = bool(on)
		else:
			self._ser.dtr = bool(on)

	@property
	def _photostim_pin(self) -> str:
		return self.trigger_line

	@property
	def _visual_pin(self) -> str:
		return "dtr" if self.trigger_line == "rts" else "rts"

	def set_dtr(self, on: bool) -> float:
		"""Hold the **photostim** line high/low; returns monotonic time of the edge."""
		t0 = time.monotonic()
		self._write(self._photostim_pin, on)
		return t0

	def set_rts(self, on: bool) -> float:
		"""Hold the **visual** line high/low; returns monotonic time of the edge."""
		t0 = time.monotonic()
		self._write(self._visual_pin, on)
		return t0

	def pulse_dtr(self, width_s: float = 0.01) -> float:
		"""Rising DTR edge for photostim; returns monotonic time of rising edge."""
		t0 = self.set_dtr(True)
		time.sleep(max(width_s, 0.001))
		self.set_dtr(False)
		return t0

	def pulse_rts(self, width_s: float = 0.01) -> float:
		"""Rising RTS edge for visual; returns monotonic time of rising edge."""
		t0 = self.set_rts(True)
		time.sleep(max(width_s, 0.001))
		self.set_rts(False)
		return t0

	def close(self) -> None:
		try:
			self._ser.dtr = False
			self._ser.rts = False
			self._ser.close()
		except OSError:
			pass
