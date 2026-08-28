"""Build MarkPoints.xml from mp-sync hits (per-point ΔF/F recommendations)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from prairie_live.markpoints import build_group_step, groups_to_xml


def aggregate_point_dff(trials: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
	"""
	Best observed ΔF/F per point_id across scored trials.

	Uses per-point ``point_dff`` when present; otherwise falls back to the
	trial group ``score`` shared across ``point_ids`` (legacy rows).
	"""
	best: dict[str, dict[str, Any]] = {}
	for row in trials:
		if row.get("phase") not in (None, "done"):
			continue
		power = row.get("power")
		ti = row.get("trial_index")
		per_pt = row.get("point_dff")
		if isinstance(per_pt, dict) and per_pt:
			items = ((str(pid), float(dff)) for pid, dff in per_pt.items())
		elif row.get("score") is not None:
			score = float(row["score"])
			items = ((str(pid), score) for pid in row.get("point_ids") or [])
		else:
			continue
		for pid, dff in items:
			prev = best.get(pid)
			if prev is None or dff > float(prev["dff"]):
				best[pid] = {
					"dff": dff,
					"power": power,
					"trial_index": ti,
				}
	return best


def trial_point_hits(
	trials: list[dict[str, Any]],
	*,
	min_dff: float = 0.0,
) -> list[dict[str, Any]]:
	"""
	Every trial×point pair whose ΔF/F exceeds ``min_dff``.

	One row per qualifying observation (same point_id may appear many times).
	"""
	hits: list[dict[str, Any]] = []
	for row in trials:
		if row.get("phase") not in (None, "done"):
			continue
		power = row.get("power")
		if power is None:
			continue
		ti = row.get("trial_index")
		per_pt = row.get("point_dff")
		if isinstance(per_pt, dict) and per_pt:
			items = ((str(pid), float(dff)) for pid, dff in per_pt.items())
		elif row.get("score") is not None:
			score = float(row["score"])
			items = ((str(pid), score) for pid in row.get("point_ids") or [])
		else:
			continue
		for pid, dff in items:
			if dff <= float(min_dff):
				continue
			hits.append(
				{
					"point_id": pid,
					"dff": dff,
					"power": power,
					"trial_index": ti,
				}
			)
	return hits


def significant_point_hits(
	trials: list[dict[str, Any]],
	*,
	min_dff: float = 0.0,
) -> list[dict[str, Any]]:
	"""
	Qualifying trial×point hits, sorted by trial then descending ΔF/F.

	Threshold hook for future stricter cuts.
	"""
	hits = trial_point_hits(trials, min_dff=min_dff)
	hits.sort(
		key=lambda h: (
			h.get("trial_index") if h.get("trial_index") is not None else -1,
			-float(h["dff"]),
		)
	)
	return hits


def build_recommended_series(
	point_pool: list[dict],
	hits: list[dict[str, Any]],
	meta: dict[str, Any],
	*,
	trigger_selection: str | None = None,
) -> list[dict]:
	"""One PVMarkPointElement per hit (same layout as fat MarkPoints.xml)."""
	by_id = {str(p["id"]): p for p in point_pool}
	steps: list[dict] = []
	for hit in hits:
		pid = str(hit["point_id"])
		pt = by_id.get(pid)
		if pt is None:
			continue
		power = hit.get("power")
		if power is None:
			continue
		ti = hit.get("trial_index")
		if ti is not None:
			name = f"Point {pid} trial {ti}"
		else:
			name = f"Point {pid}"
		steps.append(
			build_group_step(
				[copy.deepcopy(pt)],
				name=name,
				power=float(power),
				meta=meta,
				trigger_selection=trigger_selection,
			)
		)
	return steps


def write_recommended_markpoints_xml(
	path: str | Path,
	steps: list[dict],
) -> Path:
	"""Write PrairieView MarkPoints series XML (UTF-8, XML declaration)."""
	out = Path(path)
	out.parent.mkdir(parents=True, exist_ok=True)
	body = groups_to_xml(steps)
	out.write_text('<?xml version="1.0" encoding="utf-8"?>\n' + body, encoding="utf-8")
	return out.resolve()


def write_recommended_series(
	trials: list[dict[str, Any]],
	*,
	point_pool: list[dict],
	meta: dict[str, Any],
	run_root: Path | None,
	min_dff: float = 0.0,
	trigger_selection: str | None = None,
	filename: str = "recommended_MarkPoints.xml",
) -> dict[str, Any] | None:
	"""
	Export significant responders to ``recommended_MarkPoints.xml``.

	Returns summary dict for JSONL logging, or None when no hits.
	"""
	hits = significant_point_hits(trials, min_dff=min_dff)
	if not hits:
		return None
	steps = build_recommended_series(
		point_pool,
		hits,
		meta,
		trigger_selection=trigger_selection,
	)
	if not steps:
		return None
	row: dict[str, Any] = {
		"min_dff": float(min_dff),
		"n_elements": len(steps),
		"n_points": len({h["point_id"] for h in hits}),
		"point_ids": [h["point_id"] for h in hits],
		"entries": [
			{
				"point_id": h["point_id"],
				"dff": float(h["dff"]),
				"power": h.get("power"),
				"trial_index": h.get("trial_index"),
			}
			for h in hits
		],
	}
	if run_root is not None:
		path = write_recommended_markpoints_xml(run_root / filename, steps)
		row["recommended_series_path"] = str(path)
	return row
