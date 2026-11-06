"""LiveFeed baseline / %ΔF/F encode without a relay."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from prairie_live.live_feed import DISPLAY_BIN, LiveFeed, bin_mean, gaussian_blur


def _frame(val: float, h: int = 32, w: int = 32) -> np.ndarray:
	return np.full((h, w), val, dtype=np.float64)


def _png_wh(png: bytes) -> tuple[int, int]:
	assert png[:8] == b"\x89PNG\r\n\x1a\n"
	# IHDR: length(4) + type(4) + width/height
	w, h = struct.unpack(">II", png[16:24])
	return int(w), int(h)


def test_bin_mean_factor_four():
	img = np.arange(64, dtype=np.float64).reshape(8, 8)
	out = bin_mean(img, 4)
	assert out.shape == (2, 2)
	assert float(out[0, 0]) == pytest.approx(float(img[:4, :4].mean()))
	assert float(out[1, 1]) == pytest.approx(float(img[4:, 4:].mean()))


def test_encode_png_is_display_binned():
	feed = LiveFeed(baseline_n=2)
	feed.ingest_frame(_frame(10.0, h=32, w=32))
	feed.ingest_frame(_frame(20.0, h=32, w=32))
	png = feed.png_bytes()
	assert png
	w, h = _png_wh(png)
	assert (w, h) == (32 // DISPLAY_BIN, 32 // DISPLAY_BIN)
	# Full-res geometry / ROI buffer unchanged.
	assert feed.frame_wh == (32, 32)
	with feed._lock:
		assert feed._current is not None
		assert feed._current.shape == (32, 32)


def test_baseline_mean_after_n_frames():
	feed = LiveFeed(baseline_n=4)
	for v in (10.0, 20.0, 30.0, 40.0):
		feed.ingest_frame(_frame(v))
	snap = feed.snapshot()
	assert snap["baseline_ready"] is True
	assert snap["collecting_baseline"] is False
	assert snap["mode"] == "dff"
	assert snap["baseline_n"] == 4
	with feed._lock:
		assert feed._baseline is not None
		assert float(feed._baseline.mean()) == pytest.approx(25.0)


def test_request_baseline_recollects():
	feed = LiveFeed(baseline_n=3)
	for _ in range(3):
		feed.ingest_frame(_frame(50.0))
	assert feed.snapshot()["baseline_ready"]
	feed.request_baseline()
	snap = feed.snapshot()
	assert snap["baseline_ready"] is False
	assert snap["collecting_baseline"] is True
	assert snap["mode"] == "raw"
	feed.ingest_frame(_frame(1.0))
	feed.ingest_frame(_frame(1.0))
	feed.ingest_frame(_frame(1.0))
	assert feed.snapshot()["baseline_ready"] is True
	assert feed.snapshot()["mode"] == "dff"


def test_dff_mode_png_differs_from_raw():
	feed = LiveFeed(baseline_n=2)
	feed.ingest_frame(_frame(100.0))
	# Brighten after baseline so ΔF/F PNG differs from flat raw stretch.
	bright = _frame(100.0)
	bright[10:20, 10:20] = 200.0
	feed.ingest_frame(bright)
	assert feed.snapshot()["mode"] == "dff"
	dff_png = feed.png_bytes()
	feed.set_mode("raw")
	raw_png = feed.png_bytes()
	assert dff_png and raw_png
	assert dff_png != raw_png


def test_roi_dff_pct_positive_at_center():
	feed = LiveFeed(baseline_n=2)
	base = _frame(100.0)
	feed.ingest_frame(base)
	feed.ingest_frame(base.copy())
	cur = base.copy()
	cur[14:19, 14:19] = 200.0
	feed.ingest_frame(cur)
	pts = [{"id": "1", "fx": 0.5, "fy": 0.5}]
	out = feed.roi_dff_pct(pts, radius=2)
	assert "1" in out
	assert out["1"] > 0


def test_gaussian_blur_preserves_shape():
	img = np.random.default_rng(0).random((16, 16))
	out = gaussian_blur(img, 1.0)
	assert out.shape == img.shape
	assert gaussian_blur(img, 0.0).shape == img.shape


def test_smooth_med_rolling_mean_differs_from_last_frame():
	"""Med averages recent frames so the PNG is not the last frame alone."""
	feed = LiveFeed(baseline_n=200)  # stay in raw (no auto ΔF/F)
	dark = np.zeros((32, 32), dtype=np.float64)
	bright = np.zeros((32, 32), dtype=np.float64)
	bright[8:24, 8:24] = 200.0

	feed.set_smooth("off")
	feed.ingest_frame(dark)
	feed.ingest_frame(bright)
	off_png = feed.png_bytes()
	assert off_png
	assert feed.snapshot()["smooth"] == "off"

	feed.set_smooth("med")
	assert feed.snapshot()["smooth_frames"] == 8
	for _ in range(4):
		feed.ingest_frame(dark)
	for _ in range(4):
		feed.ingest_frame(bright)
	med_png = feed.png_bytes()
	assert med_png
	# Off ends on full-bright square; med mean is a dimmer square → different PNG.
	assert med_png != off_png


def test_set_smooth_rejects_unknown():
	feed = LiveFeed(baseline_n=2)
	with pytest.raises(ValueError, match="off|low|med|high"):
		feed.set_smooth("ultra")


def test_set_scale_clim_changes_raw_png():
	"""Fixed white-point must differ from auto percentile stretch."""
	feed = LiveFeed(baseline_n=200)
	feed.set_smooth("off")
	img = np.zeros((32, 32), dtype=np.float64)
	img[8:24, 8:24] = 50.0
	feed.ingest_frame(img)
	auto_png = feed.png_bytes()
	assert auto_png
	assert feed.snapshot()["scale_source"] == "auto"

	feed.set_scale(lo=0, hi=500)
	snap = feed.snapshot()
	assert snap["scale_source"] == "manual"
	assert snap["clim_hi"] == 500.0
	fixed_png = feed.png_bytes()
	assert fixed_png
	assert fixed_png != auto_png

	feed.set_scale(auto=True)
	assert feed.snapshot()["scale_source"] == "auto"
	assert feed.snapshot()["clim_hi"] is None


def test_set_scale_dff_lim_in_snapshot():
	feed = LiveFeed(baseline_n=2)
	for _ in range(2):
		feed.ingest_frame(_frame(10.0))
	assert feed.snapshot()["baseline_ready"]
	feed.set_mode("dff")
	feed.set_scale(dff_lim=0.25)
	snap = feed.snapshot()
	assert snap["dff_lim"] == pytest.approx(0.25)
	assert snap["scale_source"] == "manual"


class _FakeStateClient:
	"""Minimal get_state stub for PV clim probe."""

	def __init__(self, values: dict[str, float]):
		self.values = values

	def get_state(self, key, index=None, subindex=None):
		k = f"{key}:{index}" if index is not None else key
		if k in self.values:
			return {"ok": True, "value": self.values[k]}
		if key in self.values and index is None:
			return {"ok": True, "value": self.values[key]}
		return {"ok": False, "value": None}


def test_pull_pv_scale_hits_display_max():
	feed = LiveFeed(baseline_n=200)
	feed.set_smooth("off")
	feed.ingest_frame(_frame(40.0))
	client = _FakeStateClient({"displayMax:1": 120.0, "displayMin:1": 10.0})
	snap = feed.pull_pv_scale_now(client)
	assert snap["pv_scale_ok"] is True
	assert snap["scale_source"] == "pv"
	assert snap["clim_lo"] == pytest.approx(10.0)
	assert snap["clim_hi"] == pytest.approx(120.0)
	assert "displayMax" in (snap.get("pv_scale_note") or "")


def test_pull_pv_scale_pending_without_client():
	feed = LiveFeed(baseline_n=2)
	snap = feed.pull_pv_scale_now()
	assert snap["pv_scale_ok"] is False
	assert snap["scale_source"] == "pv"
	assert "pending" in (snap.get("pv_scale_note") or "").lower()
