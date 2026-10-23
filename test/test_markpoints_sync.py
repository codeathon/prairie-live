"""Unit tests for Mark Points XML + sync-loop grouping (no PrairieView)."""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np

from prairie_live.markpoints import (
	build_group_step,
	estimate_series_ms,
	extract_unique_points,
	groups_to_xml,
	parse_galvo_point_list,
	parse_mark_points,
	template_meta,
)
from prairie_live.sync_loop import (
	JsonlLog,
	aggregate_point_scores,
	disk_mean,
	form_groups,
	run_sync_loop,
	score_group_dff,
	score_points_dff,
)

FAT_XML = """<?xml version="1.1"?>
<PVMarkPointSeriesElements Use3D="True" AllPointsAtOnce="True" Iterations="1" IterationDelay="0.00">
  <PVMarkPointElement Repetitions="1" UncagingLaser="Uncaging" UncagingLaserPower="0.75"
    TriggerSelection="Trig In 1" TriggerFrequency="None" TriggerCount="1"
    AsyncSyncFrequency="None" VoltageOutputCategoryName="None"
    VoltageRecCategoryName="None" parameterSet="CurrentSettings">
    <PVGalvoPointElement InitialDelay="0.00" InterPointDelay="0.01" Duration="10"
      SpiralRevolutions="5" Points="Group 1" Indices="1-4">
      <Point Index="1" X="0.10" Y="0.20" Z="0" IsSpiral="True" SpiralWidth="0.2" SpiralHeight="0.2" SpiralSizeInMicrons="20"/>
      <Point Index="2" X="0.30" Y="0.40" Z="0" IsSpiral="True" SpiralWidth="0.2" SpiralHeight="0.2" SpiralSizeInMicrons="20"/>
      <Point Index="3" X="0.50" Y="0.60" Z="0" IsSpiral="True" SpiralWidth="0.2" SpiralHeight="0.2" SpiralSizeInMicrons="20"/>
      <Point Index="4" X="0.70" Y="0.80" Z="0" IsSpiral="True" SpiralWidth="0.2" SpiralHeight="0.2" SpiralSizeInMicrons="20"/>
    </PVGalvoPointElement>
  </PVMarkPointElement>
</PVMarkPointSeriesElements>
"""

GPL_XML = """<?xml version="1.0"?>
<PVGalvoPointList>
  <Point Index="1" X="0.11" Y="0.21" Z="0" IsSpiral="True" SpiralSizeInMicrons="54.5"/>
  <Point Index="2" X="0.31" Y="0.41" Z="0" IsSpiral="True" SpiralSizeInMicrons="54.5"/>
  <Point Index="3" X="0.51" Y="0.61" Z="0" IsSpiral="True" SpiralSizeInMicrons="54.5"/>
  <Point Index="4" X="0.71" Y="0.81" Z="0" IsSpiral="True" SpiralSizeInMicrons="54.5"/>
  <Point Index="5" X="0.15" Y="0.25" Z="0"/>
</PVGalvoPointList>
"""


def test_lab_fallback_defaults():
	# When series omits fields, use lab UI defaults (Monaco / 2D / 54.5 µm / 8).
	meta = template_meta([])
	assert meta["uncaging_laser"] == "Monaco"
	assert meta["use_3d"] == "False"
	assert meta["spiral_revolutions"] == "8"
	pts = [{"id": "1", "x": 0.1, "y": 0.2}]
	for k, v in (
		("is_spiral", "True"),
		("spiral_size_um", "54.5"),
	):
		pts[0].setdefault(k, v)
	step = build_group_step(pts, name="G", power=0.75, meta=meta)
	assert step["uncaging_laser"] == "Monaco"
	assert step["use_3d"] == "False"
	assert step["spiral_revolutions"] == "8"
	assert pts[0]["spiral_size_um"] == "54.5"


def test_parse_and_extract_points():
	steps = parse_mark_points(FAT_XML)
	assert len(steps) == 1
	pts = extract_unique_points(steps)
	assert [p["id"] for p in pts] == ["1", "2", "3", "4"]
	assert pts[0]["x"] == 0.10


def test_parse_gpl_point_list():
	pts = parse_galvo_point_list(GPL_XML)
	assert len(pts) == 5
	assert pts[0]["x"] == 0.11
	steps = parse_mark_points(GPL_XML)
	pool = extract_unique_points(steps)
	assert len(pool) == 5
	assert pool[2]["id"] == "3"


