"""Planning + board overlay helpers for the mapping GUI (no display)."""

from __future__ import annotations

import random

from prairie_live.candidate_view import (
	default_picked_ids,
	save_status,
	selected_rows,
	table_cells,
	toggle_picked,
)
from prairie_live.candidates import rank_candidate_points, write_candidates_xml
from prairie_live.gui_session import (
	GROUP_SIZE,
	apply_gui_overrides,
	group_sizes_for_pool,
	n_groups_for_pool,
	parse_powers_field,
	plan_group_power_trials,
	planned_trial_count,
	trial_log_line,
)
from prairie_live.markpoints import extract_unique_points, load_mark_points_file
from prairie_live.power_view import PowerBoards, board_state, mark_style, unique_powers
from prairie_live.sync_loop import form_groups


def test_eight_points_uses_every_point_once():
	pts = extract_unique_points(load_mark_points_file("samples/MarkPoints_8fov.xml"))
	assert len(pts) == 8
	assert n_groups_for_pool(8) == 2
	assert group_sizes_for_pool(8) == [4, 4]
	assert group_sizes_for_pool(7) == [4, 3]
	assert group_sizes_for_pool(9) == [3, 3, 3]
	assert group_sizes_for_pool(5) == [5]
	powers = [75.0, 140.0, 200.0]
	assert planned_trial_count(8, powers) == 6
	groups = form_groups(
		pts,
		n_groups=2,
		group_size=GROUP_SIZE,
		rng=random.Random(0),
		cover_all=True,
	)
	assert [len(g) for g in groups] == [4, 4]
	assert all(len(g) >= 3 for g in groups)
	ids = [str(p["id"]) for g in groups for p in g]
	assert len(ids) == len(set(ids)) == 8
	assert set(ids) == {str(p["id"]) for p in pts}
	plan = plan_group_power_trials(groups, powers)
	assert len(plan) == 6
	# Full FOV per power, then next power.
	assert [c["power"] for c in plan] == [75.0, 75.0, 140.0, 140.0, 200.0, 200.0]
	assert [c["group_index"] for c in plan] == [0, 1, 0, 1, 0, 1]
	for pwr in powers:
		assert {c["group_index"] for c in plan if c["power"] == pwr} == {0, 1}
	from prairie_live.gui_session import point_power_pairs

	want = {(str(p["id"]), pwr) for p in pts for pwr in powers}
	assert point_power_pairs(groups, powers) == want


def test_fifteen_points_every_point_every_power():
	pts = [{"id": str(i), "x": 0.1 * i, "y": 0.1} for i in range(1, 16)]
	powers = [40.0, 80.0, 160.0]
	assert n_groups_for_pool(15) == 5
	assert planned_trial_count(15, powers) == 15
	groups = form_groups(
		pts,
		n_groups=5,
		group_size=GROUP_SIZE,
		rng=random.Random(1),
		cover_all=True,
	)
	assert [len(g) for g in groups] == [3, 3, 3, 3, 3]
	from prairie_live.gui_session import point_power_pairs

	want = {(str(p["id"]), pwr) for p in pts for pwr in powers}
	assert point_power_pairs(groups, powers) == want
	assert len(want) == 15 * 3


def test_parse_powers_dedupes_preserve_order():
	assert parse_powers_field("75, 140, 75, 200") == [75.0, 140.0, 200.0]


def test_gui_overrides_cover_pool():
	cfg = {"via_relay": "10.0.0.1:25100", "iterations": 1}
	opt = apply_gui_overrides(
		cfg,
		series="samples/MarkPoints_8fov.xml",
		powers=[80.0, 140.0],
		seed=3,
		repetitions=2,
		stim_dff_min=0.05,
		n_points=8,
	)
	assert opt["group_size"] == 3
	assert opt["n_groups"] == 2
	assert opt["cover_all"] is True
	assert opt["slm_pack"] is False
	assert opt["powers"] == [80.0, 140.0]
	assert opt["repetitions"] == 2
	assert opt["via_relay"] == "10.0.0.1:25100"


def test_board_highlights_changed_only_over_threshold():
	pts = [
		{"id": "1", "x": 0.1, "y": 0.1},
		{"id": "2", "x": 0.2, "y": 0.2},
		{"id": "3", "x": 0.3, "y": 0.3},
		{"id": "4", "x": 0.4, "y": 0.4},
	]
	row = {
		"trial_index": 1,
		"power": 140,
		"point_ids": ["1", "2", "3"],
		"point_scores": {"1": 0.20, "2": 0.01, "3": 0.40},
	}
	st = board_state(row, pts, stim_dff_min=0.1, n_trials=6)
	by_id = {m["id"]: m for m in st["marks"]}
	assert by_id["1"]["fired"] and by_id["1"]["changed"]
	assert by_id["2"]["fired"] and not by_id["2"]["changed"]
	assert by_id["3"]["changed"]
	assert not by_id["4"]["fired"] and not by_id["4"]["changed"]
	assert mark_style(by_id["1"])["kind"] == "changed"
	assert mark_style(by_id["2"])["kind"] == "fired"
	assert mark_style(by_id["4"])["kind"] == "pool"
	assert "140" in st["title"]
	assert "1,2,3" in st["title"]
	assert "trial 1/6" in st["title"]


def test_power_boards_update_matching_window_only():
	pts = [{"id": "1", "x": 0.1, "y": 0.2}, {"id": "2", "x": 0.3, "y": 0.4}]
	boards = PowerBoards([75, 140], pts, stim_dff_min=0.0, n_trials=2, show=False)
	assert unique_powers([75, 140, 75]) == [75.0, 140.0]
	boards.update(
		{
			"trial_index": 1,
			"power": 75,
			"point_ids": ["1"],
			"point_scores": {"1": 0.3},
		}
	)
	assert 75.0 in boards.states
	assert 140.0 not in boards.states
	boards.update(
		{
			"trial_index": 2,
			"power": 140,
			"point_ids": ["2"],
			"point_scores": {"2": 0.1},
		}
	)
	assert boards.states[75.0]["fired_ids"] == ["1"]
	assert boards.states[140.0]["fired_ids"] == ["2"]


def test_candidate_table_and_save(tmp_path):
	pts = [
		{"id": "a", "x": 0.2, "y": 0.2},
		{"id": "b", "x": 0.5, "y": 0.5},
		{"id": "c", "x": 0.8, "y": 0.8},
	]
	trials = [
		{"point_scores": {"a": 0.4, "b": 0.2}, "power": 80},
		{"point_scores": {"c": -0.01}, "power": 80},
	]
	ranked = rank_candidate_points(trials, pts, stim_dff_min=0.0)
	picked = default_picked_ids(ranked)
	assert picked  # top tercile of stimulated
	cells = table_cells(ranked)
	assert cells[0]["id"] == ranked[0]["id"]
	assert "min_power" in cells[0]
	picked = toggle_picked(picked, ranked[-1]["id"])
	sel = selected_rows(ranked, picked)
	assert {r["id"] for r in sel} == picked
	path = write_candidates_xml(sel, {"duration": 10.0}, tmp_path / "next_series.xml")
	text = path.read_text(encoding="utf-8")
	assert "PVMarkPointSeriesElements" in text
	for r in sel:
		assert f'Index="{r["id"]}"' in text
	assert "next_series.xml" in save_status(len(sel), path)


def test_trial_log_line_uses_summary():
	row = {
		"summary": "trial 0 · points [1,2,3] · power 140",
		"trial_index": 0,
		"point_ids": ["1"],
		"power": 140,
	}
	assert "power 140" in trial_log_line(row)
