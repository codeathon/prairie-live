"""Hard lab defaults so the web UI does not need experiment.json."""

from __future__ import annotations

import os
from typing import Any

# Scope DHCP address changes; `gui --relay` / PRAIRIE_LIVE_RELAY override it.
DEFAULT_RELAY = os.environ.get("PRAIRIE_LIVE_RELAY") or "10.33.107.221:25100"
DEFAULT_SERIAL = "COM3"
DEFAULT_STIM_MODE = "slm"
# Bruker System Trigger BNC label (DTR → Trig In 1). Older configs used "PFI1".
DEFAULT_TRIGGER_SELECTION = "Trig In 1"
# PVSavedMarkPointSeriesElements uses a GUID, not the label. This is Trig In 1
# on the lab scope (from a PV-saved series). Null GUID = no trigger in the UI.
DEFAULT_TRIGGER_IDENTIFIER = "446f2fb8-5044-4ad3-be85-9f1b50d4be2a"
NO_TRIGGER_IDENTIFIER = "00000000-0000-0000-0000-000000000000"
# Match PV "Wait for Trigger → First repetition" for one DTR per trial/group.
DEFAULT_TRIGGER_FREQUENCY = "FirstRepetition"
STIM_MODES = ("slm", "series")
# serial: PV waits on Trig In 1 for our DTR pulse. none: PV fires as soon as the command lands.
TRIGGERS = ("serial", "none")
# Old spellings of the Trig In 1 BNC. "None"/"" mean NO trigger — never Trig In 1.
_LEGACY_TRIGGER_LINES = frozenset({"PFI1", "pfi1", "TrigIn1", "TrigIn 1", "Trig In1"})
_NONE_NAMES = frozenset({"", "None", "none"})

# PV saved series store only the trigger GUID. Only GUIDs confirmed on this rig
# go here; anything else is reported as unknown instead of guessed.
KNOWN_TRIGGER_GUIDS: dict[str, str] = {
	DEFAULT_TRIGGER_IDENTIFIER: "Trig In 1",
}


def normalize_trigger_selection(name: str | None) -> str:
	"""Legacy PFI1 spellings → 'Trig In 1'; empty / None → 'None' (no trigger)."""
	s = str(name or "").strip()
	if s in _NONE_NAMES:
		return "None"
	if s in _LEGACY_TRIGGER_LINES:
		return DEFAULT_TRIGGER_SELECTION
	return s


def _guid(g: str | None) -> str:
	return str(g or "").strip().lower()


def trigger_name_for_guid(guid: str | None) -> str | None:
	"""'None' for the null GUID, the input name for a known GUID, else None."""
	g = _guid(guid)
	if not g or g == NO_TRIGGER_IDENTIFIER:
		return "None"
	return KNOWN_TRIGGER_GUIDS.get(g)


def guid_for_trigger_name(name: str | None) -> str | None:
	sel = normalize_trigger_selection(name)
	if sel == "None":
		return NO_TRIGGER_IDENTIFIER
	for g, n in KNOWN_TRIGGER_GUIDS.items():
		if n == sel:
			return g
	return None


def trigger_identifier_for(selection: str | None, explicit: str | None = None) -> str:
	"""GUID PV must see for ``selection``.

	The GUID always follows the selected input: a known name uses its known GUID
	even if the file carried another one (that mismatch is what made the log say
	Trig In 1 while PV waited on a different input). ``explicit`` is only used
	for an input whose GUID is not in KNOWN_TRIGGER_GUIDS.
	"""
	sel = normalize_trigger_selection(selection)
	if sel == "None":
		return NO_TRIGGER_IDENTIFIER
	known = guid_for_trigger_name(sel)
	if known:
		return known
	ex = _guid(explicit)
	if ex and ex != NO_TRIGGER_IDENTIFIER:
		return ex
	raise ValueError(
		f"no GUID known for trigger input {sel!r}: save a series with that input "
		"in PV's Mark Points window and add its TriggerIdentifier to "
		"lab_defaults.KNOWN_TRIGGER_GUIDS"
	)


