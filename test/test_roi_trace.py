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
from prairie_live.viewer import MockScope, _on_frame, _window_samples


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
	assert _window_samples(8.0, 50) == 160
	assert _window_samples(0.01, 50) == 2


class _FakeArtists:
	def __init__(self):
		self.data = None
		self.extent = None
		self.line_xy = None
		self.title = ""
		self.xlim = None
		self.ylim = None

	def set_data(self, *args):
		# imshow: set_data(arr); plot line: set_data(x, y)
		self.data = args[0] if len(args) == 1 else args
		if len(args) == 2:
			self.line_xy = args

	def set_extent(self, extent):
		self.extent = extent

	def set_text(self, text):
		self.title = text

	def set_xlim(self, *args):
		self.xlim = args

	def set_ylim(self, *args):
		self.ylim = args


def test_on_frame_pushes_rect_mean_and_updates_extent():
	# Drive the viewer update path without opening a matplotlib window.
	client = MockScope()
	buf = IntensityBuffer(32)
	im = _FakeArtists()
	ax_tr = _FakeArtists()
	line = _FakeArtists()
	title = _FakeArtists()
	# Box over the pulsing blob at ~ (180, 80) in the 256² mock frame.
	state = {"n": 0, "t0": 0.0, "roi": RectRoi(160, 60, 200, 100)}
	_on_frame(client, buf, im, ax_tr, line, 0.05, title, state)
	assert len(buf) == 1
	assert buf.as_array()[0] > 3000
	assert im.extent == (0, 256, 256, 0)
	assert "rect" in title.title
	assert state["n"] == 1


def test_mock_blob_roi_brighter_than_corner():
	# Why the mock has a localized pulse: full-FOV mean would hide a cell-sized signal.
	client = MockScope()
	frame = client.get_frame()
	blob = roi_mean(frame, RectRoi(160, 60, 200, 100))
	corner = roi_mean(frame, RectRoi(0, 180, 40, 256))
	assert blob is not None and corner is not None
	assert blob > corner


def test_build_figure_pumps_trace_with_overlay():
	# Headless matplotlib: real artists + ROI overlay, no GUI window.
	import matplotlib.pyplot as plt

	from prairie_live.viewer import _build_figure, _sync_overlay

	fig, ax_img, ax_tr, im, line, title, rect, circ = _build_figure()
	buf = IntensityBuffer(16)
	roi = RectRoi(160, 60, 200, 100)
	_sync_overlay(rect, circ, roi)
	assert rect.get_visible()
	assert not circ.get_visible()
	state = {"n": 0, "t0": 0.0, "roi": roi}
	client = MockScope()
	for _ in range(8):
		_on_frame(client, buf, im, ax_tr, line, 0.05, title, state)
	assert len(buf) == 8
	x, y = line.get_data()
	assert len(y) == 8
	assert y[-1] > 0
	fig.canvas.draw()
	plt.close(fig)
