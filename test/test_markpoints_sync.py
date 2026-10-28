"""Unit tests for Mark Points XML + sync-loop grouping (no PrairieView)."""

from __future__ import annotations

import json
import random
import time
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

	def mark_points(self, *, no_wait=None, timeout=None):
		self.calls.append(("mark", no_wait, timeout))
		# Blocking -mp must outlive series arm_delay (0.25s) or stim_fire errors.
		if not no_wait:
			time.sleep(0.35)
		return {"ok": True, "cmd": "mark_points", "no_wait": no_wait}

	def mark_all_points(self, parts, *, wait_ms=0.0, no_wait=None, timeout=None):
		self.calls.append(("slm", list(parts), wait_ms, no_wait, timeout))
		# Blocking -slm must outlive the DTR arm_delay or stim_fire errors.
		if not no_wait:
			time.sleep(0.35)
		return {"ok": True, "cmd": "mark_all_points", "parts": list(parts), "no_wait": no_wait}

	def start_live(self):
		self.calls.append(("live",))
		return {"ok": True, "cmd": "live"}

	def stop_live(self):
		self.calls.append(("live_off",))
		return {"ok": True, "cmd": "live_off"}

	def get_frame(self):
		return None

	def get_state(self, key, index=None):
		# Live scan window for SLM fire-time galvo→FOV remap (matches overlay).
		self.calls.append(("get_state", key, index))
		vals = {
			("currentScanAmplitude", "XAxis"): "4.2",
			("currentScanAmplitude", "YAxis"): "4.2",
			("currentScanCenter", "XAxis"): "0",
			("currentScanCenter", "YAxis"): "0",
		}
		return {"ok": True, "value": vals.get((key, index), "0")}


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
	assert relay.calls[1][0] == "mark"


def test_gpl_slm_dry_run_packed(tmp_path: Path):
	"""stim_mode=slm + slm_pack: one packed -MarkAllPoints (FOV 0–1 points)."""
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
			slm_pack=True,
		)
	finally:
		log.close()
	assert len(rows) == 2
	assert all(r["stim_mode"] == "slm" for r in rows)
	assert all(len(r["point_ids"]) == 3 for r in rows)
	assert rows[0]["trigger_index"] == 0
	assert rows[1]["trigger_index"] == 1
	packed = [
		json.loads(ln)
		for ln in log_path.read_text(encoding="utf-8").splitlines()
		if json.loads(ln).get("phase") == "slm_packed"
	]
	assert len(packed) == 1
	assert packed[0]["slm_parts"][0] == "-MarkAllPoints"


def test_slm_via_relay_fires_mark_all_points(tmp_path: Path):
	"""Classic SLM: -MarkAllPoints on the relay (no -LoadMarkPoints)."""
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
	assert rows[0]["stim_mode"] == "slm"
	slm_calls = [c for c in relay.calls if c[0] == "slm"]
	assert len(slm_calls) == 1
	assert slm_calls[0][1][0] == "-MarkAllPoints"
	assert not any(c[0] == "load" for c in relay.calls)
	assert any(c[0] == "live" for c in relay.calls)


class _FakeTtl:
	def __init__(self):
		self.pulses: list[float] = []
		self.dtr = False

	def set_dtr(self, on: bool) -> None:
		self.dtr = bool(on)

	def pulse_dtr(self, width_s: float):
		self.pulses.append(width_s)


