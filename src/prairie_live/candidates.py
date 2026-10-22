"""Candidate list: every point×power that showed an imaged |ΔF/F| change."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prairie_live.markpoints import build_group_step, groups_to_xml

TERCILE_RGB = {
	"top": (50, 220, 80),
	"mid": (240, 200, 40),
	"bottom": (180, 180, 180),
	"none": (90, 110, 200),
}


def is_image_change(dff: float | None, stim_dff_min: float = 0.0) -> bool:
	"""True when |ΔF/F| clears the threshold (burns often darken → negative ΔF/F)."""
	if dff is None:
		return False
	return abs(float(dff)) > float(stim_dff_min)


def candidate_key(pid: str, power: float) -> str:
	return f"{pid}@{float(power):g}"


def rank_candidate_points(
	trials: list[dict[str, Any]],
	points: list[dict[str, Any]],
	*,
	stim_dff_min: float = 0.0,
) -> list[dict[str, Any]]:
	"""One row per (point, power) that burned / changed in the images.

	Powers 80 and 100 both changing point 7 → two rows (7@80 and 7@100).
	Points with no imaged change are omitted.
	"""
	by_id = {str(p["id"]): dict(p) for p in points}
	# Best |ΔF/F| seen for each point at each power.
	best: dict[tuple[str, float], float] = {}
	thresh = float(stim_dff_min)
	for row in trials:
		ps = row.get("point_scores")
		if not isinstance(ps, dict) or not ps:
			continue
		power = float(row.get("power") if row.get("power") is not None else 0)
		for pid, dff in ps.items():
			d = float(dff)
			if not is_image_change(d, thresh):
				continue
			key = (str(pid), power)
			if key not in best or abs(d) > abs(best[key]):
				best[key] = d
	ranked: list[dict[str, Any]] = []
	for (pid, power), dff in best.items():
		pt = by_id.get(pid)
		if pt is None:
			continue
		ranked.append(_cand_row(pt, power, dff))
	ranked.sort(
		key=lambda r: (
			float(r["power"]),
			-(abs(float(r["dff"])) if r["dff"] is not None else -1e9),
			str(r["id"]),
		)
	)
	_assign_terciles(ranked)
	return ranked


def _cand_row(pt: dict, power: float, dff: float) -> dict[str, Any]:
	pid = str(pt["id"])
	pwr = float(power)
	return {
		"id": pid,
		"key": candidate_key(pid, pwr),
		"x": float(pt["x"]),
		"y": float(pt["y"]),
		"stimulated": True,
		"power": pwr,
		# Older UI / logs used min_power for the stimulating power column.
		"min_power": pwr,
		"dff": float(dff),
		"tercile": "none",
		"point": pt,
	}


def _assign_terciles(ranked: list[dict[str, Any]]) -> None:
	n = len(ranked)
	for i, row in enumerate(ranked):
		if not row["stimulated"]:
			row["tercile"] = "none"
			continue
		if i < n / 3:
			row["tercile"] = "top"
		elif i < 2 * n / 3:
			row["tercile"] = "mid"
		else:
			row["tercile"] = "bottom"


def format_candidates_txt(ranked: list[dict[str, Any]]) -> str:
	lines = [
		"CANDIDATES — every point×power with an imaged |ΔF/F| change",
		"id  power  dff  tercile",
	]
	for r in ranked:
		pwr = "-" if r.get("power") is None else f"{float(r['power']):g}"
		dff = "-" if r["dff"] is None else f"{float(r['dff']):.4f}"
		lines.append(f"{r['id']}  {pwr}  {dff}  {r['tercile']}")
	return "\n".join(lines) + "\n"


def unique_points_from_rows(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Deduplicate by point id (first row wins) for next-round XML."""
	out: list[dict[str, Any]] = []
	seen: set[str] = set()
	for r in selected:
		pt = r.get("point") or r
		pid = str(pt.get("id") or r.get("id"))
		if pid in seen:
			continue
		seen.add(pid)
		out.append(pt)
	return out


def write_candidates_xml(
	selected: list[dict[str, Any]],
	meta: dict[str, Any],
	path: str | Path,
) -> Path:
	"""Fat XML (one ungrouped pool) for the next mp-sync --series."""
	pts = unique_points_from_rows(selected)
	if not pts:
		raise ValueError("no points selected")
	step = build_group_step(pts, name="candidates", power=0.0, meta=meta)
	out = Path(path)
	out.parent.mkdir(parents=True, exist_ok=True)
	out.write_text(groups_to_xml([step]), encoding="utf-8")
	return out


def write_session_candidates(
	trials: list[dict[str, Any]],
	points: list[dict[str, Any]],
	*,
	meta: dict[str, Any],
	stim_dff_min: float = 0.0,
	log: Any | None = None,
	run_root: Path | None = None,
	out_dir: Path | None = None,
) -> list[dict[str, Any]]:
	ranked = rank_candidate_points(trials, points, stim_dff_min=stim_dff_min)
	text = format_candidates_txt(ranked)
	print("\n" + text)
	dest = out_dir or run_root
	paths: dict[str, str] = {}
	if dest is not None:
		dest = Path(dest)
		dest.mkdir(parents=True, exist_ok=True)
		(dest / "candidates.txt").write_text(text, encoding="utf-8")
		slim = [
			{
				k: r[k]
				for k in ("id", "key", "x", "y", "power", "min_power", "dff", "tercile", "stimulated")
			}
			for r in ranked
		]
		(dest / "candidates.json").write_text(
			json.dumps(slim, indent=2) + "\n", encoding="utf-8"
		)
		top = [r for r in ranked if r["tercile"] == "top"]
		if top:
			xml_path = write_candidates_xml(top, meta, dest / "candidates_top.xml")
			paths["xml"] = str(xml_path)
			print(f"top-third XML → {xml_path}")
		paths["txt"] = str((dest / "candidates.txt").resolve())
		paths["json"] = str((dest / "candidates.json").resolve())
	if log is not None:
		log.write({"phase": "candidates", "n": len(ranked), "paths": paths})
	return ranked
