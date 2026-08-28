"""Tests for per-run / per-trial QC image dumps."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from prairie_live.trial_images import (
	draw_point_pool,
	power_tag,
	run_dir,
	save_trial_images,
	trial_dir_name,
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
