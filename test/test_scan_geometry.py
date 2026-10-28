"""Mark Points → live-image overlay mapping."""

import pytest

from prairie_live.markpoints import extract_unique_points, load_mark_points_file
from prairie_live.scan_geometry import fetch_scan_geometry, overlay_points


class FakeRelay:
	def __init__(self, values):
		self.values = values

	def get_state(self, key, index=None):
		return {"ok": True, "value": self.values[(key, index)]}


GEOM = {
	("currentScanAmplitude", "XAxis"): "7.6",
	("currentScanAmplitude", "YAxis"): "-7.6",
	("currentScanCenter", "XAxis"): "0",
	("currentScanCenter", "YAxis"): "0.2",
}

ZOOM1 = {
	("currentScanAmplitude", "XAxis"): "4.2035577285",
	("currentScanAmplitude", "YAxis"): "4.2035577285",
	("currentScanCenter", "XAxis"): "0",
	("currentScanCenter", "YAxis"): "0",
}


def test_fetch_geometry_parses_pv_replies():
	g = fetch_scan_geometry(FakeRelay(GEOM))
	assert g == {"amp_x": 7.6, "amp_y": -7.6, "ctr_x": 0.0, "ctr_y": 0.2}


def test_fetch_geometry_says_which_read_failed():
	vals = dict(GEOM)
	vals[("currentScanAmplitude", "XAxis")] = ""
	with pytest.raises(ValueError, match="currentScanAmplitude XAxis: PV replied ''"):
		fetch_scan_geometry(FakeRelay(vals))


def test_fetch_geometry_reports_relay_error():
	class Down:
		def get_state(self, key, index=None):
			return {"ok": False, "error": "PrairieView script port not connected"}

	with pytest.raises(ValueError, match="not connected"):
		fetch_scan_geometry(Down())


def test_waiting_note_carries_reason():
	pts = extract_unique_points(load_mark_points_file("samples/MarkPoints_rig_saved.xml"))
	out = overlay_points(pts, None, "ValueError: currentScanAmplitude XAxis: PV replied ''")
	assert "PV replied" in out["note"]


def test_galvo_points_map_into_frame():
	# Half-width amplitude: frame spans center ± amplitude.
	pts = extract_unique_points(load_mark_points_file("samples/MarkPoints_rig_saved.xml"))
	g = fetch_scan_geometry(FakeRelay(GEOM))
	out = overlay_points(pts, g)
	p1 = out["points"][0]
	assert p1["fx"] == pytest.approx(0.5 + -3.25489489483074 / 15.2, abs=1e-4)
	# Negative Y amplitude: scan runs reversed, so negative galvo Y is low in the image.
	assert p1["fy"] == pytest.approx(0.5 + (-3.66925337390467 - 0.2) / -15.2, abs=1e-4)
	assert p1["r"] == pytest.approx(0.184901809118501 / 15.2 / 2, abs=1e-4)


def test_pv_pre_run_gpl_to_post_run_fov_formula():
	"""PV's before/after Mark Points exports are the scan-window linear map.

	Lab evidence: same 4 points saved as .gpl (galvo) then as fat series after
	a PV GUI run (FOV 0–1). Fit recovers amp≈2.10178, center≈0 on both axes.
	"""
	from prairie_live.scan_geometry import galvo_to_fraction

	# currentScanAmplitude half-width implied by the export pair.
	amp = 2.10177886425
	geom = {"amp_x": amp, "amp_y": amp, "ctr_x": 0.0, "ctr_y": 0.0}
	pairs = [
		((-0.583964350325029, -1.77653289698036), (0.361078546307151, 0.0773739742086753)),
		((0.87471453318728, -1.72232523576876), (0.708089097303634, 0.0902696365767878)),
		((-0.579036381123974, -0.347421828674384), (0.362250879249707, 0.417350527549824)),
		((0.795867025970399, -0.11087930702374), (0.689331770222743, 0.473622508792497)),
	]
	for (gx, gy), (fx, fy) in pairs:
		px, py = galvo_to_fraction(gx, gy, geom)
		assert px == pytest.approx(fx, abs=1e-9)
		assert py == pytest.approx(fy, abs=1e-9)
	# SpiralSize (galvo) → SpiralWidth (FOV fraction).
	spiral_galvo = 0.0197118768042204
	spiral_fov = 0.00468933177022279
	assert spiral_galvo / (2.0 * amp) == pytest.approx(spiral_fov, abs=1e-12)


