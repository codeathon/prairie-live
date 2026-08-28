"""Tests for recommended MarkPoints.xml export."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from prairie_live.markpoints import template_meta
from prairie_live.recommended_series import (
	aggregate_point_dff,
	build_recommended_series,
	significant_point_hits,
	trial_point_hits,
	write_recommended_series,
)
from prairie_live.sync_loop import score_point_dffs


def _pool():
	return [
		{
			"id": "1",
			"name": "Point 1",
			"x": 0.2,
			"y": 0.3,
			"z": 0.0,
			"is_spiral": "True",
			"spiral_size_um": "54.5",
		},
		{
			"id": "2",
			"name": "Point 2",
			"x": 0.5,
			"y": 0.5,
			"z": 0.0,
			"is_spiral": "True",
			"spiral_size_um": "54.5",
		},
		{
			"id": "3",
			"name": "Point 3",
			"x": 0.8,
			"y": 0.7,
			"z": 0.0,
			"is_spiral": "True",
			"spiral_size_um": "54.5",
		},
	]


def test_score_point_dffs_per_disk():
	f0 = [np.full((32, 32), 100.0, dtype=np.float32)]
	f1 = [np.full((32, 32), 100.0, dtype=np.float32)]
	# Brighten disk at point 1 only.
	cx = int(round(0.2 * 31))
	cy = int(round(0.3 * 31))
	f1[0][cy - 1 : cy + 2, cx - 1 : cx + 2] = 150.0
	pts = [_pool()[0], _pool()[1]]
	out = score_point_dffs(f0, f1, pts, radius=3)
	assert "1" in out
	assert out["1"] > 0.1
	assert "2" not in out or out["2"] < 0.05


def test_significant_point_hits_includes_every_trial():
	trials = [
		{
			"phase": "done",
			"trial_index": 0,
			"power": 140.0,
			"point_dff": {"1": 0.05, "2": -0.01},
		},
		{
			"phase": "done",
			"trial_index": 1,
			"power": 100.0,
			"point_dff": {"1": 0.02, "3": 0.08},
		},
	]
	hits = significant_point_hits(trials, min_dff=0.0)
	assert len(hits) == 3
	assert [h["point_id"] for h in hits] == ["1", "3", "1"]
	assert hits[0]["power"] == 140.0
	assert hits[1]["power"] == 100.0
	assert hits[2]["power"] == 100.0
	assert hits[0]["trial_index"] == 0
	assert hits[1]["trial_index"] == 1
	assert hits[2]["trial_index"] == 1
	assert aggregate_point_dff(trials)["1"]["dff"] == 0.05
	assert aggregate_point_dff(trials)["1"]["power"] == 140.0


def test_trial_point_hits_legacy_score_fallback():
	trials = [
		{
			"phase": "done",
			"trial_index": 0,
			"power": 120.0,
			"score": 0.03,
			"point_ids": ["1", "2"],
		},
	]
	hits = trial_point_hits(trials, min_dff=0.0)
	assert len(hits) == 2
	assert {h["point_id"] for h in hits} == {"1", "2"}


def test_write_recommended_series_xml(tmp_path: Path):
	trials = [
		{
			"phase": "done",
			"trial_index": 0,
			"power": 140.0,
			"point_dff": {"1": 0.04, "2": 0.0},
		},
		{
			"phase": "done",
			"trial_index": 1,
			"power": 120.0,
			"point_dff": {"3": 0.09},
		},
	]
	meta = template_meta([])
	meta["trigger_selection"] = "PFI1"
	meta["duration"] = 100.0
	run_root = tmp_path / "run_test"
	out = write_recommended_series(
		trials,
		point_pool=_pool(),
		meta=meta,
		run_root=run_root,
		min_dff=0.0,
	)
	assert out is not None
	assert out["n_elements"] == 2
	assert out["n_points"] == 2
	assert out["point_ids"] == ["1", "3"]
	xml_path = run_root / "recommended_MarkPoints.xml"
	assert xml_path.is_file()
	root = ET.fromstring(xml_path.read_text(encoding="utf-8"))
	els = list(root)
	assert len(els) == 2
	powers = {el.attrib["UncagingLaserPower"] for el in els}
	assert powers == {"140.0", "120.0"}
	steps = build_recommended_series(
		_pool(),
		significant_point_hits(trials, min_dff=0.0),
		meta,
		trigger_selection="PFI1",
	)
	assert len(steps) == 2
	assert steps[0]["points"][0]["id"] == "1"
	assert steps[0]["name"] == "Point 1 trial 0"
	assert steps[1]["points"][0]["id"] == "3"


def test_write_recommended_series_same_point_multiple_trials(tmp_path: Path):
	trials = [
		{
			"phase": "done",
			"trial_index": 0,
			"power": 140.0,
			"point_dff": {"1": 0.04},
		},
		{
			"phase": "done",
			"trial_index": 2,
			"power": 100.0,
			"point_dff": {"1": 0.06},
		},
	]
	out = write_recommended_series(
		trials,
		point_pool=_pool(),
		meta=template_meta([]),
		run_root=tmp_path / "run",
		min_dff=0.0,
	)
	assert out is not None
	assert out["n_elements"] == 2
	assert out["n_points"] == 1
	assert out["point_ids"] == ["1", "1"]
	assert out["entries"][0]["power"] == 140.0
	assert out["entries"][1]["power"] == 100.0
