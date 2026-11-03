"""Trigger input / GUID: report what the file says, never assume Trig In 1."""

from __future__ import annotations

import pytest

from prairie_live.lab_defaults import (
	DEFAULT_TRIGGER_IDENTIFIER as TRIG1,
	NO_TRIGGER_IDENTIFIER as NULL,
	describe_file_trigger,
	normalize_trigger_selection,
	resolve_trigger,
	trigger_identifier_for,
	trigger_name_for_guid,
)
from prairie_live.markpoints import load_mark_points_file, template_meta
from prairie_live.trial_record import format_trial_summary

UNKNOWN = "1886cb55-dabc-4faa-b68a-585e58ce4939"  # Nov 3 run XML; input not yet identified
RUN = "samples/calibration/nov03_slm_run_MarkPoints.xml"


def test_none_is_not_trig_in_1():
	assert normalize_trigger_selection("None") == "None"
	assert normalize_trigger_selection("") == "None"
	assert normalize_trigger_selection("PFI1") == "Trig In 1"
	assert trigger_identifier_for("None", TRIG1) == NULL


def test_guid_names_only_when_known():
	assert trigger_name_for_guid(TRIG1) == "Trig In 1"
	assert trigger_name_for_guid(NULL) == "None"
	assert trigger_name_for_guid(UNKNOWN) is None
	assert describe_file_trigger(None, UNKNOWN) == f"unknown trigger GUID {UNKNOWN}"


def test_pv_run_xml_with_unknown_guid_is_reported_not_renamed():
	meta = template_meta(load_mark_points_file(RUN))
	assert meta["trigger_selection"] == f"unknown trigger GUID {UNKNOWN}"
	assert meta["file_trigger_identifier"] == UNKNOWN
	# Writing it back as-is keeps the same GUID (no silent switch to Trig In 1).
	assert trigger_identifier_for(meta["trigger_selection"], UNKNOWN) == UNKNOWN


def test_chosen_trig_in_1_uses_its_own_guid_and_warns_on_file_mismatch():
	res = resolve_trigger("Trig In 1", None, UNKNOWN)
	assert res["identifier"] == TRIG1  # not the file's 1886cb55
	assert res["source"].startswith("prairie-live (file had unknown trigger GUID")
	assert res["warnings"] and "Trig In 1" in res["warnings"][0]


def test_file_none_is_labelled_as_such():
	res = resolve_trigger("Trig In 1", "None", NULL)
	assert res["source"] == "prairie-live (file had no trigger)"
	assert not res["warnings"]
	res = resolve_trigger("Trig In 1", "Trig In 1", TRIG1)
	assert res["source"] == "file"


def test_unknown_input_needs_a_guid():
	with pytest.raises(ValueError, match="no GUID known"):
		resolve_trigger("Trig In 2", None, NULL)
	# A file that labels Trig In 2 with its GUID is trusted for that pair.
	g2 = "aaaaaaaa-0000-0000-0000-000000000002"
	assert resolve_trigger("Trig In 2", "Trig In 2", g2)["identifier"] == g2


def test_trial_log_shows_guid_and_source():
	row = {
		"trial_index": 1,
		"point_ids": ["1", "2"],
		"power": 100,
		"stim_mode": "series",
		"trigger": "serial",
		"trigger_selection": "Trig In 1",
		"trigger_identifier": TRIG1,
		"trigger_source": "prairie-live (file had no trigger)",
	}
	s = format_trial_summary(row)
	assert "line Trig In 1 (GUID 446f2fb8, prairie-live (file had no trigger))" in s
