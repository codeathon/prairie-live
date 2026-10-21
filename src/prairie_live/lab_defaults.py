"""Hard lab defaults so the web UI does not need experiment.json."""

from __future__ import annotations

import os
from typing import Any

# Scope DHCP address changes; `gui --relay` / PRAIRIE_LIVE_RELAY override it.
DEFAULT_RELAY = os.environ.get("PRAIRIE_LIVE_RELAY") or "10.33.107.221:25100"
DEFAULT_SERIAL = "COM3"
DEFAULT_STIM_MODE = "slm"
STIM_MODES = ("slm", "series")
# serial: PV waits on PFI1 for our DTR pulse. none: PV fires as soon as the command lands.
TRIGGERS = ("serial", "none")


def lab_defaults() -> dict[str, Any]:
	return {
		"via_relay": DEFAULT_RELAY,
		"serial": DEFAULT_SERIAL,
		"trigger": "serial",
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
		"all_points_at_once": True,
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