def test_load_truncated_gpl_raises_clear_error(tmp_path: Path):
	from prairie_live.markpoints import load_mark_points_file

	path = tmp_path / "broken.gpl"
	# Cut mid-file: common after a bad copy/export.
	path.write_text(GPL_XML.rsplit("</PVGalvoPoint", 1)[0], encoding="utf-8")
	try:
		load_mark_points_file(str(path))
		assert False, "expected ValueError"
	except ValueError as e:
		msg = str(e)
		assert "invalid XML" in msg
		assert "truncated" in msg or "unclosed" in msg.lower()
		assert "broken.gpl" in msg


def test_roundtrip_xml_keeps_coords():
	steps = parse_mark_points(FAT_XML)
	pts = extract_unique_points(steps)
	meta = template_meta(steps)
	step = build_group_step(pts[:2], name="Group A", power=0.5, meta=meta)
	xml = groups_to_xml([step])
	again = parse_mark_points(xml)
	assert len(again) == 1
	assert again[0]["laser_pwr"] == 0.5
	assert len(again[0]["points"]) == 2
	assert again[0]["points"][0]["x"] == 0.10


def test_form_groups_random_and_scored():
	pts = [{"id": str(i), "x": i / 10, "y": i / 10} for i in range(1, 9)]
	rng = random.Random(1)
	g0 = form_groups(pts, n_groups=2, group_size=3, scores=None, rng=rng)
	assert len(g0) == 2 and all(len(g) == 3 for g in g0)

	scores = {str(i): float(i) for i in range(1, 9)}
	rng = random.Random(2)
	g1 = form_groups(
		pts, n_groups=2, group_size=3, scores=scores, rng=rng, elite_frac=0.5
	)
	# Highest ids should appear among group members when elite_frac is high.
	ids = {p["id"] for g in g1 for p in g}
	assert "8" in ids and "7" in ids


def test_aggregate_and_dff():
	trials = [
		{"point_ids": ["1", "2"], "score": 1.0},
		{"point_ids": ["1"], "score": 0.0},
	]
	agg = aggregate_point_scores(trials)
	assert agg["1"] == 0.5
	assert agg["2"] == 1.0

	f0 = np.ones((20, 20), dtype=np.float32) * 10
	f1 = np.ones((20, 20), dtype=np.float32) * 10
	# Brighten near normalized (0.5, 0.5).
	f1[8:12, 8:12] = 20
	pts = [{"id": "1", "x": 0.5, "y": 0.5}]
	s = score_group_dff([f0], [f1], pts, radius=2)
	assert s > 0.4
	per = score_points_dff([f0], [f1], pts, radius=2)
	assert "1" in per and per["1"] == s
	assert disk_mean(f1, 0.5, 0.5, 2) > 10


def test_disk_mean_oob_returns_none():
	frame = np.zeros((32, 32), dtype=np.float32)
	assert disk_mean(frame, 0.5, 0.5, 2) == 0.0
	# Prairie allows galvo coords outside the visible FOV.
	assert disk_mean(frame, -2.9, 0.5, 2) is None
	assert disk_mean(frame, 0.5, 3.0, 2) is None
	assert score_group_dff([frame], [frame], [{"x": -2.9, "y": 0.5}]) == 0.0


def test_dry_run_writes_jsonl(tmp_path: Path):
	steps = parse_mark_points(FAT_XML)
	pts = extract_unique_points(steps)
	meta = template_meta(steps)
	log_path = tmp_path / "trials.jsonl"
	scope = str(tmp_path / "trial.xml")
	log = JsonlLog(log_path)
	try:
		rows = run_sync_loop(
			points=pts,
			meta=meta,
			pl=None,
			log=log,
			scope_xml=scope,
			n_iterations=2,
			n_groups=2,
			group_size=3,
			powers=[0.0, 0.75],
			seed=7,
			trigger="none",
			ttl=None,
			ttl_width_s=0.01,
			inter_trial_s=0.0,
			pad_ms=0.0,
			mock_scores=True,
			relay=None,
			via_relay=False,
			f0_s=0.0,
			f1_s=0.0,
			frame_poll_s=0.01,
			disk_radius=3,
			elite_frac=0.5,
			dry_run=True,
			show_candidate_gui=False,
		)
	finally:
		log.close()

	# 2 iterations × 2 groups × 2 powers
	assert len(rows) == 8
	assert Path(scope).is_file()
	lines = log_path.read_text(encoding="utf-8").strip().splitlines()
	# armed + done per trial + recommendation + candidates
	assert len(lines) == 18
	assert json.loads(lines[-1])["phase"] == "candidates"
	assert json.loads(lines[-2])["phase"] == "recommendation"
	done = [json.loads(ln) for ln in lines if json.loads(ln).get("phase") == "done"]
	assert done[0]["trial_index"] == 1
	assert done[0]["score_kind"] == "mock"
	assert done[0].get("point_scores")
	assert estimate_series_ms(
		[build_group_step(pts[:2], name="G", power=0.5, meta=meta)]
	) > 0


