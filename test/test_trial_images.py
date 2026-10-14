"""Tests for per-run / per-trial QC image dumps."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from prairie_live.trial_images import (
	FrameBuffer,
	disk_mean,
	draw_point_pool,
	mean_stack,
	power_tag,
	run_dir,
	save_trial_images,
	to_u8,
	trial_dir_name,
)
from prairie_live.viewer import (
	IntensityBuffer,
	_STALE_TICKS,
	_apply_status,
	_avg_n,
	_fit_fov_axes,
	_flash_overlay,
	_fov_rgb,
	_load_view_points,
	_make_layout,
	_on_frame,
	_update_traces,
	orient_fov,
)


def test_power_tag_and_trial_dir_name():
	assert power_tag(140) == "p140"
	assert power_tag(12.5) == "p12p5"
	assert trial_dir_name(3, 140) == "t0003_p140"
	assert trial_dir_name(3) == "t0003"


def test_save_trial_images_layout(tmp_path: Path):
	root = run_dir(tmp_path, run_id="run_test")
	# Bright blob after stim at (0.5, 0.5) so ΔF/F is nonzero.
	f0 = [np.full((32, 32), 10.0, dtype=np.float32)]
	f1 = [np.full((32, 32), 10.0, dtype=np.float32)]
	f1[0][14:18, 14:18] = 40.0
	fired = [{"id": "1", "x": 0.5, "y": 0.5}]
	pool = [
		{"id": "1", "x": 0.5, "y": 0.5},
		{"id": "2", "x": 0.25, "y": 0.25},
	]
	paths = save_trial_images(
		root,
		trial_index=3,
		frames_f0=f0,
		frames_f1=f1,
		fired_points=fired,
		all_points=pool,
		power=140,
		radius=2,
	)
	trial = root / "t0003_p140"
	assert trial.is_dir()
	assert (trial / "f0.png").is_file()
	assert (trial / "f1.png").is_file()
	assert (trial / "dff.png").is_file()
	assert paths["trial_dir"] == str(trial.resolve())
	assert Path(paths["f0"]).name == "f0.png"


def test_draw_point_pool_highlights_fired(tmp_path: Path):
	rgb = np.zeros((32, 32, 3), dtype=np.uint8)
	pool = [
		{"id": "1", "x": 0.5, "y": 0.5},
		{"id": "2", "x": 0.25, "y": 0.25},
	]
	fired = [{"id": "1", "x": 0.5, "y": 0.5}]
	out = draw_point_pool(
		rgb,
		pool,
		fired,
		radius=2,
		pool_color=(10, 10, 10),
		fired_color=(200, 50, 50),
	)
	cx, cy = 16, 16
	# Hollow ring — sample a pixel on the fired ring, not the center.
	assert out[cy, cx + 2, 0] == 200
	ox, oy = 8, 8
	assert out[oy, ox + 2, 0] == 10


def test_frame_buffer_mean_matches_mean_stack():
	# Why: view FOV must be the same stack mean as trial_images f0.png.
	a = np.full((4, 4), 10.0, dtype=np.float32)
	b = np.full((4, 4), 30.0, dtype=np.float32)
	buf = FrameBuffer(2)
	buf.push(a)
	buf.push(b)
	got = buf.mean()
	assert got is not None
	assert np.allclose(got, mean_stack([a, b]))
	buf.push(np.full((4, 4), 50.0, dtype=np.float32))
	assert len(buf) == 2
	assert np.allclose(buf.mean(), mean_stack([b, np.full((4, 4), 50.0)]))


def test_frame_buffer_clears_on_shape_change():
	buf = FrameBuffer(4)
	buf.push(np.zeros((4, 4), dtype=np.float32))
	buf.push(np.ones((8, 8), dtype=np.float32))
	assert len(buf) == 1
	assert buf.mean().shape == (8, 8)


def test_avg_n_at_least_two():
	assert _avg_n(0.5, 50) == 10
	assert _avg_n(0.01, 50) == 2


class _FakeIm:
	def __init__(self):
		self.data = None
		self.extent = None
		self.axes = None

	def set_data(self, arr):
		self.data = arr

	def set_extent(self, ext):
		self.extent = ext


class _FakeTitle:
	def __init__(self):
		self.text = ""

	def set_text(self, text):
		self.text = text


class _SeqClient:
	def __init__(self, frames):
		self._frames = list(frames)
		self.last_error = ""

	def get_frame(self):
		return self._frames.pop(0) if self._frames else None


class _HoldClient:
	def __init__(self, frame):
		self._frame = frame
		self.last_error = ""

	def get_frame(self):
		return self._frame


def test_orient_fov_90cw_then_mirror_lr():
	img = np.zeros((4, 6), dtype=np.uint16)
	img[-1, 0] = 9
	out = orient_fov(img)
	assert out.shape == (6, 4)
	# 90° CW puts bottom-left at top-left; fliplr sends it to top-right.
	assert int(out[0, -1]) == 9


def test_on_frame_shows_stack_mean_not_last_raw():
	# Two flats: last raw is 30; displayed image must be mean 20 → to_u8 RGB.
	a = np.full((8, 8), 10.0, dtype=np.float32)
	b = np.full((8, 8), 30.0, dtype=np.float32)
	buf = FrameBuffer(2)
	im = _FakeIm()
	title = _FakeTitle()
	state = {"n": 0, "t0": 0.0}
	client = _SeqClient([a, b])
	_on_frame(client, buf, im, title, state)
	_on_frame(client, buf, im, title, state)
	expect = _fov_rgb(mean_stack([a, b]), [], 3)
	assert np.array_equal(im.data, expect)
	assert "avg 2/2" in title.text


class _FakeLine:
	def __init__(self):
		self.xy = None

	def set_data(self, x, y):
		self.xy = (x, y)


class _FakeAx:
	def __init__(self):
		self.xlim = None
		self.ylim = None

	def set_xlim(self, a, b):
		self.xlim = (a, b)

	def set_ylim(self, a, b):
		self.ylim = (a, b)


def test_load_view_points_from_sample_xml():
	pts, r = _load_view_points("samples/MarkPoints_8fov.xml", None, None)
	assert len(pts) == 8
	assert r == 3
	assert all(0.0 <= float(p["x"]) <= 1.0 for p in pts)


def test_load_view_points_empty_without_series():
	pts, r = _load_view_points(None, None, 5)
	assert pts == []
	assert r == 5


def test_update_traces_blob_raises_pool_and_skips_oob():
	# Bright blob at (0.5,0.5); OOB point must not enter the pool mean.
	frame = np.full((32, 32), 10.0, dtype=np.float32)
	frame[14:18, 14:18] = 80.0
	points = [
		{"id": "1", "x": 0.5, "y": 0.5},
		{"id": "oob", "x": -2.0, "y": 0.5},
	]
	n = 8
	traces = {
		"points": points,
		"radius": 2,
		"buffers": {
			"1": IntensityBuffer(n),
			"oob": IntensityBuffer(n),
			"pool": IntensityBuffer(n),
		},
		"lines": {"1": _FakeLine(), "oob": _FakeLine(), "pool": _FakeLine()},
		"ax": _FakeAx(),
		"dt": 0.05,
	}
	_update_traces(frame, traces)
	assert len(traces["buffers"]["1"]) == 1
	assert len(traces["buffers"]["oob"]) == 0
	assert traces["buffers"]["pool"].as_array()[-1] == pytest.approx(
		disk_mean(frame, 0.5, 0.5, 2)
	)
	assert traces["buffers"]["1"].as_array()[-1] > 10.0


def test_make_layout_one_figure_for_all_panels():
	# Why: extra views must share this GridSpec, not spawn a second window.
	import matplotlib

	matplotlib.use("Agg")
	fig, gs = _make_layout(2, width_ratios=[1.05, 1.0])
	a0 = fig.add_subplot(gs[0, 0])
	a1 = fig.add_subplot(gs[0, 1])
	assert a0.figure is fig
	assert a1.figure is fig
	assert a0.figure is a1.figure


def test_on_frame_none_says_no_frame_from_relay():
	title = _FakeTitle()
	state = {"n": 0, "t0": 0.0}
	_on_frame(_SeqClient([]), FrameBuffer(2), _FakeIm(), title, state)
	assert "no frame from relay" in title.text or "no FOV" in title.text


def test_apply_status_updates_hud():
	title = _FakeTitle()
	hud = _FakeTitle()
	_apply_status(title, "512x512  LIVE  max=40", {"hud": hud})
	assert title.text == "512x512  LIVE  max=40"
	assert hud.text == "512x512  LIVE  max=40"


def test_on_frame_skips_1x1_grab():
	buf = FrameBuffer(4)
	im = _FakeIm()
	title = _FakeTitle()
	state = {"n": 0, "t0": 0.0}
	tiny = np.array([[16]], dtype=np.uint16)
	_on_frame(_SeqClient([tiny]), buf, im, title, state, traces=None)
	assert len(buf) == 0
	assert "1x1" in title.text
	assert im.data is None


def test_on_frame_without_series_still_updates_fov():
	frame = np.full((8, 8), 12.0, dtype=np.float32)
	buf = FrameBuffer(4)
	im = _FakeIm()
	title = _FakeTitle()
	state = {"n": 0, "t0": 0.0}
	_on_frame(_SeqClient([frame]), buf, im, title, state, traces=None)
	assert im.data is not None
	assert im.data.ndim == 3
	assert "avg 1/4" in title.text
	assert "max=12" in title.text
	assert "LIVE" in title.text


def test_on_frame_title_stale_when_pixels_frozen():
	# Same buffer every tick → STALE (Live not filling PrairieLink).
	frame = np.arange(64, dtype=np.uint16).reshape(8, 8)
	buf = FrameBuffer(8)
	im = _FakeIm()
	title = _FakeTitle()
	state = {"n": 0, "t0": 0.0}
	client = _HoldClient(frame)
	_on_frame(client, buf, im, title, state)
	assert "LIVE" in title.text
	for _ in range(_STALE_TICKS):
		_on_frame(client, buf, im, title, state)
	assert "STALE" in title.text


def test_on_frame_rescales_axes_from_placeholder():
	# Why: stuck 32² xlim cropped a real 512 FOV to a dark corner.
	frame = np.arange(64, dtype=np.uint16).reshape(8, 8)
	buf = FrameBuffer(4)
	im = _FakeIm()
	im.axes = _FakeAx()
	title = _FakeTitle()
	state = {"n": 0, "t0": 0.0}
	_on_frame(_SeqClient([frame]), buf, im, title, state, traces=None)
	assert im.extent == (0, 8, 8, 0)
	assert im.axes.xlim == (0, 8)
	assert im.axes.ylim == (8, 0)


def test_flash_overlay_ignores_global_jump_keeps_spot():
	avg = np.full((16, 16), 10.0)
	assert _flash_overlay(avg, avg + 20.0) is None
	latest = avg.copy()
	latest[8, 8] = 80.0
	heat = _flash_overlay(avg, latest)
	assert heat is not None
	assert heat[8, 8] > heat[0, 0]


def test_update_traces_fov_p99_without_xml_points():
	# Photostim of newly created PV points must move fov/p99 without --series.
	frame = np.full((16, 16), 10.0, dtype=np.float32)
	frame[4:8, 4:8] = 90.0
	n = 8
	traces = {
		"points": [],
		"radius": 3,
		"buffers": {
			"fov": IntensityBuffer(n),
			"p99": IntensityBuffer(n),
			"pool": IntensityBuffer(n),
		},
		"lines": {"fov": _FakeLine(), "p99": _FakeLine(), "pool": _FakeLine()},
		"ax": _FakeAx(),
		"dt": 0.05,
	}
	_update_traces(frame, traces)
	assert traces["buffers"]["p99"].as_array()[-1] > traces["buffers"]["fov"].as_array()[-1]
	assert len(traces["buffers"]["pool"]) == 0


def test_fit_fov_axes_noop_without_axes():
	im = _FakeIm()
	_fit_fov_axes(im, 8, 8)
	assert im.axes is None
