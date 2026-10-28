"""GUI session planning helpers (no tkinter — safe for unit tests)."""

from __future__ import annotations

from typing import Any

from prairie_live.experiment_config import powers_to_list
from prairie_live.trial_record import format_trial_summary


GROUP_SIZE = 3


def n_groups_for_pool(n_points: int, group_size: int = GROUP_SIZE) -> int:
	"""Most groups of at least ``group_size`` with no leftover and no wrap."""
	if n_points < group_size:
		raise ValueError(
			f"need at least {group_size} points, got {n_points}"
		)
	return max(1, int(n_points) // int(group_size))


def group_sizes_for_pool(
	n_points: int,
	*,
	min_size: int = GROUP_SIZE,
	n_groups: int | None = None,
) -> list[int]:
	"""Even partition: every size >= min_size, sum == n_points, no leftovers.

	8 → [4, 4], 7 → [4, 3], 9 → [3, 3, 3], 5 → [5].
	"""
	ng = n_groups if n_groups is not None else n_groups_for_pool(n_points, min_size)
	ng = int(ng)
	if ng < 1:
		raise ValueError("n_groups must be >= 1")
	if n_points < ng * min_size:
		ng = n_groups_for_pool(n_points, min_size)
	base, extra = divmod(int(n_points), ng)
	sizes = [base + 1] * extra + [base] * (ng - extra)
	if any(s < min_size for s in sizes):
		raise ValueError(
			f"cannot cover {n_points} points with {ng} groups of at least {min_size}"
		)
	return sizes


def planned_trial_count(
	n_points: int,
	powers: list[float],
	*,
	group_size: int = GROUP_SIZE,
	n_groups: int | None = None,
	iterations: int = 1,
) -> int:
	ng = n_groups if n_groups is not None else n_groups_for_pool(n_points, group_size)
	return int(iterations) * int(ng) * len(list(powers))


def plan_group_power_trials(
	groups: list[list[dict[str, Any]]],
	powers: list[float],
) -> list[dict[str, Any]]:
	"""Every group at every power. Order = power, then group (full FOV per power)."""
	out: list[dict[str, Any]] = []
	pwr = [float(p) for p in powers]
	for power in pwr:
		for gi, gpts in enumerate(groups):
			ids = [str(p["id"]) for p in gpts]
			out.append({"group_index": gi, "point_ids": ids, "power": power})
	return out


def point_power_pairs(
	groups: list[list[dict[str, Any]]],
	powers: list[float],
) -> set[tuple[str, float]]:
	"""(point id, power) pairs the plan will fire — one of each when cover_all."""
	out: set[tuple[str, float]] = set()
	for step in plan_group_power_trials(groups, powers):
		pwr = float(step["power"])
		for pid in step["point_ids"]:
			out.add((pid, pwr))
	return out


def parse_powers_field(text: str) -> list[float]:
	vals = powers_to_list(text)
	if not vals:
		raise ValueError("enter at least one power, e.g. 75,140,200")
	out: list[float] = []
	seen: set[float] = set()
	for p in vals:
		if p in seen:
			continue
		seen.add(p)
		out.append(p)
	return out


def apply_gui_overrides(
	cfg: dict[str, Any] | None = None,
	*,
	series: str,
	powers: list[float],
	seed: int,
	repetitions: int,
	stim_dff_min: float,
	n_points: int,
	n_groups: int | None = None,
	via_relay: str | None = None,
	serial: str | None = None,
	stim_mode: str | None = None,
	trigger: str | None = None,
	xml_fields: dict[str, Any] | None = None,
	visual_enabled: bool | None = None,
	visual_screen: int | None = None,
	visual_refresh_hz: float | None = None,
	visual_isi_s: float | None = None,
	visual_lead_ms: float | None = None,
	visual_grating_frames: int | None = None,
	visual_dtr_frames: int | None = None,
) -> dict[str, Any]:
	"""Lab defaults (not experiment.json) plus the form / XML fields."""
	from prairie_live.lab_defaults import STIM_MODES, TRIGGERS, lab_defaults

	opt = lab_defaults()
	if cfg:
		opt.update({k: v for k, v in cfg.items() if v is not None})
	opt["series"] = series
	opt["powers"] = list(powers)
	opt["seed"] = int(seed)
	opt["repetitions"] = max(int(repetitions), 1)
	opt["stim_dff_min"] = float(stim_dff_min)
	opt["group_size"] = GROUP_SIZE
	opt["n_groups"] = (
		int(n_groups)
		if n_groups is not None
		else n_groups_for_pool(n_points, GROUP_SIZE)
	)
	opt["iterations"] = max(int(opt.get("iterations") or 1), 1)
	# Classic SLM: one -MarkAllPoints per group (FOV 0–1); never pack with serial.
	opt["slm_pack"] = False
	opt["cover_all"] = True
	if via_relay:
		opt["via_relay"] = via_relay
	if serial:
		opt["serial"] = serial
		opt["trigger"] = "serial"
	if trigger:
		trig = str(trigger).lower()
		if trig not in TRIGGERS:
			raise ValueError(f"trigger must be one of {TRIGGERS}")
		opt["trigger"] = trig
	if stim_mode:
		mode = str(stim_mode).lower()
		if mode not in STIM_MODES:
			raise ValueError(f"stim_mode must be one of {STIM_MODES}")
		opt["stim_mode"] = mode
	if visual_enabled is not None:
		opt["visual_enabled"] = bool(visual_enabled)
	if visual_screen is not None:
		opt["visual_screen"] = max(int(visual_screen), 0)
	if visual_refresh_hz is not None:
		opt["visual_refresh_hz"] = float(visual_refresh_hz)
	if visual_isi_s is not None:
		opt["visual_isi_s"] = float(visual_isi_s)
	if visual_lead_ms is not None:
		opt["visual_lead_ms"] = float(visual_lead_ms)
	if visual_grating_frames is not None:
		opt["visual_grating_frames"] = max(int(visual_grating_frames), 1)
	if visual_dtr_frames is not None:
		opt["visual_dtr_frames"] = max(int(visual_dtr_frames), 1)
	if xml_fields:
		for key in (
			"duration_ms",
			"inter_point_delay_ms",
			"initial_delay_ms",
			"spiral_revolutions",
			"spiral_size_um",
			"fov_width_um",
			"fov_height_um",
			"pixel_size_um",
			"laser",
		):
			if xml_fields.get(key) not in (None, ""):
				opt[key] = xml_fields[key]
		if xml_fields.get("spiral") is not None:
			opt["spiral"] = bool(xml_fields["spiral"])
	return opt


def trial_log_line(row: dict[str, Any]) -> str:
	"""One scrolling-log line; falls back if the row has no summary yet."""
	summary = row.get("summary")
	if summary:
		return str(summary)
	return format_trial_summary(row)
