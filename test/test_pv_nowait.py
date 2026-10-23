"""Trig In 1 detection + burn path: block Mark Points, pulse DTR concurrently.

-NoWait made DONE return before PV armed for Trig In 1, so later DTR never burned.
"""

from __future__ import annotations

import time

from prairie_live.pv_script import _script_parts_wait_on_trigger
from prairie_live.stim_fire import run_while_pulsing_dtr


def test_detect_trig_in_1():
	parts = [
		"-MarkAllPoints",
		"1",
		"False",
		"0.1",
		"0.2",
		"10",
		"Monaco",
		"40",
		"True",
		"0.1",
		"5",
		"Trig In 1",
		"1",
	]
	assert _script_parts_wait_on_trigger(parts) is True


def test_detect_none_trigger():
	parts = ["-MarkAllPoints", "1", "False", "0.1", "0.2", "10", "Monaco", "40", "None"]
	assert _script_parts_wait_on_trigger(parts) is False


class _FakeTtl:
	def __init__(self):
		self.pulses: list[float] = []

	def pulse_dtr(self, width_s: float):
		self.pulses.append(width_s)
		return time.monotonic()


def test_run_while_pulsing_dtr_overlaps_fn():
	ttl = _FakeTtl()
	order: list[str] = []

	def _mp() -> str:
		order.append("mp_enter")
		# DTR arm_delay is 0.05; wait so the pulse lands during this call.
		time.sleep(0.08)
		order.append("mp_done")
		return "ok"

	out = run_while_pulsing_dtr(
		_mp,
		ttl=ttl,
		n_pulses=1,
		ttl_width_s=0.01,
		arm_delay_s=0.05,
	)
	assert out == "ok"
	assert ttl.pulses == [0.01]
	assert order == ["mp_enter", "mp_done"]