def test_slm_serial_one_slm_and_one_dtr_per_group(tmp_path: Path):
	"""Serial SLM: group → arm -slm → one DTR → next group (never pack)."""
	steps = parse_mark_points(FAT_XML)
	pts = extract_unique_points(steps)
	meta = template_meta(steps)
	meta["fov_width_um"] = 545.0
	meta["spiral_size_um"] = "54.5"
	meta["is_spiral"] = "True"
	meta["initial_delay_ms"] = 0.0
	log = JsonlLog(tmp_path / "slm_serial.jsonl")
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
			# Short delays in unit tests; lab defaults are longer for mask gen.
			slm_arm_delay_s=0.05,
			slm_group_settle_s=0.05,
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
			slm_pack=True,  # ignored for serial
		)
	finally:
		log.close()
	assert len(rows) == 2
	slm_calls = [c for c in relay.calls if c[0] == "slm"]
	assert len(slm_calls) == 2
	assert all(c[1][0] == "-MarkAllPoints" for c in slm_calls)
	assert len(ttl.pulses) == 2
	assert [r["trigger_index"] for r in rows] == [0, 1]
	assert all(r["n_triggers"] == 2 for r in rows)
	assert all(r.get("slm_pack") is False for r in rows)
	assert "group 1/2" in rows[0]["summary"]
	assert "group 2/2" in rows[1]["summary"]
	# Live off before each -slm; Live on only after burn (no on→off bounce).
	assert sum(1 for c in relay.calls if c[0] == "live_off") >= 2
	assert sum(1 for c in relay.calls if c[0] == "live") >= 2
	assert not any(c[0] == "load" for c in relay.calls)
	# FOV 0–1 lists must not call -gts before -slm (that blocked real Runs).
	assert not any(
		isinstance(c, tuple) and c[0] == "get_state" for c in relay.calls
	)

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
	"""PV's own saved series: inline X/Y; XML Out ↔ GUI Display via ×400.

	Monaco: Out 0–2.5, Display max 1000 → Display = Out × (1000/2.5).
	"""
	import xml.etree.ElementTree as ET

	import pytest

	from prairie_live.markpoints import (
		MONACO_DISPLAY_MAX,
		MONACO_OUT_MAX,
		POWER_FULL_SCALE,
		load_mark_points_file,
		xml_power,
	)

	assert POWER_FULL_SCALE == pytest.approx(MONACO_DISPLAY_MAX / MONACO_OUT_MAX)
	steps = load_mark_points_file("samples/MarkPoints_rig_saved.xml")
	pts = extract_unique_points(steps)
	assert len(pts) == 5
	assert pts[0]["x"] == pytest.approx(-3.25489489483074)
	# XML 0.4 Out → Display 160; XML 0.625 → Display 250.
	assert steps[0]["laser_pwr"] == pytest.approx(160.0)
	assert steps[-1]["laser_pwr"] == pytest.approx(250.0)
	meta = template_meta(steps)
	assert meta["pv_saved_format"] is True
	assert meta["repetitions"] == 3 and meta["duration"] == 36.0
	assert xml_power(160, meta) == 0.4
	with pytest.raises(ValueError):
		xml_power(1500, meta)

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
	# One wait per group: first point only; later points free-run after that DTR.
	assert els[0].get("TriggerSelection") == "Trig In 1"
	assert els[0].get("TriggerFrequency") == "FirstRepetition"
	assert els[0].get("TriggerIdentifier") == DEFAULT_TRIGGER_IDENTIFIER
	assert els[0].get("TriggerIdentifier") != NO_TRIGGER_IDENTIFIER
	assert els[1].get("TriggerSelection") is None
	assert els[1].get("TriggerFrequency") == "None"
	assert els[1].get("TriggerIdentifier") == NO_TRIGGER_IDENTIFIER
	assert els[2].get("TriggerFrequency") == "None"
	assert els[2].get("TriggerIdentifier") == NO_TRIGGER_IDENTIFIER


def test_series_serial_one_dtr_per_group(tmp_path: Path):
	"""Serial series: one DTR per group trial, not one per point."""
	from prairie_live.lab_defaults import (
		DEFAULT_TRIGGER_FREQUENCY,
		DEFAULT_TRIGGER_IDENTIFIER,
		DEFAULT_TRIGGER_SELECTION,
	)
	from prairie_live.markpoints import load_mark_points_file

	steps = load_mark_points_file("samples/MarkPoints_25_trig_in1.xml")
	pts = extract_unique_points(steps)
	meta = template_meta(steps)
	meta["trigger_selection"] = DEFAULT_TRIGGER_SELECTION
	meta["trigger_frequency"] = DEFAULT_TRIGGER_FREQUENCY
	meta["trigger_identifier"] = DEFAULT_TRIGGER_IDENTIFIER
	log = JsonlLog(tmp_path / "series_serial.jsonl")
	relay = _FakeRelayMp()
	ttl = _FakeTtl()
	try:
		rows = run_sync_loop(
			points=pts[:6],
			meta=meta,
			pl=None,
			log=log,
			scope_xml=str(tmp_path / "trial.xml"),
			n_iterations=1,
			n_groups=2,
			group_size=3,
			powers=[160.0],
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
			stim_mode="series",
			cover_all=False,
		)
	finally:
		log.close()
	assert len(rows) == 2
	assert len(ttl.pulses) == 2
	assert all(r["n_triggers"] == 1 for r in rows)