class _FakeRelayMp:
	def __init__(self):
		self.calls = []

	def load_mark_points(self, xml, path=None):
		self.calls.append(("load", xml[:40], path))
		return {"ok": True, "cmd": "load_mark_points", "path": "x.xml"}

	def mark_points(self):
		self.calls.append("mark")
		return {"ok": True, "cmd": "mark_points"}

	def mark_all_points(self, parts, *, wait_ms=0.0):
		self.calls.append(("slm", list(parts), wait_ms))
		return {"ok": True, "cmd": "mark_all_points", "parts": list(parts)}

	def get_frame(self):
		return None


def test_via_relay_fires_without_scope_xml(tmp_path: Path):
	steps = parse_mark_points(FAT_XML)
	pts = extract_unique_points(steps)
	meta = template_meta(steps)
	log = JsonlLog(tmp_path / "t.jsonl")
	relay = _FakeRelayMp()
	try:
		rows = run_sync_loop(
			points=pts,
			meta=meta,
			pl=None,
			log=log,
			scope_xml=None,
			n_iterations=1,
			n_groups=1,
			group_size=3,
			powers=[0.75],
			seed=1,
			trigger="none",
			ttl=None,
			ttl_width_s=0.01,
			inter_trial_s=0.0,
			pad_ms=0.0,
			mock_scores=True,
			relay=relay,
			via_relay=True,
			f0_s=0.0,
			f1_s=0.0,
			frame_poll_s=0.01,
			disk_radius=3,
			elite_frac=0.5,
			dry_run=False,
		)
	finally:
		log.close()
	assert len(rows) == 1
	assert relay.calls[0][0] == "load"
	assert relay.calls[1] == "mark"


def test_gpl_slm_dry_run(tmp_path: Path):
	steps = parse_mark_points(GPL_XML)
	pts = extract_unique_points(steps)
	meta = template_meta(steps)
	meta["fov_width_um"] = 1136.7
	meta["spiral_size_um"] = "54.5"
	meta["is_spiral"] = "True"
	meta["initial_delay_ms"] = 22.1
	log_path = tmp_path / "gpl_slm.jsonl"
	log = JsonlLog(log_path)
	scope = tmp_path / "trial.xml"
	try:
		rows = run_sync_loop(
			points=pts,
			meta=meta,
			pl=None,
			log=log,
			scope_xml=str(scope),
			n_iterations=1,
			n_groups=2,
			group_size=3,
			powers=[0.75],
			seed=1,
			trigger="none",
			ttl=None,
			ttl_width_s=0.01,
			inter_trial_s=0.0,
			pad_ms=0.0,
			mock_scores=True,
			relay=None,
			via_relay=False,
			f0_s=0.0,
			f1_s=0.0,
			frame_poll_s=0.01,
			disk_radius=3,
			elite_frac=0.5,
			dry_run=True,
			stim_mode="slm",
		)
	finally:
		log.close()
	assert len(rows) == 2
	assert all(r["stim_mode"] == "slm" for r in rows)
	assert all(len(r["point_ids"]) == 3 for r in rows)
	assert "summary" in rows[0]
	assert "slm_parts" not in rows[0]
	assert "group_trigger_map" not in rows[0]
	assert rows[0]["trigger_index"] == 0
	assert rows[1]["trigger_index"] == 1
	packed = [
		json.loads(ln)
		for ln in log_path.read_text(encoding="utf-8").splitlines()
		if json.loads(ln).get("phase") == "slm_packed"
	]
	assert len(packed) == 1
	assert packed[0]["n_triggers"] == 2
	assert packed[0]["slm_parts"][0] == "-MarkAllPoints"
	assert packed[0]["group_trigger_map"][1]["point_ids"] == rows[1]["point_ids"]
	assert scope.is_file()
	assert "PVSavedMarkPointSeriesElements" in scope.read_text(encoding="utf-8")