def describe_file_trigger(selection: str | None, guid: str | None) -> str:
	"""What a loaded file says, without guessing: name, GUID-derived name, or unknown."""
	sel = normalize_trigger_selection(selection)
	g = _guid(guid)
	if sel != "None":
		return sel
	name = trigger_name_for_guid(g)
	if name is not None:
		return name
	return f"unknown trigger GUID {g}"


def resolve_trigger(
	chosen: str | None, file_selection: str | None, file_guid: str | None
) -> dict[str, Any]:
	"""Decide the trigger input + GUID for a run and say where it came from.

	``chosen``: the GUI / CLI trigger line (what DTR is wired to).
	Returns selection, identifier, file (what the file said), source, warnings.
	"""
	sel = normalize_trigger_selection(chosen)
	file_desc = describe_file_trigger(file_selection, file_guid)
	warnings: list[str] = []
	if sel == "None":
		return {"selection": "None", "identifier": NO_TRIGGER_IDENTIFIER,
			"file": file_desc, "source": "no trigger", "warnings": warnings}
	file_g = _guid(file_guid)
	known = guid_for_trigger_name(sel)
	if known:
		ident = known
	elif file_desc == sel and file_g and file_g != NO_TRIGGER_IDENTIFIER:
		ident = file_g  # file labels this input with its GUID: trust the pair
	else:
		ident = trigger_identifier_for(sel)  # raises: GUID unknown
	if file_desc == sel:
		source = "file"
	elif file_desc == "None":
		source = "prairie-live (file had no trigger)"
	else:
		source = f"prairie-live (file had {file_desc})"
		warnings.append(
			f"trigger: file uses {file_desc}, run uses {sel} ({ident}) — "
			"check the DTR cable goes to " + sel
		)
	return {"selection": sel, "identifier": ident, "file": file_desc,
		"source": source, "warnings": warnings}


def lab_defaults() -> dict[str, Any]:
	return {
		"via_relay": DEFAULT_RELAY,
		"serial": DEFAULT_SERIAL,
		"trigger": "serial",
		"trigger_selection": DEFAULT_TRIGGER_SELECTION,
		"stim_mode": DEFAULT_STIM_MODE,
		# One packed -MarkAllPoints per power; one DTR (Trig In 1) per group.
		"slm_pack": True,
		"cover_all": True,
		"password": "0000",
		"host": "127.0.0.1",
		"port": 1236,
		"log": "trials.jsonl",
		"images_dir": "trial_images",
		"image_width_px": 512,
		"image_height_px": 512,
		"laser": "Monaco",
		"use_3d": False,
		# None: use the Mark Points file (PV default is one point at a time).
		"all_points_at_once": None,
		"spiral": True,
		"spiral_size_um": 54.5,
		"spiral_revolutions": 5,
		"duration_ms": 100.0,
		"initial_delay_ms": 22.1,
		"inter_point_delay_ms": 10.0,
		"fov_width_um": 1136.7,
		"fov_height_um": 1136.7,
		"pixel_size_um": 2.220,
		"ttl_width": 0.01,
		"inter_trial": 0.5,
		# Seconds after -slm starts before the first DTR — covers SLM mask
		# generation so Trig In 1 is armed. Too short → -slm DONE before DTR.
		# Do not Abort between groups (Abort kills LiveScan).
		"slm_arm_delay_s": 3.0,
		# Idle after each group burn before the next -slm (LiveScan settle only).
		"slm_group_settle_s": 2.5,
		# Packed serial: min seconds between per-group DTR pulses (raised
		# automatically to cover burn + PV Delay + F0/F1 windows).
		"slm_group_gap_s": 3.0,
		# PV Delay between packed groups — short; each group still waits on
		# its own Trig In 1 edge.
		"slm_group_delay_ms": 100.0,
		# Keep LiveScan on through the packed sequence (per-group ΔF/F).
		"slm_live_during_stim": True,
		"pad_ms": 100.0,
		"f0_s": 0.5,
		"f1_s": 1.0,
		"frame_poll": 0.05,
		"disk_radius": 3,
		"seed": 0,
		"repetitions": 1,
		"stim_dff_min": 0.0,
		"iterations": 1,
		"group_size": 3,
		"elite_frac": 0.4,
		"visual_enabled": False,
	}
