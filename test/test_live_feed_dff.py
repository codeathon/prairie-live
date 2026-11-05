"""LiveFeed baseline / %ΔF/F encode without a relay."""

from __future__ import annotations

import numpy as np
import pytest

from prairie_live.live_feed import LiveFeed


def _frame(val: float, h: int = 32, w: int = 32) -> np.ndarray:
	return np.full((h, w), val, dtype=np.float64)


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
