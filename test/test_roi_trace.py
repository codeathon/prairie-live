"""Unit tests for live ROI mean intensity (no PrairieView)."""

from __future__ import annotations

import numpy as np
import pytest

from prairie_live.roi_trace import (
	DiskRoi,
	IntensityBuffer,
	RectRoi,
	roi_label,
	roi_mean,
)
from prairie_live.viewer import _avg_n, _set_roi, _update_traces


def test_full_frame_mean():
	img = np.arange(16, dtype=np.float32).reshape(4, 4)
	assert roi_mean(img, None) == pytest.approx(float(img.mean()))


def test_rect_mean_clips_and_ignores_oob():
	img = np.zeros((10, 10), dtype=np.float32)
	img[2:5, 3:7] = 8.0
	got = roi_mean(img, RectRoi(3, 2, 7, 5))
	assert got == pytest.approx(8.0)
	# Fully off-frame (Prairie-style galvo coords in pixel space).
	assert roi_mean(img, RectRoi(-20, -20, -5, -5)) is None


def test_disk_mean_on_blob_vs_background():
	img = np.zeros((32, 32), dtype=np.float32)
	img[14:18, 14:18] = 40.0
	on_blob = roi_mean(img, DiskRoi(16, 16, 2))
	bg = roi_mean(img, DiskRoi(2, 2, 2))
	assert on_blob is not None and on_blob > 10
	assert bg == pytest.approx(0.0)
	assert roi_mean(img, DiskRoi(-40, 16, 2)) is None


def test_roi_label_kinds():
	assert roi_label(None) == "full FOV"
	assert "rect" in roi_label(RectRoi(0, 0, 10, 20))
	assert "disk" in roi_label(DiskRoi(8, 9, 4))


def test_buffer_rolls_clears_and_keeps_window():
	n = 10
	buf = IntensityBuffer(n)
	for i in range(n + 5):
		buf.push(float(i))
	assert len(buf) == n
	out = buf.as_array()
	assert out.shape == (n,)
	# Oldest kept sample is i=5, newest is i=14.
	assert out[0] == 5.0
	assert out[-1] == 14.0
	buf.clear()
	assert len(buf) == 0
	assert buf.as_array().size == 0


def test_window_samples_at_least_two():
	assert _avg_n(8.0, 50) == 160
	assert _avg_n(0.01, 50) == 2


class _Line:
	def __init__(self):
		self.xy = ([], [])

	def set_data(self, x, y):
		self.xy = (x, y)


class _Ax:
	def set_xlim(self, *a):
		pass

	def set_ylim(self, *a):
		pass


def _traces(n=16):
	names = ("fov", "p99", "pool", "roi")
	return {
		"points": [],
		"radius": 3,
		"buffers": {k: IntensityBuffer(n) for k in names},
		"lines": {k: _Line() for k in names},
		"ax": _Ax(),
		"dt": 0.05,
		"roi": None,
	}


def test_roi_trace_follows_selected_box_and_resets():
	frame = np.zeros((64, 64), dtype=np.float32)
	frame[10:20, 30:40] = 500.0
	tr = _traces()
	_update_traces(frame, tr)
	assert len(tr["buffers"]["roi"]) == 0
	_set_roi(tr, RectRoi(30, 10, 40, 20))
	_update_traces(frame, tr)
	assert tr["buffers"]["roi"].as_array()[-1] == pytest.approx(500.0)
	assert len(tr["buffers"]["fov"]) == 2
	_set_roi(tr, None)
	assert len(tr["buffers"]["roi"]) == 0


def test_sync_overlay_switches_rect_and_disk():
	pytest.importorskip("matplotlib")
	from matplotlib.patches import Circle, Rectangle

	from prairie_live.viewer import _sync_overlay

	rect = Rectangle((0, 0), 0, 0, visible=False)
	circ = Circle((0, 0), 1, visible=False)
	_sync_overlay(rect, circ, RectRoi(5, 6, 15, 26))
	assert rect.get_visible() and not circ.get_visible()
	assert rect.get_width() == 10 and rect.get_height() == 20
	_sync_overlay(rect, circ, DiskRoi(8, 9, 4))
	assert circ.get_visible() and not rect.get_visible()
	_sync_overlay(rect, circ, None)
	assert not rect.get_visible() and not circ.get_visible()
