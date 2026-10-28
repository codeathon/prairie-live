"""Unit tests for -MarkAllPoints (-slm) argument builder."""

from __future__ import annotations

import pytest

from prairie_live.mark_all_points import (
	build_mark_all_points_parts,
	pack_mark_all_points,
	spiral_size_fov_frac,
	stim_params_from_meta,
)


def test_spiral_frac():
	assert spiral_size_fov_frac(54.5, 545.0) == pytest.approx(0.1)
	with pytest.raises(ValueError):
		spiral_size_fov_frac(10, 0)


def test_build_2d_spiral_trigger():
	pts = [{"id": "1", "x": 0.1, "y": 0.2}, {"id": "2", "x": 0.3, "y": 0.4}]
	parts = build_mark_all_points_parts(
		pts,
		power=0.75,
		laser="Monaco",
		duration_ms=16.92,
		use_3d=False,
		spiral=True,
		spiral_size_um=54.5,
		spiral_revolutions=8,
		fov_width_um=545.0,
		trigger_selection="Trig In 1",
	)
	assert parts[0] == "-MarkAllPoints"
	assert parts[1] == "2"
	assert parts[2] == "False"
	assert parts[3:7] == ["0.1", "0.2", "0.3", "0.4"]
	assert parts[7] == "16.92"
	assert parts[8] == "Monaco"
	assert parts[9] == "0.75"
	assert parts[10:13] == ["True", "0.1", "8"]
	assert parts[13] == "Trig In 1"
	# Countable trigger sources require Trigger Count (PV rejects without it).
	assert parts[14] == "1"


def test_pack_two_sets_delay_between():
	g0 = [{"id": "1", "x": 0.1, "y": 0.2}, {"id": "2", "x": 0.3, "y": 0.4}]
	g1 = [
		{"id": "3", "x": 0.5, "y": 0.6},
		{"id": "4", "x": 0.7, "y": 0.8},
		{"id": "5", "x": 0.15, "y": 0.25},
	]
	parts = pack_mark_all_points(
		[g0, g1],
		power=0.75,
		laser="Monaco",
		duration_ms=16.92,
		use_3d=False,
		spiral=True,
		spiral_size_um=54.5,
		spiral_revolutions=8,
		fov_width_um=545.0,
		trigger_selection="Trig In 1",
		trigger_count=1,
		delay_ms=150.0,
	)
	assert parts[0] == "-MarkAllPoints"
	# First set: … Trig In 1, TriggerCount, Delay; last set: … Trig In 1, Count (no Delay).
	assert parts.count("Trig In 1") == 2
	assert "150" in parts
	i0 = parts.index("Trig In 1")
	assert parts[i0 + 1] == "1"
	assert parts[i0 + 2] == "150"
	assert parts[-2:] == ["Trig In 1", "1"]


def test_none_trigger_omits_count():
	pts = [{"id": "1", "x": 0.1, "y": 0.2}]
	parts = build_mark_all_points_parts(
		pts,
		power=0.5,
		spiral=False,
		trigger_selection="None",
	)
	assert parts[-1] == "None"
	# No Trigger Count token after None (only countable sources need it).
	assert parts.count("None") == 1


def test_spiral_requires_fov():
	pts = [{"id": "1", "x": 0.1, "y": 0.2}]
	with pytest.raises(ValueError, match="fov_width_um"):
		build_mark_all_points_parts(
			pts,
			power=0.5,
			spiral=True,
			spiral_size_um=54.5,
			fov_width_um=None,
		)


def test_spiral_size_fov_overrides_um():
	# Galvo SpiralSize→FOV frac from fire-time geometry must win over µm defaults.
	pts = [{"id": "1", "x": 0.1, "y": 0.2}]
	parts = build_mark_all_points_parts(
		pts,
		power=160,
		spiral=True,
		spiral_size_um=54.5,
		fov_width_um=1136.7,
		spiral_size_fov=0.022,
		trigger_selection="None",
	)
	assert parts[parts.index("True") + 1] == "0.022"