def test_slm_via_relay_fires_mark_all_points(tmp_path: Path):
	steps = parse_mark_points(FAT_XML)
	pts = extract_unique_points(steps)
	meta = template_meta(steps)
	meta["fov_width_um"] = 545.0
	meta["spiral_size_um"] = "54.5"
	meta["is_spiral"] = "True"
	meta["initial_delay_ms"] = 22.1
	log = JsonlLog(tmp_path / "slm_relay.jsonl")
	relay = _FakeRelayMp()
	try:
		rows = run_sync_loop(
			points=pts,
			meta=meta,
			pl=None,
			log=log,
			scope_xml=None,
			n_iterations=1,
			n_groups=1,
			group_size=3,
			powers=[0.75],
			seed=1,
			trigger="none",
			ttl=None,
			ttl_width_s=0.01,
			inter_trial_s=0.0,
			pad_ms=0.0,
			mock_scores=True,
			relay=relay,
			via_relay=True,
			f0_s=0.0,
			f1_s=0.0,
			frame_poll_s=0.01,
			disk_radius=3,
			elite_frac=0.5,
			dry_run=False,
			stim_mode="slm",
		)
	finally:
		log.close()
	assert len(rows) == 1
	assert len(relay.calls) == 1
	kind, parts, wait_ms = relay.calls[0]
	assert kind == "slm"
	assert parts[0] == "-MarkAllPoints"
	assert wait_ms == 22.1
	# No series load/mark on SLM path.
	assert all(c[0] != "load" for c in relay.calls if isinstance(c, tuple))


class _FakeTtl:
	def __init__(self):
		self.pulses: list[float] = []

	def pulse_dtr(self, width_s: float):
		self.pulses.append(width_s)


def test_slm_packed_maps_trigger_index_to_group(tmp_path: Path):
	steps = parse_mark_points(FAT_XML)
	pts = extract_unique_points(steps)
	meta = template_meta(steps)
	meta["fov_width_um"] = 545.0
	meta["spiral_size_um"] = "54.5"
	meta["is_spiral"] = "True"
	meta["initial_delay_ms"] = 0.0
	log = JsonlLog(tmp_path / "slm_pack.jsonl")
	relay = _FakeRelayMp()
	ttl = _FakeTtl()
	try:
		rows = run_sync_loop(
			points=pts,
			meta=meta,
			pl=None,
			log=log,
			scope_xml=None,
			n_iterations=1,
			n_groups=2,
			group_size=3,
			powers=[0.75],
			seed=1,
			trigger="serial",
			ttl=ttl,
			ttl_width_s=0.01,
			inter_trial_s=0.0,
			pad_ms=0.0,
			mock_scores=True,
			relay=relay,
			via_relay=True,
			f0_s=0.0,
			f1_s=0.0,
			frame_poll_s=0.01,
			disk_radius=3,
			elite_frac=0.5,
			dry_run=False,
			stim_mode="slm",
		)
	finally:
		log.close()
	# One packed -slm; two DTR pulses in group order.
	assert len(relay.calls) == 1
	assert relay.calls[0][0] == "slm"
	assert len(ttl.pulses) == 2
	assert [r["trigger_index"] for r in rows] == [0, 1]
	assert "summary" in rows[0]
	packed = [
		json.loads(ln)
		for ln in (tmp_path / "slm_pack.jsonl").read_text(encoding="utf-8").splitlines()
		if json.loads(ln).get("phase") == "slm_packed"
	]
	assert packed[0]["group_trigger_map"][0]["point_ids"] == rows[0]["point_ids"]
	assert packed[0]["group_trigger_map"][1]["point_ids"] == rows[1]["point_ids"]
	# Packed string contains two Trig In 1 tokens (one per set) + Delay between.
	assert relay.calls[0][1].count("Trig In 1") == 2


