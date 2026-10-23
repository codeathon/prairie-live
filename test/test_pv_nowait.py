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
		rescue_window_s=0.0,
	)
	assert out == "ok"
	assert ttl.pulses == [0.01]
	assert order == ["mp_enter", "mp_done"]
	# Pulse after arm_delay, while Mark Points was still running.
	assert ttl.times[0] - t0 >= 0.04


def test_packed_slm_emits_exactly_one_dtr_per_group_no_rescue():
	"""Extra edges would release the next packed hologram early."""
	ttl = _FakeTtl()

	def _slm() -> str:
		# Longer than arm + all gaps + pulses — must not add rescue DTRs.
		time.sleep(0.55)
		return "ok"

	out = run_while_pulsing_dtr(
		_slm,
		ttl=ttl,
		n_pulses=3,
		ttl_width_s=0.01,
		gap_s=0.05,
		arm_delay_s=0.05,
		rescue_window_s=2.0,  # forced off when n_pulses > 1
	)
	assert out == "ok"
	assert ttl.pulses == [0.01, 0.01, 0.01]


def test_single_dtr_no_rescue_during_long_stim_dwell():
	"""Thread stays alive while hologram burns — that is not a missed arm."""
	ttl = _FakeTtl()

	def _slm() -> str:
		time.sleep(0.6)
		return "ok"

	out = run_while_pulsing_dtr(
		_slm,
		ttl=ttl,
		n_pulses=1,
		ttl_width_s=0.01,
		arm_delay_s=0.05,
		rescue_window_s=0.1,  # arm window ends ~0.15s; dwell continues
	)
	assert out == "ok"
	assert ttl.pulses == [0.01]


def test_slm_script_power_keeps_ui_scale():
	from prairie_live.sync_loop import _slm_script_power

	# -slm wants UI UncagingLaserPower, not series XML ÷400.
	assert _slm_script_power(160.0, {}) == 160.0
	assert _slm_script_power(0.75, {}) == 0.75
