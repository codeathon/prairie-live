"""Rank fat-XML points by lowest power that still stimulated (f0/f1 ΔF/F)."""

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


def rank_candidate_points(
	trials: list[dict[str, Any]],
	points: list[dict[str, Any]],
	*,
	stim_dff_min: float = 0.0,
) -> list[dict[str, Any]]:
	"""Lowest stimulating power first, then highest ΔF/F; unstimulated last."""
	by_id = {str(p["id"]): dict(p) for p in points}
	hits: dict[str, list[tuple[float, float]]] = {pid: [] for pid in by_id}
	for row in trials:
		ps = row.get("point_scores")
		if not isinstance(ps, dict) or not ps:
			continue
		power = float(row.get("power") if row.get("power") is not None else 0)
		for pid, dff in ps.items():
			hits.setdefault(str(pid), []).append((power, float(dff)))
	ranked: list[dict[str, Any]] = []
	for pid, pt in by_id.items():
		pairs = hits.get(pid) or []
		stim = [(p, d) for p, d in pairs if d >= stim_dff_min]
		if stim:
			min_p = min(p for p, _ in stim)
			dff = max(d for p, d in stim if p == min_p)
			ranked.append(_cand_row(pt, True, min_p, dff))
		else:
			best = max((d for _, d in pairs), default=None)
			ranked.append(_cand_row(pt, False, None, best))
	ranked.sort(
		key=lambda r: (
			0 if r["stimulated"] else 1,
			float(r["min_power"]) if r["stimulated"] else 1e9,
			-(float(r["dff"]) if r["dff"] is not None else -1e9),
		)
	)
	_assign_terciles(ranked)
	return ranked


def _cand_row(pt: dict, stimulated: bool, min_power, dff) -> dict[str, Any]:
	return {
		"id": str(pt["id"]),
		"x": float(pt["x"]),
		"y": float(pt["y"]),
		"stimulated": stimulated,
		"min_power": min_power,
		"dff": dff,
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
		"CANDIDATES — lowest power that stimulated, then max ΔF/F",
		"id  tercile  min_power  dff",
	]
	for r in ranked:
		pwr = "-" if r["min_power"] is None else str(r["min_power"])
		dff = "-" if r["dff"] is None else f"{float(r['dff']):.4f}"
		tag = "stim" if r["stimulated"] else "no stim"
		lines.append(f"{r['id']}  {r['tercile']}  {pwr}  {dff}  {tag}")
	return "\n".join(lines) + "\n"


def write_candidates_xml(
	selected: list[dict[str, Any]],
	meta: dict[str, Any],
	path: str | Path,
) -> Path:
	"""Fat XML (one ungrouped pool) for the next mp-sync --series."""
	pts = [r.get("point") or r for r in selected]
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
		slim = [{k: r[k] for k in ("id", "x", "y", "stimulated", "min_power", "dff", "tercile")} for r in ranked]
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