def test_full_width_mapping_rejected_on_rig():
	"""Treating -gts amp as full width puts zoom-1 picked points off-image.

	Lab commit 6ee7ca2: amp 4.20356 is half-width (center ± amp). Using amp as
	full width mismatched PV's Mark Points GUI (points far from burns / PV marks).
	"""
	from prairie_live.scan_geometry import galvo_to_fraction

	pts = extract_unique_points(load_mark_points_file("samples/MarkPoints_rig_saved.xml"))
	amp = 4.2035577285
	half = {"amp_x": amp, "amp_y": amp, "ctr_x": 0.0, "ctr_y": 0.0}
	# Wrong: divide by amp once (full-width interpretation of the same -gts value).
	bad_in = 0
	good_in = 0
	for p in pts:
		gx, gy = float(p["x"]), float(p["y"])
		fx, fy = galvo_to_fraction(gx, gy, half)
		if 0 <= fx <= 1 and 0 <= fy <= 1:
			good_in += 1
		bfx, bfy = 0.5 + gx / amp, 0.5 + gy / amp
		if 0 <= bfx <= 1 and 0 <= bfy <= 1:
			bad_in += 1
	assert good_in == len(pts)
	assert bad_in < len(pts)


def test_fraction_galvo_roundtrip_tracks_live_fov():
	from prairie_live.scan_geometry import (
		fraction_to_galvo,
		galvo_to_fraction,
		prepare_points_for_live_fov,
		points_to_galvo_for_series,
	)

	g = fetch_scan_geometry(FakeRelay(GEOM))
	fx, fy = galvo_to_fraction(-3.25, 1.5, g)
	gx, gy = fraction_to_galvo(fx, fy, g)
	assert gx == pytest.approx(-3.25, abs=1e-6)
	assert gy == pytest.approx(1.5, abs=1e-6)

	pts = extract_unique_points(load_mark_points_file("samples/MarkPoints_rig_saved.xml"))
	fov_pts, note = prepare_points_for_live_fov(pts, FakeRelay(GEOM))
	assert "live FOV" in note
	assert all(p.get("coord_space") == "fov" for p in fov_pts)
	pan = dict(GEOM)
	pan[("currentScanCenter", "XAxis")] = "1.0"
	fired, fire_note = points_to_galvo_for_series(fov_pts, FakeRelay(pan))
	assert "FOV→galvo" in fire_note
	assert fired[0]["x"] != pytest.approx(float(pts[0]["x"]), abs=0.01)
	g2 = fetch_scan_geometry(FakeRelay(pan))
	rx, ry = galvo_to_fraction(float(fired[0]["x"]), float(fired[0]["y"]), g2)
	assert rx == pytest.approx(float(fov_pts[0]["x"]), abs=1e-5)
	assert ry == pytest.approx(float(fov_pts[0]["y"]), abs=1e-5)


def test_rig_scan_puts_all_picked_points_in_frame():
	# PV 6.1 on the rig at zoom 1: amplitude 4.2035577285, center 0 on both axes.
	pts = extract_unique_points(load_mark_points_file("samples/MarkPoints_rig_saved.xml"))
	out = overlay_points(pts, fetch_scan_geometry(FakeRelay(ZOOM1)))
	assert all(0 <= p["fx"] <= 1 and 0 <= p["fy"] <= 1 for p in out["points"])
	assert out["note"] == "PV scan X 0 ± 4.20356, Y 0 ± 4.20356"
	p1 = out["points"][0]
	assert round(p1["fx"] * 512) == 58 and round(p1["fy"] * 512) == 33


