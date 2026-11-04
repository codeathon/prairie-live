"""Random 3-point Mark Points groups from a FAT points-only XML.

Offline / scope-PC helper: sample groups for a manual PrairieView Mark Points
run, then export a second series from user-selected groups.
"""

from __future__ import annotations

import colorsys
import random
from pathlib import Path
from typing import Any

from prairie_live.markpoints import build_group_step, groups_to_xml

GROUP_SIZE = 3


def group_rgb(group_index: int) -> tuple[int, int, int]:
	"""Distinct RGB for group index (1-based)."""
	i = max(int(group_index), 1)
	# Golden-angle hues so adjacent ids look different.
	h = ((i - 1) * 0.61803398875) % 1.0
	r, g, b = colorsys.hsv_to_rgb(h, 0.75, 0.95)
	return int(r * 255), int(g * 255), int(b * 255)


def group_centroid(points: list[dict[str, Any]]) -> tuple[float, float]:
	"""Mean FOV / galvo X,Y of the points in a group."""
	xs = [float(p["x"]) for p in points]
	ys = [float(p["y"]) for p in points]
	return sum(xs) / len(xs), sum(ys) / len(ys)


def min_groups_for_coverage(n_points: int, group_size: int = GROUP_SIZE) -> int:
	"""Fewest groups of ``group_size`` that can include every point at least once."""
	gs = max(int(group_size), 1)
	n = max(int(n_points), 0)
	if n < 1:
		return 0
	return (n + gs - 1) // gs


def _pack_group(
	chosen: list[dict[str, Any]],
) -> dict[str, Any]:
	cx, cy = group_centroid(chosen)
	return {
		"point_ids": [str(p["id"]) for p in chosen],
		"points": [dict(p) for p in chosen],
		"cx": cx,
		"cy": cy,
	}


def build_random_groups(
	points: list[dict[str, Any]],
	n_groups: int,
	*,
	group_size: int = GROUP_SIZE,
	seed: int | None = None,
) -> list[dict[str, Any]]:
	"""Sample ``n_groups`` of ``group_size`` unique points each.

	Within a group points are distinct. Across groups the same point may
	appear again. Every pool point appears in at least one group: if
	``n_groups`` is below the coverage minimum, it is raised. Covering
	groups are built from a shuffled partition (remainder filled from the
	pool); any extra groups are independent random draws. Final group
	order is shuffled so the series is not sequential by point id.
	"""
	pool = [dict(p) for p in points if p.get("x") is not None and p.get("y") is not None]
	gs = max(int(group_size), 1)
	n = max(int(n_groups), 0)
	if len(pool) < gs:
		raise ValueError(
			f"need at least {gs} points to build groups (got {len(pool)})"
		)
	if n < 1:
		raise ValueError("n_groups must be >= 1")
	need = min_groups_for_coverage(len(pool), gs)
	n = max(n, need)
	rng = random.Random(seed)
	# Coverage pass: shuffled partition so every point is used ≥ once.
	shuffled = list(pool)
	rng.shuffle(shuffled)
	raw: list[dict[str, Any]] = []
	i = 0
	while i < len(shuffled):
		chunk = shuffled[i : i + gs]
		i += gs
		if len(chunk) < gs:
			have = {str(p["id"]) for p in chunk}
			fillers = [p for p in pool if str(p["id"]) not in have]
			rng.shuffle(fillers)
			chunk = chunk + fillers[: gs - len(chunk)]
			if len(chunk) < gs:
				raise ValueError(
					f"cannot fill a group of {gs} from a pool of {len(pool)}"
				)
		raw.append(_pack_group(chunk))
	# Extra groups beyond coverage (repeats across groups allowed).
	while len(raw) < n:
		raw.append(_pack_group(rng.sample(pool, gs)))
	rng.shuffle(raw)
	groups: list[dict[str, Any]] = []
	for gi, g in enumerate(raw, start=1):
		rgb = group_rgb(gi)
		groups.append(
			{
				"id": str(gi),
				"name": f"G{gi}",
				"point_ids": g["point_ids"],
				"points": g["points"],
				"cx": g["cx"],
				"cy": g["cy"],
				"rgb": list(rgb),
			}
		)
	return groups


def groups_to_pv_steps(
	groups: list[dict[str, Any]],
	*,
	power: float,
	meta: dict[str, Any],
) -> list[dict[str, Any]]:
	"""One ``build_group_step`` per random group (fat nested-Point layout)."""
	base = dict(meta)
	# Manual PV load expects fat nested <Point> series, not PVSaved galvo layout.
	base["pv_saved_format"] = False
	steps: list[dict[str, Any]] = []
	for g in groups:
		pts = list(g.get("points") or [])
		if len(pts) < GROUP_SIZE:
			raise ValueError(f"group {g.get('id')} needs {GROUP_SIZE} points")
		step = build_group_step(
			pts,
			name=str(g.get("name") or f"G{g.get('id')}"),
			power=float(power),
			meta=base,
		)
		steps.append(step)
	return steps


def write_groups_xml(
	groups: list[dict[str, Any]],
	*,
	power: float,
	meta: dict[str, Any],
	path: str | Path,
) -> Path:
	"""Write a PV-loadable Mark Points series (one element per group of 3)."""
	steps = groups_to_pv_steps(groups, power=power, meta=meta)
	if not steps:
		raise ValueError("no groups to write")
	out = Path(path)
	out.parent.mkdir(parents=True, exist_ok=True)
	out.write_text(groups_to_xml(steps), encoding="utf-8")
	return out


def groups_xml_text(
	groups: list[dict[str, Any]],
	*,
	power: float,
	meta: dict[str, Any],
) -> str:
	"""Same as ``write_groups_xml`` but return the XML string (for download)."""
	steps = groups_to_pv_steps(groups, power=power, meta=meta)
	if not steps:
		raise ValueError("no groups to write")
	return groups_to_xml(steps)


def select_groups_by_id(
	groups: list[dict[str, Any]], ids: list[str] | set[str]
) -> list[dict[str, Any]]:
	"""Preserve order of ``ids``; keep full 3-point membership."""
	want = [str(i) for i in ids]
	by_id = {str(g["id"]): g for g in groups}
	missing = [i for i in want if i not in by_id]
	if missing:
		raise ValueError(f"unknown group id(s): {', '.join(missing)}")
	return [dict(by_id[i]) for i in want]


def overlay_group_centroids(
	groups: list[dict[str, Any]],
	*,
	selected_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
	"""FOV-fraction centroids for Live SVG (caller maps galvo→FOV if needed)."""
	sel = {str(x) for x in (selected_ids or set())}
	rows: list[dict[str, Any]] = []
	for g in groups:
		rgb = g.get("rgb") or list(group_rgb(int(g["id"])))
		rows.append(
			{
				"id": str(g["id"]),
				"name": str(g.get("name") or f"G{g['id']}"),
				"fx": float(g["cx"]),
				"fy": float(g["cy"]),
				"rgb": [int(rgb[0]), int(rgb[1]), int(rgb[2])],
				"point_ids": list(g.get("point_ids") or []),
				"selected": str(g["id"]) in sel,
			}
		)
	return rows
