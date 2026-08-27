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
	"""Multi-line block for trials.txt / readable.txt (human log)."""
	pts = ", ".join(str(p) for p in row.get("point_ids") or [])
	trig = row.get("trigger_index")
	n = row.get("n_triggers")
	if trig is not None and n is not None:
		pulse = f"{trig} of {int(n) - 1}  (0-based pulse index among {n} packed groups)"
	else:
		pulse = "n/a (series mode or single trial)"
	score = row.get("score")
	score_s = (
		f"{float(score):.6f}  ({row.get('score_kind')})"
		if score is not None
		else str(row.get("score_kind") or "none")
	)
	imgs = row.get("image_paths") or {}
	img_dir = imgs.get("trial_dir") or "(none)"
	ti = row.get("trial_index")
	bar = "=" * 60
	lines = [
		bar,
		f"TRIAL {ti}",
		bar,
		f"  Fired points:  {pts}",
		f"  Group name:    {row.get('group_name')}",
		f"  Laser power:   {row.get('power')}  (Prairie UI UncagingLaserPower)",
		f"  Stim mode:     {row.get('stim_mode')}   trigger={row.get('trigger')}  "
		f"line={row.get('trigger_selection')}",
		f"  TTL / pulse:   {pulse}",
		f"  ΔF/F score:    {score_s}",
		f"  Images:        {img_dir}",
		f"  One-liner:     {row.get('summary') or format_trial_summary(row)}",
		"",
	]
	return "\n".join(lines)


def format_packed_readable(row: dict[str, Any]) -> str:
	"""Human block for a packed -slm batch (no argv dump)."""
	bar = "-" * 60
	power = row.get("power")
	n = row.get("n_triggers")
	lines = [
		bar,
		f"PACKED SLM  power={power}  pulses={n}",
		bar,
		row.get("summary") or "",
		"",
		format_packed_map(row.get("group_trigger_map") or []).rstrip(),
		"",
		"(Raw -MarkAllPoints argv is only in trials.jsonl phase=slm_packed.)",
		"",
	]
	return "\n".join(lines)


def readable_log_path(jsonl_path: str | Path) -> Path:
	"""trials.jsonl → trials.txt (same folder, always openable in Notepad)."""
	p = Path(jsonl_path)
	return p.with_suffix(".txt") if p.suffix.lower() == ".jsonl" else Path(str(p) + ".txt")


class ReadableLog:
	"""Append-only plain-text twin of JsonlLog for humans."""

	def __init__(self, path: str | Path):
		self.path = Path(path)
		self.path.parent.mkdir(parents=True, exist_ok=True)
		self._fp = open(self.path, "a", encoding="utf-8")
		# Session banner so successive mp-sync runs are separable.
		from datetime import datetime, timezone

		now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
		self._fp.write(f"\n##### mp-sync session {now} #####\n\n")
		self._fp.flush()

	def write_row(self, row: dict[str, Any]) -> None:
		phase = row.get("phase")
		if phase == "done":
			self._fp.write(format_trial_readable(row))
		elif phase == "slm_packed":
			self._fp.write(format_packed_readable(row))
		else:
			return
		self._fp.flush()

	def close(self) -> None:
		self._fp.close()


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


def iter_jsonl(path: str | Path) -> list[dict[str, Any]]:
	rows: list[dict[str, Any]] = []
	with open(path, encoding="utf-8") as f:
		for line in f:
			line = line.strip()
			if not line:
				continue
			rows.append(json.loads(line))
	return rows


def format_log_table(
	rows: list[dict[str, Any]], *, phase: str | None = "done"
) -> str:
	"""Fixed-width table of trial rows. phase=None → all rows."""
	picked = [r for r in rows if phase is None or r.get("phase") == phase]
	if not picked:
		label = "all phases" if phase is None else f"phase={phase!r}"
		return f"(no rows with {label})\n"
	headers = ("trial", "pulse", "points", "power", "score", "kind")
	lines = ["  ".join(f"{h:<12}" for h in headers), "-" * 72]
	for r in picked:
		pts = ",".join(str(p) for p in r.get("point_ids") or [])
		trig = r.get("trigger_index")
		n = r.get("n_triggers")
		pulse = f"{trig}/{int(n) - 1}" if trig is not None and n else "-"
		score = r.get("score")
		score_s = f"{float(score):.4f}" if score is not None else "-"
		cols = (
			str(r.get("trial_index", r.get("phase", "-"))),
			pulse,
			pts or "-",
			str(r.get("power", "-")),
			score_s,
			str(r.get("score_kind") or r.get("phase") or "-"),
		)
		lines.append("  ".join(f"{c:<12}" for c in cols))
	lines.append("")
	for r in picked:
		if r.get("phase") == "slm_packed":
			lines.append(r.get("summary") or f"slm_packed power={r.get('power')}")
		else:
			lines.append(r.get("summary") or format_trial_summary(r))
	return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
	"""CLI: python -m prairie_live show-log [trials.jsonl]"""
	import argparse

	p = argparse.ArgumentParser(
		prog="prairie_live show-log",
		description="Print a readable table of mp-sync trials.jsonl rows",
	)
	p.add_argument(
		"path",
		nargs="?",
		default="trials.jsonl",
		help="JSONL path (default: trials.jsonl)",
	)
	p.add_argument(
		"--all",
		action="store_true",
		help="Show every phase (default: done rows only)",
	)
	p.add_argument(
		"--packed",
		action="store_true",
		help="Also print slm_packed pulse→group maps",
	)
	args = p.parse_args(argv)
	path = Path(args.path)
	if not path.is_file():
		raise SystemExit(f"not found: {path}")
	rows = iter_jsonl(path)
	print(format_log_table(rows, phase=None if args.all else "done"))
	if args.packed:
		for r in rows:
			if r.get("phase") != "slm_packed":
				continue
			print(r.get("summary") or f"packed power={r.get('power')}")
			print(format_packed_map(r.get("group_trigger_map") or []))