def test_rejects_galvo_coords_for_slm():
	# Sending raw galvo volts as -slm FOV fractions misplaces burns.
	pts = [{"id": "1", "x": -3.0, "y": -2.0}]
	with pytest.raises(ValueError, match="outside FOV 0–1"):
		build_mark_all_points_parts(
			pts,
			power=160,
			spiral=False,
			trigger_selection="None",
		)


def test_stim_params_from_meta():
	meta = {
		"uncaging_laser": "Monaco",
		"duration": 16.92,
		"use_3d": "False",
		"is_spiral": "True",
		"spiral_size_um": "54.5",
		"spiral_revolutions": "8",
		"fov_width_um": 500.0,
		"trigger_selection": "Trig In 1",
		"initial_delay_ms": 22.1,
	}
	p = stim_params_from_meta(meta)
	assert p["laser"] == "Monaco"
	assert p["spiral"] is True
	assert p["use_3d"] is False
	assert p["fov_width_um"] == 500.0
	assert p["initial_delay_ms"] == 22.1
	assert p["trigger_count"] == 1


def _set_triggers(parts: list[str]) -> list[str]:
	"""Trigger token of each set (anything that is 'Trig In 1' or 'None')."""
	return [p for p in parts if p in ("Trig In 1", "None")]


def test_bnc_repetitions_collapsed_to_one_set():
	# Trig In 1 + Live off cannot pack Trigger=None repeats (early DONE / no fire).
	pts = [{"id": "1", "x": 0.1, "y": 0.2}, {"id": "2", "x": 0.3, "y": 0.4}]
	parts = build_mark_all_points_parts(
		pts,
		power=140,
		duration_ms=36,
		spiral=False,
		trigger_selection="Trig In 1",
		repetitions=3,
		rep_delay_ms=14,
	)
	assert parts[0] == "-MarkAllPoints"
	assert parts.count("2") == 1
	assert _set_triggers(parts) == ["Trig In 1"]
	assert parts[-2:] == ["Trig In 1", "1"]
	assert "14" not in parts


def test_none_trigger_repetitions_pack_followup_sets():
	pts = [{"id": "1", "x": 0.1, "y": 0.2}, {"id": "2", "x": 0.3, "y": 0.4}]
	parts = build_mark_all_points_parts(
		pts,
		power=140,
		duration_ms=36,
		spiral=False,
		trigger_selection="None",
		repetitions=3,
		rep_delay_ms=14,
	)
	assert parts.count("2") == 3
	assert _set_triggers(parts) == ["None", "None", "None"]
	i0 = parts.index("None")
	assert parts[i0 + 1 : i0 + 3] == ["14", "2"]
	assert parts[-1] == "None"


def test_repetitions_one_is_unchanged():
	pts = [{"id": "1", "x": 0.1, "y": 0.2}]
	a = build_mark_all_points_parts(pts, power=1, spiral=False)
	b = build_mark_all_points_parts(pts, power=1, spiral=False, repetitions=1)
	assert a == b
	assert a[-2:] == ["Trig In 1", "1"]


def test_packed_bnc_groups_one_set_each_with_group_delay():
	g0 = [{"id": "1", "x": 0.1, "y": 0.2}]
	g1 = [{"id": "2", "x": 0.5, "y": 0.6}]
	parts = pack_mark_all_points(
		[g0, g1],
		power=1,
		spiral=False,
		trigger_selection="Trig In 1",
		delay_ms=500,
		repetitions=2,
		rep_delay_ms=14,
	)
	# BNC: one set per group (reps collapsed); Delay only between groups.
	assert _set_triggers(parts) == ["Trig In 1", "Trig In 1"]
	assert "500" in parts
	assert "14" not in parts
	assert parts[-2:] == ["Trig In 1", "1"]


def test_stim_params_carry_repetitions_and_burn_time():
	from prairie_live.mark_all_points import slm_burn_ms

	p = stim_params_from_meta(
		{"repetitions": "5", "inter_point_delay": "14", "duration": 36}
	)
	assert p["repetitions"] == 5
	assert p["rep_delay_ms"] == 14.0
	assert slm_burn_ms(p) == 5 * 36 + 4 * 14
	q = stim_params_from_meta({})
	assert q["repetitions"] == 1
	assert slm_burn_ms(q) == q["duration_ms"]
