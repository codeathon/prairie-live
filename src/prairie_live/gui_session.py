"""GUI session planning helpers (no tkinter — safe for unit tests)."""

from __future__ import annotations

from typing import Any

from prairie_live.experiment_config import powers_to_list
from prairie_live.trial_record import format_trial_summary


GROUP_SIZE = 3


def n_groups_for_pool(n_points: int, group_size: int = GROUP_SIZE) -> int:
	"""Enough groups of ``group_size`` that every point is used.

	When ``n_points`` is not a multiple of 3, ``form_groups`` wraps so the
	last group reuses earlier points. No point sits out.
	"""
	if n_points < group_size:
		raise ValueError(
			f"need at least {group_size} points, got {n_points}"
		)
	gs = int(group_size)
	return (int(n_points) + gs - 1) // gs


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
	"""Cartesian product: every group at every power. Order = group, then power."""
	out: list[dict[str, Any]] = []
	pwr = [float(p) for p in powers]
	for gi, gpts in enumerate(groups):
		ids = [str(p["id"]) for p in gpts]
		for power in pwr:
			out.append({"group_index": gi, "point_ids": ids, "power": power})
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
	cfg: dict[str, Any],
	*,
	series: str,
	powers: list[float],
	seed: int,
	repetitions: int,
	stim_dff_min: float,
	n_points: int,
	n_groups: int | None = None,
) -> dict[str, Any]:
	"""Copy experiment.json and overlay the launch-form fields."""
	opt = dict(cfg)
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
	# One trial per group×power so each group is scored at every level.
	opt["slm_pack"] = False
	return opt


def trial_log_line(row: dict[str, Any]) -> str:
	"""One scrolling-log line; falls back if the row has no summary yet."""
	summary = row.get("summary")
	if summary:
		return str(summary)
	return format_trial_summary(row)
