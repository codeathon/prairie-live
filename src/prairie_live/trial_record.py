"""Human-readable trial records for mp-sync.

JSONL stays machine-friendly (one object per line). Bulky SLM command dumps
live only on the once-per-batch ``slm_packed`` row; each trial gets a short
``summary`` plus optional pretty files next to the PNGs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Stable key order so trials.jsonl is skimmable (not alpha-sorted noise).
_TRIAL_KEY_ORDER = (
	"phase",
	"summary",
	"trial_index",
	"trigger_index",
	"n_triggers",
	"group_index",
	"group_name",
	"point_ids",
	"power",
	"score",
	"score_kind",
	"stim_mode",
	"trigger",
	"trigger_selection",
	"iteration",
	"t_cmd",
	"t_ttl",
	"image_paths",
	"record_paths",
)

# Never repeat these on every armed/done line — see phase=slm_packed.
_BULKY = frozenset({"slm_parts", "group_trigger_map"})


def format_trial_summary(row: dict[str, Any]) -> str:
	"""One-line human summary for console + JSONL ``summary`` field."""
	ti = row.get("trial_index")
	pts = ",".join(str(p) for p in row.get("point_ids") or [])
	power = row.get("power")
	score = row.get("score")
	kind = row.get("score_kind") or ""
	mode = row.get("stim_mode") or "?"
	trig = row.get("trigger_index")
	n = row.get("n_triggers")
	parts = [f"trial {ti}"]
	if trig is not None and n is not None:
		parts.append(f"pulse {trig}/{int(n) - 1}")
	parts.append(f"points [{pts}]")
	parts.append(f"power {power}")
	parts.append(f"mode {mode}")
	if score is not None:
		parts.append(f"ΔF/F {float(score):.4f} ({kind})")
	elif kind:
		parts.append(f"score {kind}")
	return " · ".join(parts)


def format_trial_readable(row: dict[str, Any]) -> str:
	"""Multi-line block for readable.txt next to trial PNGs."""
	pts = ", ".join(str(p) for p in row.get("point_ids") or [])
	trig = row.get("trigger_index")
	n = row.get("n_triggers")
	pulse = (
		f"pulse {trig} of {int(n) - 1} (0-based; {n} groups in packed -slm)"
		if trig is not None and n is not None
		else "n/a"
	)
	score = row.get("score")
	score_s = (
		f"{float(score):.6f} ({row.get('score_kind')})"
		if score is not None
		else str(row.get("score_kind") or "none")
	)
	imgs = row.get("image_paths") or {}
	img_dir = imgs.get("trial_dir") or "(none)"
	lines = [
		f"Trial {row.get('trial_index')}",
		f"  summary:   {row.get('summary') or format_trial_summary(row)}",
		f"  group:     {row.get('group_name')}",
		f"  points:    {pts}  ← these are the FOV indices that fired",
		f"  power:     {row.get('power')} (Prairie UI UncagingLaserPower)",
		f"  stim_mode: {row.get('stim_mode')}  trigger={row.get('trigger')} "
		f"line={row.get('trigger_selection')}",
		f"  TTL/pulse: {pulse}",
		f"  score:     {score_s}",
		f"  images:    {img_dir}",
		"",
		"Ignore raw -MarkAllPoints argv dumps; see phase=slm_packed in trials.jsonl",
		"for the one packed command + full pulse→group map for this power batch.",
	]
	return "\n".join(lines) + "\n"


def order_trial_row(row: dict[str, Any]) -> dict[str, Any]:
	"""Drop bulky fields and order keys for readable JSONL."""
	out: dict[str, Any] = {}
	for key in _TRIAL_KEY_ORDER:
		if key in row and key not in _BULKY:
			out[key] = row[key]
	for key, val in row.items():
		if key in _BULKY or key in out:
			continue
		out[key] = val
	return out


def write_trial_sidecars(row: dict[str, Any], trial_dir: Path) -> dict[str, str]:
	"""Pretty trial.json + readable.txt beside f0/f1/dff.png."""
	trial_dir.mkdir(parents=True, exist_ok=True)
	payload = order_trial_row({**row, "phase": row.get("phase") or "done"})
	json_path = trial_dir / "trial.json"
	txt_path = trial_dir / "readable.txt"
	json_path.write_text(
		json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
		encoding="utf-8",
	)
	txt_path.write_text(format_trial_readable(payload), encoding="utf-8")
	return {
		"trial_json": str(json_path.resolve()),
		"readable_txt": str(txt_path.resolve()),
	}


def append_run_summary(run_root: Path, row: dict[str, Any]) -> None:
	"""Append one line to <run_root>/summary.txt for the whole session."""
	run_root.mkdir(parents=True, exist_ok=True)
	path = run_root / "summary.txt"
	line = row.get("summary") or format_trial_summary(row)
	with open(path, "a", encoding="utf-8") as f:
		f.write(line + "\n")


def format_packed_map(group_map: list[dict]) -> str:
	"""Readable pulse → points table for the slm_packed batch."""
	lines = ["pulse → points (packed -slm batch)", "-----"]
	for entry in group_map:
		pts = ",".join(str(p) for p in entry.get("point_ids") or [])
		lines.append(
			f"  pulse {entry.get('trigger_index')}: "
			f"{entry.get('group_name')}  points [{pts}]"
		)
	return "\n".join(lines) + "\n"