def test_slm_single_fires_one_mark_all_points_per_group(tmp_path: Path):
	steps = parse_mark_points(FAT_XML)
	pts = extract_unique_points(steps)
	meta = template_meta(steps)
	meta["fov_width_um"] = 545.0
	meta["spiral_size_um"] = "54.5"
	meta["is_spiral"] = "True"
	meta["initial_delay_ms"] = 0.0
	log_path = tmp_path / "slm_single.jsonl"
	log = JsonlLog(log_path)
	relay = _FakeRelayMp()
	ttl = _FakeTtl()
	try:
		rows = run_sync_loop(
			points=pts,
			meta=meta,
			pl=None,
			log=log,
			scope_xml=None,
			n_iterations=1,
			n_groups=2,
			group_size=3,
			powers=[0.75],
			seed=1,
			trigger="serial",
			ttl=ttl,
			ttl_width_s=0.01,
			inter_trial_s=0.0,
			pad_ms=0.0,
			mock_scores=True,
			relay=relay,
			via_relay=True,
			f0_s=0.0,
			f1_s=0.0,
			frame_poll_s=0.01,
			disk_radius=3,
			elite_frac=0.5,
			dry_run=False,
			stim_mode="slm",
			slm_pack=False,
		)
	finally:
		log.close()
	# Two groups → two separate -slm commands; two DTR pulses.
	assert len(rows) == 2
	assert len(relay.calls) == 2
	assert all(c[0] == "slm" for c in relay.calls)
	assert all(c[1][0] == "-MarkAllPoints" for c in relay.calls)
	assert len(ttl.pulses) == 2
	assert all(r["trigger_index"] == 0 for r in rows)
	assert all(r["n_triggers"] == 1 for r in rows)
	assert all(r.get("slm_pack") is False for r in rows)
	singles = [
		json.loads(ln)
		for ln in log_path.read_text(encoding="utf-8").splitlines()
		if json.loads(ln).get("phase") == "slm_single"
	]
	assert len(singles) == 2
	assert singles[0]["n_triggers"] == 1
	assert singles[0]["group_trigger_map"][0]["point_ids"] == rows[0]["point_ids"]
	packed = [
		json.loads(ln)
		for ln in log_path.read_text(encoding="utf-8").splitlines()
		if json.loads(ln).get("phase") == "slm_packed"
	]
	assert packed == []


RIG_GPL = """<?xml version="1.0" encoding="utf-8"?>
<PVGalvoPointList>
  <PVGalvoPoint X="-3.25" Y="-3.67" Name="Point 1" Index="0" UncagingLaser="Monaco" UncagingLaserPower="0" Duration="100" IsSpiral="True" SpiralSize="0.1849" SpiralRevolutions="5" Z="3595.85" />
  <PVGalvoPoint X="-2.33" Y="-3.60" Name="Point 2" Index="1" UncagingLaser="Monaco" UncagingLaserPower="0" Duration="100" IsSpiral="True" SpiralSize="0.1849" SpiralRevolutions="5" Z="3595.85" />
  <PVGalvoPoint X="-1.41" Y="-3.67" Name="Point 3" Index="2" UncagingLaser="Monaco" UncagingLaserPower="0" Duration="100" IsSpiral="True" SpiralSize="0.1849" SpiralRevolutions="5" Z="3595.85" />
</PVGalvoPointList>
"""


def test_gpl_group_fires_like_pv(tmp_path: Path):
	"""A .gpl group must keep its dwell/spiral and fire one point at a time."""
	import xml.etree.ElementTree as ET

	from prairie_live.gui_session import apply_gui_overrides
	from prairie_live.sync_loop import execute_session
	from prairie_live.xml_summary import summarize_fat_xml

	gpl = tmp_path / "rig.gpl"
	gpl.write_text(RIG_GPL, encoding="utf-8")
	opt = apply_gui_overrides(
		series=str(gpl),
		powers=[160.0],
		seed=0,
		repetitions=1,
		stim_dff_min=0.0,
		n_points=3,
		stim_mode="series",
		trigger="none",
		xml_fields=summarize_fat_xml(str(gpl)),
	)
	scope = tmp_path / "trial.xml"
	opt.update(
		dry_run=True,
		scope_xml=str(scope),
		via_relay=None,
		mock_scores=True,
		f0_s=0,
		f1_s=0,
		inter_trial=0,
		log=str(tmp_path / "t.jsonl"),
		images_dir=str(tmp_path / "img"),
	)
	execute_session(opt, show_candidate_gui=False)
	root = ET.fromstring(scope.read_text(encoding="utf-8"))
	assert root.tag == "PVSavedMarkPointSeriesElements"
	steps = root.findall("PVMarkPointElement")
	assert len(steps) == 3
	assert {s.get("UncagingLaserPower") for s in steps} == {"0.4"}
	galvo = steps[0].find("PVGalvoPointElement")
	assert float(galvo.get("Duration")) == 100.0
	assert galvo.get("SpiralRevolutions") == "5"
	assert galvo.get("InterPointDelay") == "10"
	assert galvo.get("SpiralSize") == "0.1849"
	assert galvo.get("X") is not None and galvo.find("Point") is None


