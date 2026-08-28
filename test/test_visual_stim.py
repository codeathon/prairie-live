"""Visual stimulus + TTL timing (no PsychoPy window in CI)."""

from __future__ import annotations

import random
import time

import pytest

from prairie_live.sync_loop import _run_stim_epoch
from prairie_live.visual_stim import (
	NullVisualStim,
	VisualConfig,
	visual_config_from_dict,
)


class MockTtl:
	"""Record RTS/DTR edges with monotonic timestamps."""

	def __init__(self) -> None:
		self.events: list[tuple[str, bool, float]] = []

	def set_rts(self, on: bool) -> float:
		t = time.monotonic()
		self.events.append(("rts", bool(on), t))
		return t

	def set_dtr(self, on: bool) -> float:
		t = time.monotonic()
		self.events.append(("dtr", bool(on), t))
		return t

	def pulse_dtr(self, width_s: float) -> float:
		t0 = self.set_dtr(True)
		time.sleep(max(width_s, 0.001))
		self.set_dtr(False)
		return t0


def _fast_visual_cfg() -> VisualConfig:
	# Short epoch for unit tests; lead_frame still ≈12 @ 120 Hz.
	return VisualConfig(
		enabled=True,
		isi_s=0.0,
		refresh_hz=120.0,
		lead_ms=100.0,
		dtr_frames=6,
		grating_frames=24,
	)


def test_visual_config_from_dict_defaults():
	cfg = visual_config_from_dict({"visual_enabled": True})
	assert cfg.enabled is True
	assert cfg.lead_frame == 12
	assert abs(cfg.dtr_width_s - 6 / 120.0) < 1e-6


def test_rts_precedes_dtr_with_visual_lead():
	ttl = MockTtl()
	cfg = _fast_visual_cfg()
	visual = NullVisualStim(cfg)
	row: dict = {}
	rng = random.Random(0)

	_run_stim_epoch(
		row,
		trigger="serial",
		ttl=ttl,
		ttl_width_s=0.01,
		dry_run=False,
		visual_stim=visual,
		visual_cfg=cfg,
		rng=rng,
		relay=None,
		want=False,
		f0_s=0.1,
		f1_s=0.1,
		frame_poll_s=0.01,
		wait_s=0.0,
	)

	rts_on = [e for e in ttl.events if e[0] == "rts" and e[1] is True]
	dtr_on = [e for e in ttl.events if e[0] == "dtr" and e[1] is True]
	assert len(rts_on) == 1
	assert len(dtr_on) == 1
	assert rts_on[0][2] < dtr_on[0][2]
	assert row.get("t_rts_mono") is not None
	assert row.get("t_ttl_mono") is not None
	assert row.get("visual_ori_deg") is not None
	assert row.get("visual_contrast_pct") is not None


def test_dtr_width_matches_frame_count():
	ttl = MockTtl()
	cfg = _fast_visual_cfg()
	visual = NullVisualStim(cfg)
	row: dict = {}

	_run_stim_epoch(
		row,
		trigger="serial",
		ttl=ttl,
		ttl_width_s=0.01,
		dry_run=False,
		visual_stim=visual,
		visual_cfg=cfg,
		rng=random.Random(1),
		relay=None,
		want=False,
		f0_s=0.1,
		f1_s=0.1,
		frame_poll_s=0.01,
		wait_s=0.0,
	)

	dtr_edges = [e for e in ttl.events if e[0] == "dtr"]
	assert len(dtr_edges) == 2
	width = dtr_edges[1][2] - dtr_edges[0][2]
	assert abs(width - cfg.dtr_width_s) < 0.02


def test_visual_disabled_preserves_pulse_dtr():
	ttl = MockTtl()
	cfg = VisualConfig(enabled=False)
	visual = NullVisualStim(cfg)
	row: dict = {}

	_run_stim_epoch(
		row,
		trigger="serial",
		ttl=ttl,
		ttl_width_s=0.02,
		dry_run=False,
		visual_stim=visual,
		visual_cfg=cfg,
		rng=random.Random(2),
		relay=None,
		want=False,
		f0_s=0.1,
		f1_s=0.1,
		frame_poll_s=0.01,
		wait_s=0.0,
	)

	assert "visual_ori_deg" not in row
	dtr_on = [e for e in ttl.events if e[0] == "dtr" and e[1] is True]
	assert len(dtr_on) == 1
	assert row.get("t_ttl_mono") is not None
