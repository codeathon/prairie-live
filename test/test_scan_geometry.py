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


def test_rig_scan_puts_all_picked_points_in_frame():
	# PV 6.1 on the rig at zoom 1: amplitude 4.2035577285, center 0 on both axes.
	rig = {
		("currentScanAmplitude", "XAxis"): "4.2035577285",
		("currentScanAmplitude", "YAxis"): "4.2035577285",
		("currentScanCenter", "XAxis"): "0",
		("currentScanCenter", "YAxis"): "0",
	}
	pts = extract_unique_points(load_mark_points_file("samples/MarkPoints_rig_saved.xml"))
	out = overlay_points(pts, fetch_scan_geometry(FakeRelay(rig)))
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
	# A geometry-only note with no points used to shrink the live FOV on load.
	assert overlay_points([], {"amp_x": 4.2, "amp_y": 4.2, "ctr_x": 0.0, "ctr_y": 0.0}) == {
		"points": [],
		"note": "",
	}


def test_fraction_points_used_as_is():
	pts = extract_unique_points(load_mark_points_file("samples/MarkPoints_8fov.xml"))
	out = overlay_points(pts, None)
	assert len(out["points"]) == 8
	assert out["points"][0]["fx"] == pytest.approx(pts[0]["x"], abs=1e-4)


def test_live_feed_stops_polling_after_dropped_session():
	from prairie_live.live_feed import LiveFeed

	class Dropped:
		def get_state(self, key, index=None):
			return {"ok": False, "error": "PrairieView dropped the connection during -gts (reset)"}

	feed = LiveFeed("127.0.0.1:25100")
	assert feed._poll_geometry(Dropped()) is None
	assert "polling stopped" in feed.scan_geometry_error()
	assert feed._poll_geometry(FakeRelay(GEOM)) == 2.0
	assert feed.scan_geometry_error() == ""


def test_points_for_image_maps_galvo_for_scoring():
	from prairie_live.scan_geometry import points_for_image

	rig = {"amp_x": 4.2035577285, "amp_y": 4.2035577285, "ctr_x": 0.0, "ctr_y": 0.0}
	# Point 7 from the user's series.
	pts = [{"id": "7", "x": 0.70059295475, "y": -2.02004301952917}]
	out = points_for_image(pts, rig)
	assert out[0]["id"] == "7"
	assert 0 < out[0]["x"] < 1 and 0 < out[0]["y"] < 1
	assert out[0]["x_galvo"] == pts[0]["x"]
	# Original list is unchanged (firing still uses galvo units).
	assert pts[0]["x"] == 0.70059295475


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
	# Without mapping, galvo coords are skipped.
	assert score_points_dff([f0], [f1], pts, radius=2) == {}
	got = score_points_dff([f0], [f1], mapped, radius=2)
	assert "7" in got and got["7"] > 0.5
