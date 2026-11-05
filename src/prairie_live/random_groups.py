"""Random 3-point Mark Points groups from a FAT points-only XML.

Offline / scope-PC helper: sample groups for a manual PrairieView Mark Points
run, then export a second series from user-selected groups.
"""

from __future__ import annotations

import colorsys
import random
from pathlib import Path
from typing import Any

from prairie_live.markpoints import (
	build_group_step,
	galvo_points_to_gpl_xml,
	groups_to_xml,
)

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


def _sort_key_point(p: dict[str, Any]) -> tuple:
	"""Stable order: numeric id, else name, else insertion."""
	pid = str(p.get("id") or "")
	if pid.isdigit():
		return (0, int(pid), "")
	return (1, 0, str(p.get("name") or pid))


def renumber_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Force ``Index == Point N``: id ``N`` and name ``Point N`` for N = 1..len.

	Source files sometimes keep a Name like ``Point 7`` with a different
	``Index`` (e.g. 31). PV then shows mismatched labels; exports renumber so
	name and index always agree.
	"""
	ordered = sorted((dict(p) for p in points), key=_sort_key_point)
	out: list[dict[str, Any]] = []
	for i, p in enumerate(ordered, start=1):
		row = dict(p)
		row["id"] = str(i)
		row["name"] = f"Point {i}"
		out.append(row)
	return out


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


def _by_id(pool: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
	out: dict[str, dict[str, Any]] = {}
	for p in pool:
		out[str(p["id"])] = p
	return out


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
	groups drain an uncovered set (fill with already-covered points when a
	tail group is short); any extra groups are independent random draws.
	Final group order is shuffled so the series is not sequential by point id.
	"""
	raw_pool = [
		dict(p) for p in points if p.get("x") is not None and p.get("y") is not None
	]
	# Point 7 ↔ Index 7 in every export (series + companion .gpl).
	pool = renumber_points(raw_pool)
	gs = max(int(group_size), 1)
	n = max(int(n_groups), 0)
	if len(pool) < gs:
		raise ValueError(
			f"need at least {gs} points to build groups (got {len(pool)})"
		)
	if n < 1:
		raise ValueError("n_groups must be >= 1")
	by_id = _by_id(pool)
	all_ids = list(by_id.keys())
	need = min_groups_for_coverage(len(all_ids), gs)
	n = max(n, need)
	rng = random.Random(seed)
	# Coverage: keep drawing from uncovered ids until none remain.
	uncovered = list(all_ids)
	rng.shuffle(uncovered)
	raw: list[dict[str, Any]] = []
	covered: set[str] = set()
	while uncovered:
		take = uncovered[:gs]
		uncovered = uncovered[gs:]
		chosen_ids = list(take)
		if len(chosen_ids) < gs:
			# Pad with already-covered (or any other) points so the group is full.
			pad_from = [i for i in all_ids if i not in chosen_ids]
			rng.shuffle(pad_from)
			chosen_ids.extend(pad_from[: gs - len(chosen_ids)])
		if len(chosen_ids) < gs:
			raise ValueError(
				f"cannot fill a group of {gs} from a pool of {len(all_ids)}"
			)
		chosen = [dict(by_id[i]) for i in chosen_ids]
		raw.append(_pack_group(chosen))
		covered.update(chosen_ids)
	# Repair pass (should be a no-op): any still-missing id gets its own group.
	missing = [i for i in all_ids if i not in covered]
	while missing:
		take = missing[:gs]
		missing = missing[gs:]
		chosen_ids = list(take)
		pad_from = [i for i in all_ids if i not in chosen_ids]
		rng.shuffle(pad_from)
		chosen_ids.extend(pad_from[: gs - len(chosen_ids)])
		raw.append(_pack_group([dict(by_id[i]) for i in chosen_ids]))
		covered.update(chosen_ids)
	# Extra groups beyond coverage (repeats across groups allowed).
	while len(raw) < n:
		raw.append(_pack_group(rng.sample(pool, gs)))
	rng.shuffle(raw)
	# Hard guarantee before returning.
	final_ids = {pid for g in raw for pid in g["point_ids"]}
	if final_ids != set(all_ids):
		miss = sorted(set(all_ids) - final_ids, key=lambda s: (not s.isdigit(), int(s) if s.isdigit() else s))
		raise RuntimeError(f"group coverage failed; missing point id(s): {', '.join(miss)}")
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


