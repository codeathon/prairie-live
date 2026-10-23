"""Trig In 1 detection + burn path: block Mark Points, pulse DTR concurrently.

-NoWait made DONE return before PV armed for Trig In 1, so later DTR never burned.
DTR must start *after* -LoadMarkPoints / -Wait, once -MarkPoints/-slm is in flight.
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
		self.times: list[float] = []

	def pulse_dtr(self, width_s: float):
		self.pulses.append(width_s)
		self.times.append(time.monotonic())
		return self.times[-1]


def test_run_while_pulsing_dtr_starts_mp_before_dtr():
	ttl = _FakeTtl()
	order: list[str] = []
	t0 = time.monotonic()

	def _mp() -> str:
		order.append("mp_enter")
		# Stay blocked longer than arm_delay so the pulse lands during wait.
		time.sleep(0.12)
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
	# Pulse after arm_delay, while Mark Points was still running.
	assert ttl.times[0] - t0 >= 0.04


def test_slm_script_power_ui_to_fraction():
	from prairie_live.sync_loop import _slm_script_power

	assert _slm_script_power(160.0, {}) == 0.4
	assert _slm_script_power(0.75, {}) == 0.75
