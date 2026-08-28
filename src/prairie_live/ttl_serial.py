"""Serial DTR/RTS TTL pulses (PackIO / PFI sync).

Matches the PsychoPy-style COM3 DTR=photostim, RTS=visual wiring. Wire DTR
to the PFI line Prairie Mark Points waits on (e.g. PFI1).
"""

from __future__ import annotations

import time


class SerialTtl:
	"""Pulse or hold DTR (photostim) and/or RTS (visual) on a serial port."""

	def __init__(self, port: str, baudrate: int = 9600):
		try:
			import serial  # type: ignore
		except ImportError as e:
			raise ImportError(
				"pyserial is required for --serial TTL. "
				"pip install pyserial"
			) from e
		self._ser = serial.Serial(port, baudrate=baudrate, timeout=1)
		# Idle low so the rising edge is unambiguous.
		self._ser.dtr = False
		self._ser.rts = False

	def set_dtr(self, on: bool) -> float:
		"""Hold DTR high/low; returns monotonic time of the edge."""
		t0 = time.monotonic()
		self._ser.dtr = bool(on)
		return t0

	def set_rts(self, on: bool) -> float:
		"""Hold RTS high/low; returns monotonic time of the edge."""
		t0 = time.monotonic()
		self._ser.rts = bool(on)
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
