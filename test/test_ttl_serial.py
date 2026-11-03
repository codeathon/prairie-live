"""Serial TTL: no edge on open; photostim line selectable (DTR or RTS)."""

from __future__ import annotations

import sys
import types

import pytest


class _FakeSerial:
	log: list = []

	def __init__(self, *a, **k):
		self.is_open = False
		self._rts = True  # pyserial default: asserted
		self._dtr = True

	def _rec(self, what):
		_FakeSerial.log.append((what, self.is_open))

	@property
	def rts(self):
		return self._rts

	@rts.setter
	def rts(self, v):
		self._rts = v
		self._rec(f"rts={v}")

	@property
	def dtr(self):
		return self._dtr

	@dtr.setter
	def dtr(self, v):
		self._dtr = v
		self._rec(f"dtr={v}")

	def open(self):
		# A real port drives the pins to the stored state at open.
		self.is_open = True
		self._rec(f"open rts={self._rts} dtr={self._dtr}")

	def close(self):
		self.is_open = False


@pytest.fixture
def fake_serial(monkeypatch):
	mod = types.ModuleType("serial")
	mod.Serial = _FakeSerial
	monkeypatch.setitem(sys.modules, "serial", mod)
	_FakeSerial.log = []
	return _FakeSerial


def test_port_opens_with_both_lines_low(fake_serial):
	from prairie_live.ttl_serial import SerialTtl

	SerialTtl("COM5")
	opens = [w for w, _ in fake_serial.log if w.startswith("open")]
	assert opens == ["open rts=False dtr=False"]


def test_default_photostim_is_dtr(fake_serial):
	from prairie_live.ttl_serial import SerialTtl

	t = SerialTtl("COM5")
	fake_serial.log = []
	t.pulse_dtr(0.001)
	assert [w for w, _ in fake_serial.log] == ["dtr=True", "dtr=False"]
	fake_serial.log = []
	t.pulse_rts(0.001)
	assert [w for w, _ in fake_serial.log] == ["rts=True", "rts=False"]


def test_rts_wiring_swaps_photostim_and_visual(fake_serial):
	from prairie_live.ttl_serial import SerialTtl

	t = SerialTtl("COM5", trigger_line="rts")
	fake_serial.log = []
	t.pulse_dtr(0.001)  # photostim → RTS pin
	assert [w for w, _ in fake_serial.log] == ["rts=True", "rts=False"]
	fake_serial.log = []
	t.set_rts(True)  # visual → DTR pin
	assert [w for w, _ in fake_serial.log] == ["dtr=True"]


def test_bad_line_rejected(fake_serial):
	from prairie_live.ttl_serial import SerialTtl

	with pytest.raises(ValueError):
		SerialTtl("COM5", trigger_line="cts")
