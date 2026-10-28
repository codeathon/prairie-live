"""run_while_pulsing_dtr: early DONE handling for Live-on packed -slm."""

from __future__ import annotations

import pytest

from prairie_live.stim_fire import run_while_pulsing_dtr


class _Ttl:
	def __init__(self):
		self.pulses = []

	def pulse_dtr(self, w):
		self.pulses.append(w)


def test_early_done_raises_by_default():
	ttl = _Ttl()
	with pytest.raises(RuntimeError, match="before the first DTR"):
		run_while_pulsing_dtr(lambda: None, ttl=ttl, n_pulses=2, arm_delay_s=0.05)
	assert ttl.pulses == []


def test_early_done_allowed_keeps_schedule():
	ttl = _Ttl()
	run_while_pulsing_dtr(
		lambda: None,
		ttl=ttl,
		n_pulses=3,
		gap_s=0.01,
		arm_delay_s=0.05,
		allow_early_done=True,
	)
	assert len(ttl.pulses) == 3
