"""LiveFeed baseline / %ΔF/F encode without a relay."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from prairie_live.live_feed import (
	DISPLAY_BIN,
	DISPLAY_CLIM_HI,
	DISPLAY_CLIM_LO,
	LiveFeed,
	bin_mean,
)
from prairie_live.trial_images import to_u8, to_u8_clim


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


def test_raw_clim_is_fixed_0_to_100k():
	"""Dim ADU must stay dim vs auto percentile stretch."""
	feed = LiveFeed(baseline_n=200)  # stay in raw
	img = np.zeros((32, 32), dtype=np.float64)
	img[8:24, 8:24] = 50.0
	feed.ingest_frame(img)
	snap = feed.snapshot()
	assert snap["clim_lo"] == DISPLAY_CLIM_LO
	assert snap["clim_hi"] == DISPLAY_CLIM_HI
	assert DISPLAY_CLIM_LO == 0.0
	assert DISPLAY_CLIM_HI == 100_000.0
	# Fixed clim: 50/100000 → ~0 gray; auto percentile would blast the square white.
	fixed = to_u8_clim(img, DISPLAY_CLIM_LO, DISPLAY_CLIM_HI)
	auto = to_u8(img)
	assert int(fixed[16, 16]) < 5
	assert int(auto[16, 16]) > 200
