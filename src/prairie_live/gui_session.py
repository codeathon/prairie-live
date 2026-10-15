"""GUI session planning helpers (no tkinter — safe for unit tests)."""

from __future__ import annotations

from typing import Any

from prairie_live.experiment_config import powers_to_list
from prairie_live.trial_record import format_trial_summary


GROUP_SIZE = 3


def n_groups_for_pool(n_points: int, group_size: int = GROUP_SIZE) -> int:
	"""Cover the pool once in disjoint groups; leftovers sit out."""
	if n_points < group_size:
		raise ValueError(
			f"need at least {group_size} points, got {n_points}"
		)
	return max(1, n_points // int(group_size))


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
	return opt


def trial_log_line(row: dict[str, Any]) -> str:
	"""One scrolling-log line; falls back if the row has no summary yet."""
	summary = row.get("summary")
	if summary:
		return str(summary)
	return format_trial_summary(row)
