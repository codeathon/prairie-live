"""Unit tests for -MarkAllPoints (-slm) argument builder."""

from __future__ import annotations

import pytest

from prairie_live.mark_all_points import (
	build_mark_all_points_parts,
	pack_mark_all_points,
	parts_to_com_command,
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
		trigger_selection="PFI1",
	)
	assert parts[0] == "-MarkAllPoints"
	assert parts[1] == "2"
	assert parts[2] == "False"
	assert parts[3:7] == ["0.1", "0.2", "0.3", "0.4"]
	assert parts[7] == "16.92"
	assert parts[8] == "Monaco"
	assert parts[9] == "0.75"
	assert parts[10:13] == ["True", "0.1", "8"]
	assert parts[13] == "PFI1"
	cmd = parts_to_com_command(parts)
	assert cmd.startswith("-MarkAllPoints 2 False")
	assert "Monaco" in cmd


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
		trigger_selection="PFI1",
		delay_ms=150.0,
	)
	assert parts[0] == "-MarkAllPoints"
	# First set ends with PFI1 then Delay; second set ends with PFI1 (no Delay).
	assert parts.count("PFI1") == 2
	assert "150" in parts
	# Delay sits between the two Trigger tokens.
	i0 = parts.index("PFI1")
	assert parts[i0 + 1] == "150"
	assert parts[-1] == "PFI1"


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


def test_stim_params_from_meta():
	meta = {
		"uncaging_laser": "Monaco",
		"duration": 16.92,
		"use_3d": "False",
		"is_spiral": "True",
		"spiral_size_um": "54.5",
		"spiral_revolutions": "8",
		"fov_width_um": 500.0,
		"trigger_selection": "PFI1",
		"initial_delay_ms": 22.1,
	}
	p = stim_params_from_meta(meta)
	assert p["laser"] == "Monaco"
	assert p["spiral"] is True
	assert p["use_3d"] is False
	assert p["fov_width_um"] == 500.0
	assert p["initial_delay_ms"] == 22.1
