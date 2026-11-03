"""Galvo → image calibration against real PrairieView point pairs from this rig."""

from __future__ import annotations

import json

import pytest

from prairie_live import galvo_calibration as gc
from prairie_live.scan_geometry import fraction_to_galvo, galvo_to_fraction, overlay_points

AMP = 4.2035577285  # -gts currentScanAmplitude at zoom 1 (both sessions)
GEOM = {"amp_x": AMP, "amp_y": AMP, "ctr_x": 0.0, "ctr_y": 0.0}
GPL = "samples/calibration/nov03_slm.gpl"
RUN = "samples/calibration/nov03_slm_run_MarkPoints.xml"

# Oct 2026 export pair (Use SLM likely off): width = 1 × amp.
OCT_PAIRS = [
	((-0.583964350325029, -1.77653289698036), (0.361078546307151, 0.0773739742086753)),
	((0.87471453318728, -1.72232523576876), (0.708089097303634, 0.0902696365767878)),
	((-0.579036381123974, -0.347421828674384), (0.362250879249707, 0.417350527549824)),
	((0.795867025970399, -0.11087930702374), (0.689331770222743, 0.473622508792497)),
]


@pytest.fixture(autouse=True)
def _reset_k(tmp_path, monkeypatch):
	monkeypatch.setenv("PRAIRIE_LIVE_CALIBRATION", str(tmp_path / "cal.json"))
	yield
	gc.set_galvo_k(1.0, condition="test reset")


def test_nov03_slm_pair_needs_k2():
	galvo, fov, _ = gc._pairs_from_files(GPL, RUN)
	gc.activate("slm")
	for (gx, gy), (fx, fy) in zip(galvo, fov):
		px, py = galvo_to_fraction(gx, gy, GEOM)
		assert px == pytest.approx(fx, abs=1e-9)
		assert py == pytest.approx(fy, abs=1e-9)
		# Round trip back to the same galvo volts.
		bx, by = fraction_to_galvo(fx, fy, GEOM)
		assert (bx, by) == pytest.approx((gx, gy), abs=1e-9)


def test_oct_pair_is_k1_galvo_mode():
	gc.activate("galvo")
	for (gx, gy), (fx, fy) in OCT_PAIRS:
		assert galvo_to_fraction(gx, gy, GEOM) == pytest.approx((fx, fy), abs=1e-9)


def test_wrong_k_reproduces_screenshot_2x_spread():
	"""k=1 on the SLM rig puts rings at 0.5 + 2(f − 0.5): rings 1–3 off the image."""
	galvo, fov, _ = gc._pairs_from_files(GPL, RUN)
	gc.activate("galvo")
	for (gx, gy), (fx, fy) in zip(galvo, fov):
		px, py = galvo_to_fraction(gx, gy, GEOM)
		assert px == pytest.approx(0.5 + 2 * (fx - 0.5), abs=1e-9)
		assert py == pytest.approx(0.5 + 2 * (fy - 0.5), abs=1e-9)


def test_overlay_spiral_radius_matches_pv_spiralwidth():
	from prairie_live.markpoints import extract_unique_points, load_mark_points_file

	gc.activate("slm")
	pts = extract_unique_points(load_mark_points_file(GPL))
	rows = overlay_points(pts, GEOM)["points"]
	assert rows[0]["fx"] == pytest.approx(0.186878727634195, abs=1e-5)
	# PV SpiralWidth (diameter, image fraction) = 0.005964 → ring radius half that.
	assert rows[0]["r"] == pytest.approx(0.00596421471172959 / 2, abs=1e-5)
	assert "galvo k=(2,2)" in overlay_points(pts, GEOM)["note"]


def test_fit_and_save_calibration(capsys):
	gc.main(["--gpl", GPL, "--run-xml", RUN, "--amp", str(AMP), "--condition", "slm", "--save"])
	out = capsys.readouterr().out
	assert "k = (2, 2)" in out
	saved = json.loads(gc.calibration_path().read_text())
	assert saved["slm"]["kx"] == pytest.approx(2.0, abs=1e-9)
	# Saved calibration wins over the lab default.
	gc.set_galvo_k(1.0)
	assert "cal.json" in gc.activate("slm")
	assert gc.galvo_k() == pytest.approx((2.0, 2.0), abs=1e-9)


def test_fit_reports_exact_pair():
	galvo, fov, names = gc._pairs_from_files(GPL, RUN)
	fit = gc.fit_pairs(galvo, fov)
	assert names == ["Point 1", "Point 2", "Point 3", "Point 4"]
	assert fit["x_width"] == pytest.approx(2 * AMP, rel=1e-6)
	assert fit["y_width"] == pytest.approx(2 * AMP, rel=1e-6)
	assert abs(fit["x_center"]) < 1e-6 and abs(fit["y_center"]) < 1e-6
	assert fit["x_max_resid"] < 1e-9 and abs(fit["x_cross"]) < 1e-9