def test_points_outside_scan_are_counted():
	pts = [{"id": "1", "x": 5.0, "y": 0.0}, {"id": "2", "x": 0.0, "y": 0.0}]
	g = {"amp_x": 4.2, "amp_y": 4.2, "ctr_x": 0.0, "ctr_y": 0.0}
	assert "1 point(s) outside" in overlay_points(pts, g)["note"]


def test_galvo_points_wait_for_geometry():
	pts = extract_unique_points(load_mark_points_file("samples/MarkPoints_rig_saved.xml"))
	out = overlay_points(pts, None)
	assert out["points"] == [] and "galvo" in out["note"]


def test_empty_pool_has_no_note():
	assert overlay_points([], {"amp_x": 4.2, "amp_y": 4.2, "ctr_x": 0.0, "ctr_y": 0.0}) == {
		"points": [],
		"note": "",
	}


def test_fraction_points_used_as_is():
	pts = extract_unique_points(load_mark_points_file("samples/MarkPoints_8fov.xml"))
	out = overlay_points(pts, None)
	assert len(out["points"]) == 8
	assert out["points"][0]["fx"] == pytest.approx(pts[0]["x"], abs=1e-4)


def test_live_feed_keeps_geometry_after_dropped_session():
	from prairie_live.live_feed import LiveFeed

	class Dropped:
		def get_state(self, key, index=None):
			return {"ok": False, "error": "PrairieView dropped the connection during -gts (reset)"}

	feed = LiveFeed("127.0.0.1:25100")
	assert feed._poll_geometry(FakeRelay(GEOM)) == 2.0
	geom_ok, _ = feed.scan_geometry()
	assert geom_ok is not None
	wait = feed._poll_geometry(Dropped())
	assert wait == 30.0
	assert "paused" in feed.scan_geometry_error() or "last scan" in feed.scan_geometry_error()
	geom2, _ = feed.scan_geometry()
	assert geom2 == geom_ok
	assert feed._poll_geometry(FakeRelay(GEOM)) == 2.0
	assert feed.scan_geometry_error() == ""


def test_points_for_image_maps_galvo_for_scoring():
	from prairie_live.scan_geometry import points_for_image

	rig = {"amp_x": 4.2035577285, "amp_y": 4.2035577285, "ctr_x": 0.0, "ctr_y": 0.0}
	pts = [{"id": "7", "x": 0.70059295475, "y": -2.02004301952917}]
	out = points_for_image(pts, rig)
	assert out[0]["id"] == "7"
	assert 0 < out[0]["x"] < 1 and 0 < out[0]["y"] < 1
	assert out[0]["x_galvo"] == pts[0]["x"]
	assert pts[0]["x"] == 0.70059295475


def test_prepare_points_for_slm_maps_galvo_via_relay():
	from prairie_live.scan_geometry import prepare_points_for_slm

	class FakeRelay:
		def get_state(self, key, index):
			vals = {
				("currentScanAmplitude", "XAxis"): 4.2,
				("currentScanAmplitude", "YAxis"): 4.2,
				("currentScanCenter", "XAxis"): 0.0,
				("currentScanCenter", "YAxis"): 0.0,
			}
			return {"ok": True, "value": str(vals[(key, index)])}

	pts = [{"id": "7", "x": 0.7, "y": -2.0}]
	out, note = prepare_points_for_slm(pts, FakeRelay())
	assert "mapped" in note
	assert 0 < out[0]["x"] < 1 and 0 < out[0]["y"] < 1
	assert out[0]["x_galvo"] == 0.7
	fov = [{"id": "1", "x": 0.2, "y": 0.3}]
	got, note2 = prepare_points_for_slm(fov, None)
	assert "already" in note2
	assert got[0]["x"] == 0.2
	try:
		prepare_points_for_slm(pts, None)
		assert False, "expected ValueError without relay"
	except ValueError as exc:
		assert "relay" in str(exc).lower()


