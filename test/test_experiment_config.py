"""Tests for experiment.json loading / CLI merge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prairie_live.experiment_config import (
	apply_stim_to_meta,
	load_experiment,
	merge_cli_over_config,
	powers_to_list,
)


def test_load_and_powers(tmp_path: Path):
	path = tmp_path / "experiment.json"
	path.write_text(
		json.dumps(
			{
				"series": "X.xml",
				"powers": [0, 0.75],
				"stim_mode": "slm",
				"laser": "Monaco",
				"_comment": "ignored",
			}
		),
		encoding="utf-8",
	)
	cfg = load_experiment(path)
	assert "_comment" not in cfg
	assert cfg["laser"] == "Monaco"
	assert powers_to_list(cfg["powers"]) == [0.0, 0.75]
	assert powers_to_list("0.5,1") == [0.5, 1.0]


def test_merge_cli_overrides_config():
	class NS:
		series = "cli.xml"
		scope_xml = None
		via_relay = None
		relay = None
		log = None
		host = None
		port = None
		password = None
		iterations = 9
		n_groups = None
		group_size = None
		powers = None
		seed = None
		trigger = None
		serial = None
		ttl_width = None
		inter_trial = None
		pad_ms = None
		elite_frac = None
		mock_scores = False
		f0_s = None
		f1_s = None
		frame_poll = None
		disk_radius = None
		dry_run = False
		inspect = False
		slm_pack = None

	cfg = {
		"series": "cfg.xml",
		"iterations": 1,
		"n_groups": 2,
		"stim_mode": "slm",
		"laser": "Monaco",
		"trigger": "serial",
	}
	opt = merge_cli_over_config(NS(), cfg)
	assert opt["series"] == "cli.xml"
	assert opt["iterations"] == 9
	assert opt["n_groups"] == 2
	assert opt["stim_mode"] == "slm"
	assert opt["laser"] == "Monaco"
	assert opt["trigger"] == "serial"


def test_apply_stim_to_meta():
	meta = {
		"uncaging_laser": "Uncaging",
		"use_3d": "True",
		"spiral_revolutions": "5",
		"duration": 1.0,
	}
	out = apply_stim_to_meta(
		meta,
		{
			"laser": "Monaco",
			"use_3d": False,
			"spiral_revolutions": 8,
			"spiral_size_um": 54.5,
			"spiral": True,
			"duration_ms": 16.92,
			"initial_delay_ms": 22.1,
			"inter_point_delay_ms": 10.0,
			"trigger_selection": "PFI1",
			"stim_mode": "slm",
		},
	)
	assert out["uncaging_laser"] == "Monaco"
	assert out["use_3d"] == "False"
	assert out["spiral_revolutions"] == "8"
	assert out["spiral_size_um"] == "54.5"
	assert out["is_spiral"] == "True"
	assert out["duration"] == 16.92
	assert float(out["initial_delay"]) == pytest.approx(0.0221)
	assert float(out["inter_point_delay"]) == pytest.approx(0.01)
	assert out["trigger_selection"] == "PFI1"
	assert out["stim_mode"] == "slm"


def test_merge_slm_pack_cli_overrides_config():
	class NS:
		series = None
		scope_xml = None
		via_relay = None
		relay = None
		log = None
		host = None
		port = None
		password = None
		iterations = None
		n_groups = None
		group_size = None
		powers = None
		seed = None
		trigger = None
		serial = None
		ttl_width = None
		inter_trial = None
		pad_ms = None
		elite_frac = None
		mock_scores = False
		f0_s = None
		f1_s = None
		frame_poll = None
		disk_radius = None
		dry_run = False
		inspect = False
		slm_pack = False

	opt = merge_cli_over_config(NS(), {"slm_pack": True})
	assert opt["slm_pack"] is False