def test_trigger_none_clears_file_trig_in_1_guid():
	"""Frequency=None + Trig In 1 GUID crashes PV spiral DAC on -LoadMarkPoints."""
	from prairie_live.lab_defaults import (
		DEFAULT_TRIGGER_IDENTIFIER,
		NO_TRIGGER_IDENTIFIER,
		trigger_identifier_for,
	)

	assert (
		trigger_identifier_for("None", DEFAULT_TRIGGER_IDENTIFIER)
		== NO_TRIGGER_IDENTIFIER
	)
	assert trigger_identifier_for("none", DEFAULT_TRIGGER_IDENTIFIER) == NO_TRIGGER_IDENTIFIER
	assert trigger_identifier_for("", DEFAULT_TRIGGER_IDENTIFIER) == NO_TRIGGER_IDENTIFIER
	assert (
		trigger_identifier_for("Trig In 1", DEFAULT_TRIGGER_IDENTIFIER)
		== DEFAULT_TRIGGER_IDENTIFIER
	)


def test_series_none_keeps_lab_25pt_file_spiral(tmp_path: Path):
	"""Lab 25-pt FirstRepetition file + trigger=none must not rewrite spiral timing."""
	import xml.etree.ElementTree as ET

	import pytest

	from prairie_live.experiment_config import apply_stim_to_meta
	from prairie_live.lab_defaults import NO_TRIGGER_IDENTIFIER, lab_defaults
	from prairie_live.markpoints import load_mark_points_file
	from prairie_live.sync_loop import execute_session

	steps = load_mark_points_file("samples/MarkPoints_25_trig_in1.xml")
	pts = extract_unique_points(steps)
	assert len(pts) == 25
	assert pts[0].get("spiral_revolutions") == "8"
	meta = apply_stim_to_meta(template_meta(steps), lab_defaults())
	assert meta["duration"] == 36.0
	assert meta["spiral_revolutions"] == "8"
	assert str(meta["inter_point_delay"]) == "14"

	scope = tmp_path / "trial.xml"
	opt = lab_defaults()
	opt.update(
		series="samples/MarkPoints_25_trig_in1.xml",
		powers=[160.0],
		seed=0,
		# Lab default reps=1 must not clobber the file's Repetitions=3.
		repetitions=1,
		stim_dff_min=0.0,
		n_groups=1,
		group_size=3,
		cover_all=False,
		stim_mode="series",
		trigger="none",
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
	els = root.findall("PVMarkPointElement")
	assert len(els) == 3
	assert {e.get("TriggerFrequency") for e in els} == {"None"}
	assert {e.get("TriggerIdentifier") for e in els} == {NO_TRIGGER_IDENTIFIER}
	assert all(e.get("TriggerSelection") is None for e in els)
	assert {e.get("Repetitions") for e in els} == {"3"}
	g0 = els[0].find("PVGalvoPointElement")
	assert float(g0.get("Duration")) == 36.0
	assert g0.get("SpiralRevolutions") == "8"
	assert g0.get("InterPointDelay") == "14"
	assert float(g0.get("SpiralSize")) == pytest.approx(0.184901809118501)
	# Native galvo X/Y (not FOV-remapped into 0–1).
	assert not (0.0 <= float(g0.get("X")) <= 1.0 and 0.0 <= float(g0.get("Y")) <= 1.0)
