"""Unit tests for readable trial record helpers."""

from __future__ import annotations

import json
from pathlib import Path

from prairie_live.trial_record import (
	best_trial_recommendation,
	format_recommendation,
	format_trial_summary,
	order_trial_row,
	write_session_recommendation,
	write_trial_sidecars,
)


def test_summary_and_ordered_row_drop_bulky():
	row = {
		"trial_index": 4,
		"trigger_index": 4,
		"n_triggers": 5,
		"point_ids": ["1", "4", "8"],
		"power": 140.0,
		"score": 0.0096,
		"score_kind": "relay_disk_dff",
		"stim_mode": "slm",
		"slm_parts": ["-MarkAllPoints", "3"],
		"group_trigger_map": [{"trigger_index": 0}],
	}
	s = format_trial_summary(row)
	assert "trial 4" in s
	assert "points [1,4,8]" in s
	assert "0.0096" in s
	ordered = order_trial_row({**row, "phase": "done", "summary": s})
	assert list(ordered.keys())[0] == "phase"
	assert ordered["summary"] == s
	assert "slm_parts" not in ordered
	assert "group_trigger_map" not in ordered


def test_write_trial_sidecars(tmp_path: Path):
	row = {
		"phase": "done",
		"trial_index": 0,
		"point_ids": ["1", "2", "3"],
		"power": 140,
		"score": 0.1,
		"score_kind": "mock",
		"stim_mode": "slm",
		"group_name": "PL_t0000_g1",
		"trigger": "none",
		"trigger_selection": "PFI1",
	}
	row["summary"] = format_trial_summary(row)
	paths = write_trial_sidecars(row, tmp_path / "t0000")
	assert Path(paths["trial_json"]).is_file()
	assert Path(paths["readable_txt"]).is_file()
	data = json.loads(Path(paths["trial_json"]).read_text(encoding="utf-8"))
	assert data["point_ids"] == ["1", "2", "3"]
	txt = Path(paths["readable_txt"]).read_text(encoding="utf-8")
	assert "points:" in txt and "1, 2, 3" in txt


def test_format_log_table(tmp_path: Path):
	from prairie_live.trial_record import format_log_table, main

	path = tmp_path / "trials.jsonl"
	rows = [
		{"phase": "armed", "trial_index": 0, "point_ids": ["1"]},
		{
			"phase": "done",
			"trial_index": 0,
			"trigger_index": 0,
			"n_triggers": 2,
			"point_ids": ["1", "4", "8"],
			"power": 140,
			"score": 0.0096,
			"score_kind": "relay_disk_dff",
			"stim_mode": "slm",
			"summary": "trial 0 · points [1,4,8]",
		},
	]
	path.write_text(
		"\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
	)
	table = format_log_table(rows, phase="done")
	assert "1,4,8" in table
	assert "0.0096" in table
	main([str(path)])


def test_jsonl_writes_readable_txt(tmp_path: Path):
	from prairie_live.sync_loop import JsonlLog

	log_path = tmp_path / "trials.jsonl"
	log = JsonlLog(log_path)
	try:
		log.write(
			{
				"phase": "slm_packed",
				"power": 140,
				"n_triggers": 1,
				"summary": "packed",
				"group_trigger_map": [
					{
						"trigger_index": 0,
						"group_name": "g1",
						"point_ids": ["1", "2", "3"],
					}
				],
			}
		)
		log.write(
			{
				"phase": "done",
				"trial_index": 0,
				"group_name": "g1",
				"point_ids": ["1", "2", "3"],
				"power": 140,
				"score": 0.1,
				"score_kind": "mock",
				"stim_mode": "slm",
				"trigger": "none",
				"trigger_selection": "PFI1",
				"summary": "trial 0 · points [1,2,3]",
			}
		)
	finally:
		log.close()
	txt = log.txt_path.read_text(encoding="utf-8")
	assert "TRIAL 0" in txt
	assert "Fired points:  1, 2, 3" in txt
	assert "PACKED SLM" in txt
	assert log_path.is_file()


def test_best_trial_recommendation_picks_highest_score():
	trials = [
		{"trial_index": 0, "point_ids": ["1", "2"], "power": 100, "score": 0.02},
		{"trial_index": 1, "point_ids": ["3", "4", "5"], "power": 140, "score": 0.05},
		{"trial_index": 2, "point_ids": ["6"], "power": 140, "score": None},
	]
	rec = best_trial_recommendation(trials)
	assert rec is not None
	assert rec["point_ids"] == ["3", "4", "5"]
	assert rec["power"] == 140
	assert rec["score"] == 0.05
	assert rec["trial_index"] == 1


def test_write_session_recommendation(tmp_path: Path):
	trials = [
		{
			"phase": "done",
			"trial_index": 0,
			"point_ids": ["1"],
			"power": 100,
			"score": 0.01,
			"score_kind": "mock",
			"point_dff": {"1": -0.01},
		},
		{
			"phase": "done",
			"trial_index": 1,
			"point_ids": ["4", "8"],
			"power": 140,
			"score": 0.09,
			"score_kind": "mock",
			"point_dff": {"4": 0.09, "8": 0.07},
			"image_paths": {"trial_dir": str(tmp_path / "t0001_p140")},
		},
	]
	run_root = tmp_path / "run_test"
	pool = [
		{"id": "1", "name": "Point 1", "x": 0.1, "y": 0.1, "z": 0, "is_spiral": "True"},
		{"id": "4", "name": "Point 4", "x": 0.4, "y": 0.4, "z": 0, "is_spiral": "True"},
		{"id": "8", "name": "Point 8", "x": 0.8, "y": 0.8, "z": 0, "is_spiral": "True"},
	]
	meta = {"trigger_selection": "PFI1", "duration": 100.0}
	rec = write_session_recommendation(
		trials,
		run_root=run_root,
		point_pool=pool,
		meta=meta,
		min_dff=0.0,
	)
	assert rec is not None
	assert "4, 8" in format_recommendation(rec)
	assert (run_root / "recommendation.txt").is_file()
	assert (run_root / "recommended_MarkPoints.xml").is_file()
