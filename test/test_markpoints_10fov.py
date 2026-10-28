"""samples/MarkPoints_10fov_trig_in1.xml — FOV fractions for reliable SLM tests."""

from prairie_live.lab_defaults import (
	DEFAULT_TRIGGER_FREQUENCY,
	DEFAULT_TRIGGER_IDENTIFIER,
	DEFAULT_TRIGGER_SELECTION,
)
from prairie_live.markpoints import (
	extract_unique_points,
	load_mark_points_file,
	template_meta,
)
from prairie_live.scan_geometry import is_fraction_space

_SAMPLE = "samples/MarkPoints_10fov_trig_in1.xml"


def test_10fov_loads_ten_fraction_points():
	# FOV 0–1 coords → SLM fire skips -gts (works at any zoom/pan).
	steps = load_mark_points_file(_SAMPLE)
	pts = extract_unique_points(steps)
	assert len(pts) == 10
	assert is_fraction_space(pts)
	assert all(p.get("spiral_space") == "fov" for p in pts)
	assert all(0.15 <= float(p["x"]) <= 0.85 for p in pts)
	assert all(0.20 <= float(p["y"]) <= 0.80 for p in pts)


def test_10fov_matches_lab_pvsaved_trig_timing():
	# Same FirstRepetition / Trig In 1 / Monaco timing as the lab 21-pt paste.
	steps = load_mark_points_file(_SAMPLE)
	meta = template_meta(steps)
	assert meta.get("trigger_selection") == DEFAULT_TRIGGER_SELECTION
	assert meta.get("trigger_identifier") == DEFAULT_TRIGGER_IDENTIFIER
	assert meta.get("trigger_frequency") == DEFAULT_TRIGGER_FREQUENCY
	assert meta.get("uncaging_laser") == "Monaco"
	assert float(meta.get("duration")) == 36.0
	assert float(meta.get("inter_point_delay")) == 14.0
	assert float(meta.get("initial_delay")) == 22.1
	assert float(meta.get("spiral_revolutions")) == 8.0
	assert int(meta.get("repetitions") or 0) == 5
