"""Tests for per-run / per-trial QC image dumps."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from prairie_live.trial_images import run_dir, save_trial_images


def test_save_trial_images_layout(tmp_path: Path):
	root = run_dir(tmp_path, run_id="run_test")
	# Bright blob after stim at (0.5, 0.5) so ΔF/F is nonzero.
	f0 = [np.full((32, 32), 10.0, dtype=np.float32)]
	f1 = [np.full((32, 32), 10.0, dtype=np.float32)]
	f1[0][14:18, 14:18] = 40.0
	pts = [{"id": "1", "x": 0.5, "y": 0.5}]
	paths = save_trial_images(
		root,
		trial_index=3,
		frames_f0=f0,
		frames_f1=f1,
		points=pts,
		radius=2,
	)
	trial = root / "t0003"
	assert trial.is_dir()
	assert (trial / "f0.png").is_file()
	assert (trial / "f1.png").is_file()
	assert (trial / "dff.png").is_file()
	assert paths["trial_dir"] == str(trial.resolve())
	assert Path(paths["f0"]).name == "f0.png"
