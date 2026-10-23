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
_LEGACY_TRIGGER_LINES = frozenset(
	{"", "None", "none", "PFI1", "pfi1", "TrigIn1", "TrigIn 1", "Trig In1"}
)


def normalize_trigger_selection(name: str | None) -> str:
	"""Map empty / legacy PFI1 names to the lab Trig In 1 line."""
	s = str(name or "").strip()
	if s in _LEGACY_TRIGGER_LINES:
		return DEFAULT_TRIGGER_SELECTION
	return s


def trigger_identifier_for(selection: str | None, explicit: str | None = None) -> str:
	"""GUID for PVSaved XML. Prefer an ID from the loaded file; else lab Trig In 1."""
	ex = str(explicit or "").strip()
	if ex and ex != NO_TRIGGER_IDENTIFIER:
		return ex
	sel = normalize_trigger_selection(selection)
	if sel in ("None", "none", ""):
		return NO_TRIGGER_IDENTIFIER
	return DEFAULT_TRIGGER_IDENTIFIER


def lab_defaults() -> dict[str, Any]:
	return {
		"via_relay": DEFAULT_RELAY,
		"serial": DEFAULT_SERIAL,
		"trigger": "serial",
		"trigger_selection": DEFAULT_TRIGGER_SELECTION,
		"stim_mode": DEFAULT_STIM_MODE,
		"slm_pack": False,
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