def test_pv_saved_series_reads_and_writes_like_pv(tmp_path: Path):
	"""PV's own saved series: inline X/Y, power as a fraction of 400."""
	import xml.etree.ElementTree as ET

	import pytest

	from prairie_live.markpoints import load_mark_points_file, xml_power

	steps = load_mark_points_file("samples/MarkPoints_rig_saved.xml")
	pts = extract_unique_points(steps)
	assert len(pts) == 5
	assert pts[0]["x"] == pytest.approx(-3.25489489483074)
	assert steps[0]["laser_pwr"] == pytest.approx(160.0)
	assert steps[-1]["laser_pwr"] == pytest.approx(250.0)
	meta = template_meta(steps)
	assert meta["pv_saved_format"] is True
	assert meta["repetitions"] == 3 and meta["duration"] == 36.0
	assert xml_power(160, meta) == 0.4
	with pytest.raises(ValueError):
		xml_power(500, meta)

	step = build_group_step(pts[:3], name="g", power=160.0, meta=meta)
	root = ET.fromstring(groups_to_xml([step]))
	assert root.tag == "PVSavedMarkPointSeriesElements"
	els = root.findall("PVMarkPointElement")
	assert [e.get("UncagingLaserPower") for e in els] == ["0.4"] * 3
	delays = [e.find("PVGalvoPointElement").get("InitialDelay") for e in els]
	assert delays == ["22.1", "2", "2"]
	g = els[0].find("PVGalvoPointElement")
	assert (g.get("Duration"), g.get("SpiralRevolutions"), g.get("InterPointDelay")) == ("36.0", "8", "14")
	assert els[0].get("Repetitions") == "3"


def test_pv_saved_serial_writes_trig_in_1_guid_not_null():
	"""Null TriggerIdentifier makes PV show Trigger Selection = None."""
	import xml.etree.ElementTree as ET

	from prairie_live.lab_defaults import (
		DEFAULT_TRIGGER_FREQUENCY,
		DEFAULT_TRIGGER_IDENTIFIER,
		DEFAULT_TRIGGER_SELECTION,
		NO_TRIGGER_IDENTIFIER,
	)
	from prairie_live.markpoints import load_mark_points_file

	steps = load_mark_points_file("samples/MarkPoints_rig_saved.xml")
	pts = extract_unique_points(steps)
	meta = template_meta(steps)
	meta["trigger_selection"] = DEFAULT_TRIGGER_SELECTION
	meta["trigger_frequency"] = DEFAULT_TRIGGER_FREQUENCY
	meta["trigger_identifier"] = DEFAULT_TRIGGER_IDENTIFIER
	step = build_group_step(
		pts[:3],
		name="g",
		power=160.0,
		meta=meta,
		trigger_selection=DEFAULT_TRIGGER_SELECTION,
	)
	root = ET.fromstring(groups_to_xml([step]))
	els = root.findall("PVMarkPointElement")
	assert els[0].get("TriggerSelection") == "Trig In 1"
	assert els[0].get("TriggerFrequency") == "FirstRepetition"
	assert els[0].get("TriggerIdentifier") == DEFAULT_TRIGGER_IDENTIFIER
	assert els[0].get("TriggerIdentifier") != NO_TRIGGER_IDENTIFIER
	# Match PV-saved XML: every point waits on Trig In 1 / FirstRepetition.
	assert els[1].get("TriggerSelection") == "Trig In 1"
	assert els[1].get("TriggerFrequency") == "FirstRepetition"
	assert els[1].get("TriggerIdentifier") == DEFAULT_TRIGGER_IDENTIFIER
	assert els[2].get("TriggerIdentifier") == DEFAULT_TRIGGER_IDENTIFIER
