import numpy as np

from prairie_live.candidates import (
	TERCILE_RGB,
	rank_candidate_points,
	write_candidates_xml,
	write_session_candidates,
)
from prairie_live.markpoints import extract_unique_points, load_mark_points_file


def test_rank_prefers_low_power_then_high_dff():
	pts = [
		{"id": "a", "x": 0.2, "y": 0.2},
		{"id": "b", "x": 0.5, "y": 0.5},
		{"id": "c", "x": 0.8, "y": 0.8},
		{"id": "d", "x": 0.3, "y": 0.3},
	]
	trials = [
		{"point_scores": {"a": 0.10, "b": 0.40}, "power": 140},
		{"point_scores": {"a": 0.12}, "power": 80},
		# Burns often darken — negative ΔF/F still counts as a change.
		{"point_scores": {"c": -0.25}, "power": 100},
		{"point_scores": {"d": 0.0}, "power": 100},
	]
	ranked = rank_candidate_points(trials, pts, stim_dff_min=0.0)
	ids = [r["id"] for r in ranked]
	assert ids[0] == "a"
	assert ranked[0]["min_power"] == 80
	assert ranked[0]["dff"] == 0.12
	assert {r["id"] for r in ranked if r["stimulated"]} == {"a", "b", "c"}
	assert next(r for r in ranked if r["id"] == "c")["dff"] == -0.25
	assert next(r for r in ranked if r["id"] == "d")["stimulated"] is False
	assert ranked[0]["tercile"] in TERCILE_RGB


def test_burn_darkening_is_a_candidate():
	pts = [{"id": "7", "x": 0.1, "y": 0.1}]
	trials = [{"point_scores": {"7": -0.4}, "power": 100}]
	ranked = rank_candidate_points(trials, pts, stim_dff_min=0.0)
	assert ranked[0]["stimulated"] is True
	assert ranked[0]["min_power"] == 100
	assert ranked[0]["tercile"] == "top"


def test_same_power_higher_dff_wins():
	pts = [{"id": "1", "x": 0.1, "y": 0.1}, {"id": "2", "x": 0.2, "y": 0.2}]
	trials = [
		{"point_scores": {"1": 0.2, "2": 0.5}, "power": 100},
	]
	ranked = rank_candidate_points(trials, pts, stim_dff_min=0.0)
	assert [r["id"] for r in ranked] == ["2", "1"]


def test_write_candidates_and_top_xml(tmp_path):
	pts = extract_unique_points(load_mark_points_file("samples/MarkPoints_8fov.xml"))
	trials = [
		{"point_scores": {str(p["id"]): 0.2 for p in pts[:3]}, "power": 80},
		{"point_scores": {str(p["id"]): 0.05 for p in pts[3:6]}, "power": 140},
	]
	meta = {"duration": 10.0, "repetitions": 2}
	ranked = write_session_candidates(
		trials, pts, meta=meta, stim_dff_min=0.0, out_dir=tmp_path
	)
	assert (tmp_path / "candidates.json").is_file()
	assert (tmp_path / "candidates.txt").is_file()
	assert (tmp_path / "candidates_top.xml").is_file()
	assert any(r["tercile"] == "top" for r in ranked)
	xml = write_candidates_xml(ranked[:3], meta, tmp_path / "next.xml")
	assert xml.is_file()
	assert "PVMarkPointSeriesElements" in xml.read_text(encoding="utf-8")


def test_tercile_color_helper():
	assert TERCILE_RGB["top"] != TERCILE_RGB["mid"]
	from prairie_live.candidate_view import _label, _nearest

	row = {"id": "3", "stimulated": True, "min_power": 80, "dff": 0.2, "x": 0.0, "y": 0.0}
	assert "p=80" in _label(row)
	assert _nearest([row], (32, 32), 0.0, 0.0) == "3"
	assert _nearest([row], (32, 32), 30.0, 30.0) is None
