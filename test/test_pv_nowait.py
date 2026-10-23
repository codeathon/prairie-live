"""-NoWait must arm triggered Mark Points before DTR, not after."""

from __future__ import annotations

from prairie_live.pv_script import _script_parts_wait_on_trigger


def test_detect_trig_in_1():
	parts = [
		"-MarkAllPoints",
		"1",
		"False",
		"0.1",
		"0.2",
		"10",
		"Monaco",
		"40",
		"True",
		"0.1",
		"5",
		"Trig In 1",
		"1",
	]
	assert _script_parts_wait_on_trigger(parts) is True


def test_detect_none_trigger():
	parts = ["-MarkAllPoints", "1", "False", "0.1", "0.2", "10", "Monaco", "40", "None"]
	assert _script_parts_wait_on_trigger(parts) is False