def unique_points_from_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Deduped points sorted by Index 1..N (file order PV uses on import)."""
	seen: set[str] = set()
	out: list[dict[str, Any]] = []
	for g in groups:
		for p in g.get("points") or []:
			pid = str(p.get("id"))
			if pid in seen:
				continue
			seen.add(pid)
			out.append(dict(p))
	# PrairieView reassigns indices by list order when loading a .gpl — keep
	# Point 1, Point 2, … in ascending Index order in the file.
	out.sort(key=_sort_key_point)
	return out


def groups_gpl_text(
	groups: list[dict[str, Any]],
	*,
	power: float,
	meta: dict[str, Any] | None = None,
) -> str:
	"""Companion Galvo Point List so PV can resolve series Indices without a prior .gpl."""
	meta = meta or {}
	pts = unique_points_from_groups(groups)
	if not pts:
		raise ValueError("no points to write to .gpl")
	dur = float(meta.get("duration") or pts[0].get("duration") or 100.0)
	laser = str(meta.get("uncaging_laser") or "Monaco")
	# Ensure FOV spirals carry SpiralSize for the .gpl writer.
	for p in pts:
		if p.get("spiral_size_fov") in (None, "") and p.get("spiral_width") not in (None, ""):
			p["spiral_size_fov"] = p["spiral_width"]
		if p.get("spiral_revolutions") in (None, ""):
			p["spiral_revolutions"] = meta.get("spiral_revolutions", "8")
	return galvo_points_to_gpl_xml(
		pts, power=float(power), laser=laser, duration_ms=dur
	)


def write_groups_gpl(
	groups: list[dict[str, Any]],
	*,
	power: float,
	meta: dict[str, Any] | None = None,
	path: str | Path,
) -> Path:
	"""Write the companion ``.gpl`` next to a series XML."""
	out = Path(path)
	out.parent.mkdir(parents=True, exist_ok=True)
	out.write_text(groups_gpl_text(groups, power=power, meta=meta), encoding="utf-8")
	return out


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


def _step_coord_points(step: dict[str, Any]) -> list[dict[str, Any]]:
	"""Non-placeholder points with X/Y from a parsed Mark Points step."""
	pts = list(step.get("points") or [])
	gname = str(step.get("raw_points") or step.get("name") or "")
	out: list[dict[str, Any]] = []
	for pt in pts:
		if pt.get("x") is None or pt.get("y") is None:
			continue
		if (
			pt.get("name") == gname
			and float(pt.get("x", 0)) == 0.0
			and float(pt.get("y", 0)) == 0.0
			and len(pts) == 1
		):
			continue
		out.append(dict(pt))
	return out


def classify_series(steps: list[dict[str, Any]]) -> str:
	"""``group_catalog`` if ≥2 multi-point steps; else ``points_pool``."""
	multi = [s for s in steps if len(_step_coord_points(s)) >= 2]
	if len(multi) >= 2:
		return "group_catalog"
	return "points_pool"


def catalog_groups_from_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Stable group catalog from a series that already encodes multi-point steps."""
	out: list[dict[str, Any]] = []
	gi = 0
	for step in steps:
		pts = _step_coord_points(step)
		if len(pts) < 2:
			continue
		gi += 1
		cx, cy = group_centroid(pts)
		gid = str(step.get("id") or gi)
		name = str(step.get("name") or f"G{gid}")
		# Prefer G# label when Points= listed Point names.
		if name.lower().startswith("point ") or "," in name:
			name = f"G{gi}"
		out.append(
			{
				"id": str(gi),
				"name": name if name.startswith("G") else f"G{gi}",
				"point_ids": [str(p["id"]) for p in pts],
				"points": pts,
				"cx": cx,
				"cy": cy,
				"rgb": list(group_rgb(gi)),
				"source_step": dict(step),
			}
		)
	if len(out) < 2:
		raise ValueError("need at least 2 multi-point groups in the series")
	return out


def build_group_sequence(
	catalog: list[dict[str, Any]],
	n: int,
	*,
	seed: int | None = None,
) -> list[dict[str, Any]]:
	"""Random-with-replacement sequence of catalog groups, length ``n``."""
	if len(catalog) < 1:
		raise ValueError("empty group catalog")
	n = int(n)
	if n < 1:
		raise ValueError("n_sequences must be >= 1")
	rng = random.Random(seed)
	return [dict(g) for g in rng.choices(catalog, k=n)]


_STEP_META_KEYS = (
	"duration",
	"repetitions",
	"initial_delay",
	"inter_point_delay",
	"spiral_revolutions",
	"uncaging_laser",
	"trigger_selection",
	"trigger_identifier",
	"trigger_frequency",
	"trigger_count",
	"async_sync_frequency",
	"voltage_output_category",
	"voltage_rec_category",
	"parameter_set",
	"use_3d",
	"all_points_at_once",
	"calc_funct_map",
	"iteration_delay",
)


def sequence_to_pv_steps(
	sequence: list[dict[str, Any]],
	*,
	meta: dict[str, Any],
) -> list[dict[str, Any]]:
	"""Rebuild fat steps from a sequence, preserving each source group's fields."""
	base = dict(meta)
	base["pv_saved_format"] = False
	steps: list[dict[str, Any]] = []
	for g in sequence:
		pts = list(g.get("points") or [])
		if len(pts) < 2:
			raise ValueError(f"group {g.get('id')} needs at least 2 points")
		src = dict(g.get("source_step") or {})
		step_meta = dict(base)
		for k in _STEP_META_KEYS:
			if src.get(k) is not None:
				step_meta[k] = src[k]
		pwr = src.get("laser_pwr")
		if pwr is None:
			pwr = base.get("power", 160)
		step = build_group_step(
			pts,
			name=str(g.get("name") or f"G{g.get('id')}"),
			power=float(pwr),
			meta=step_meta,
		)
		steps.append(step)
	return steps


def sequence_xml_text(
	sequence: list[dict[str, Any]],
	*,
	meta: dict[str, Any],
) -> str:
	steps = sequence_to_pv_steps(sequence, meta=meta)
	if not steps:
		raise ValueError("no groups to write")
	return groups_to_xml(steps)


def write_sequence_xml(
	sequence: list[dict[str, Any]],
	*,
	meta: dict[str, Any],
	path: str | Path,
) -> Path:
	out = Path(path)
	out.parent.mkdir(parents=True, exist_ok=True)
	out.write_text(sequence_xml_text(sequence, meta=meta), encoding="utf-8")
	return out


def catalog_power(catalog: list[dict[str, Any]], default: float = 160.0) -> float:
	"""UI power from the first catalog group's source step."""
	for g in catalog:
		src = g.get("source_step") or {}
		if src.get("laser_pwr") is not None:
			return float(src["laser_pwr"])
	return float(default)