def test_points_for_slm_fire_remaps_galvo_with_live_amp():
	"""Burns must use the *current* scan amp (overlay), not a stale session lock."""
	from prairie_live.scan_geometry import (
		galvo_to_fraction,
		points_for_slm_fire,
		prepare_points_for_live_fov,
	)

	stale = {
		("currentScanAmplitude", "XAxis"): "6.0",
		("currentScanAmplitude", "YAxis"): "6.0",
		("currentScanCenter", "XAxis"): "0",
		("currentScanCenter", "YAxis"): "0",
	}
	live = dict(ZOOM1)
	raw = extract_unique_points(load_mark_points_file("samples/MarkPoints_25_trig_in1.xml"))
	locked, _ = prepare_points_for_live_fov(raw, FakeRelay(stale))
	p11 = next(p for p in locked if str(p["id"]) == "11")
	stale_fx = float(p11["x"])
	live_fx, live_fy = galvo_to_fraction(
		float(p11["x_galvo"]), float(p11["y_galvo"]), fetch_scan_geometry(FakeRelay(live))
	)
	assert stale_fx != pytest.approx(live_fx, abs=1e-3)

	fire, spiral, note = points_for_slm_fire(
		[p11], FakeRelay(live), spiral_size_um=54.5, fov_width_um=1136.7
	)
	assert "SLM fire" in note
	assert float(fire[0]["x"]) == pytest.approx(live_fx, abs=1e-5)
	assert float(fire[0]["y"]) == pytest.approx(live_fy, abs=1e-5)
	# Lab µm spiral wins (matches working FOV SpiralWidth ≈ 0.048).
	assert spiral == pytest.approx(54.5 / 1136.7, abs=1e-5)
	assert "54.5" in note and "µm" in note


def test_points_for_slm_fire_rejects_oob():
	from prairie_live.scan_geometry import points_for_slm_fire

	tiny = {
		("currentScanAmplitude", "XAxis"): "1.0",
		("currentScanAmplitude", "YAxis"): "1.0",
		("currentScanCenter", "XAxis"): "0",
		("currentScanCenter", "YAxis"): "0",
	}
	pts = [{"id": "1", "x": -3.0, "y": -3.0, "x_galvo": -3.0, "y_galvo": -3.0}]
	with pytest.raises(ValueError, match="outside live FOV"):
		points_for_slm_fire(pts, FakeRelay(tiny))


def test_slm_fire_converts_pvsaved_spiral_size_not_as_fov_frac():
	"""Never-run PVSaved SpiralSize ~0.185 V must not go to -slm as FOV 0.185."""
	from prairie_live.scan_geometry import points_for_slm_fire

	raw = extract_unique_points(load_mark_points_file("samples/MarkPoints_rig_saved.xml"))
	assert raw[0].get("spiral_space") == "galvo"
	# No µm override → galvo SpiralSize / (2·|amp|).
	fire, spiral, note = points_for_slm_fire([raw[0]], FakeRelay(ZOOM1))
	assert "spiral_fov" in note
	correct = 0.184901809118501 / (2 * 4.2035577285)
	assert spiral == pytest.approx(correct, abs=1e-5)
	assert spiral != pytest.approx(0.184901809118501, abs=1e-3)
	assert fire[0].get("spiral_space") == "galvo"


def test_score_galvo_points_after_mapping():
	import numpy as np

	from prairie_live.scan_geometry import points_for_image
	from prairie_live.sync_loop import score_points_dff

	rig = {"amp_x": 4.2, "amp_y": 4.2, "ctr_x": 0.0, "ctr_y": 0.0}
	pts = [{"id": "7", "x": 0.7, "y": -2.0}]
	mapped = points_for_image(pts, rig)
	h = w = 64
	f0 = np.ones((h, w), dtype=np.float64)
	f1 = np.ones((h, w), dtype=np.float64)
	cx = int(round(mapped[0]["x"] * (w - 1)))
	cy = int(round(mapped[0]["y"] * (h - 1)))
	f1[max(cy - 2, 0) : cy + 3, max(cx - 2, 0) : cx + 3] = 4.0
	assert score_points_dff([f0], [f1], pts, radius=2) == {}
	got = score_points_dff([f0], [f1], mapped, radius=2)
	assert "7" in got and got["7"] > 0.5
