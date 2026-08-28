"""Synchronized Mark Points mapping loop: groups → TTL → JSONL → regroup.

Flow per mapping iteration
--------------------------
1. Build pseudo-random groups from the points pool (or score-biased groups).
2. stim_mode=series: each (group × power) → -lmp/-mp + optional DTR.
	stim_mode=slm, slm_pack=true (default): pack all groups into one
   -MarkAllPoints string; send once; DTR once per group in pack order.
   stim_mode=slm, slm_pack=false: one -MarkAllPoints per group (trigger_index
   always 0); DTR once per trial.
3. Aggregate per-point scores; next iteration reuses top responders plus
   random fill.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from prairie_live.markpoints import (
	build_group_step,
	estimate_series_ms,
	extract_unique_points,
	groups_to_xml,
	load_mark_points_file,
	template_meta,
)
from prairie_live.prairie_link import PrairieLink
from prairie_live.tcp_backend import DEFAULT_PORT


def form_groups(
	points: list[dict],
	*,
	n_groups: int,
	group_size: int,
	scores: dict[str, float] | None = None,
	rng: random.Random,
	elite_frac: float = 0.4,
) -> list[list[dict]]:
	"""
	Partition points into n_groups of group_size.

	Without scores: shuffle then chunk (with wrap if pool is small).
	With scores: seed each group with elite responders, fill from the rest.
	"""
	# Lab rule: every stim group has at least 3 points.
	if n_groups < 1 or group_size < 3:
		raise ValueError("n_groups must be >= 1 and group_size must be >= 3")
	if not points:
		raise ValueError("empty points pool")
	if len(points) < 3:
		raise ValueError(
			f"need at least 3 points in the pool, got {len(points)}"
		)

	pool = list(points)
	if scores:
		ranked = sorted(
			pool,
			key=lambda p: scores.get(str(p["id"]), 0.0),
			reverse=True,
		)
		n_elite = max(1, int(len(ranked) * elite_frac))
		elite = ranked[:n_elite]
		rest = ranked[n_elite:]
		rng.shuffle(rest)
		# Round-robin elite into groups, then fill from rest / recycle.
		groups: list[list[dict]] = [[] for _ in range(n_groups)]
		for i, pt in enumerate(elite):
			groups[i % n_groups].append(pt)
		fill = rest + elite  # recycle allowed if pool is small
		fi = 0
		for g in groups:
			while len(g) < group_size:
				g.append(fill[fi % len(fill)])
				fi += 1
		return groups

	order = list(pool)
	rng.shuffle(order)
	groups = []
	for gi in range(n_groups):
		chunk = []
		for j in range(group_size):
			chunk.append(order[(gi * group_size + j) % len(order)])
		groups.append(chunk)
	return groups


def aggregate_point_scores(trials: list[dict]) -> dict[str, float]:
	"""Mean on-target ΔF/F (or mock score) per stimulated point_id."""
	acc: dict[str, list[float]] = {}
	for row in trials:
		score = row.get("score")
		if score is None:
			continue
		for pid in row.get("point_ids", []):
			acc.setdefault(str(pid), []).append(float(score))
	return {pid: float(np.mean(vals)) for pid, vals in acc.items()}


def disk_mean(frame: np.ndarray, x_norm: float, y_norm: float, radius: int) -> float | None:
	"""
	Mean intensity in a disk; x/y are FOV-normalized (0–1 = visible frame).

	Prairie allows Mark Points X/Y outside 0–1 (uncaging galvo range). Those
	fall off the imaging frame — return None so callers skip them.
	"""
	h, w = frame.shape[:2]
	cx = int(round(float(x_norm) * (w - 1)))
	cy = int(round(float(y_norm) * (h - 1)))
	# Entire disk must miss the frame → not scoreable on this image.
	if cx + radius < 0 or cy + radius < 0 or cx - radius >= w or cy - radius >= h:
		return None
	yy, xx = np.ogrid[:h, :w]
	mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= radius ** 2
	if not np.any(mask):
		return None
	return float(frame[mask].mean())


def score_group_dff(
	frames_f0: list[np.ndarray],
	frames_f1: list[np.ndarray],
	points: list[dict],
	radius: int = 3,
) -> float:
	"""Mean ΔF/F across group points that fall on the imaging FOV."""
	if not frames_f0 or not frames_f1:
		return 0.0
	f0 = np.mean(np.stack(frames_f0, axis=0), axis=0)
	f1 = np.mean(np.stack(frames_f1, axis=0), axis=0)
	vals = []
	n_oob = 0
	for pt in points:
		b = disk_mean(f0, pt["x"], pt["y"], radius)
		a = disk_mean(f1, pt["x"], pt["y"], radius)
		if b is None or a is None:
			n_oob += 1
			continue
		if b <= 0:
			continue
		vals.append((a - b) / b)
	if n_oob:
		print(
			f"  score: skipped {n_oob}/{len(points)} points outside imaging FOV "
			f"(X/Y not in ~0–1)"
		)
	return float(np.mean(vals)) if vals else 0.0


class JsonlLog:
	"""Append-only JSONL + plain-text twin (trials.jsonl + trials.txt)."""

	def __init__(self, path: str | Path):
		from prairie_live.trial_record import ReadableLog, readable_log_path

		self.path = Path(path)
		self.path.parent.mkdir(parents=True, exist_ok=True)
		self._fp = open(self.path, "a", encoding="utf-8")
		self.txt_path = readable_log_path(self.path)
		self._txt = ReadableLog(self.txt_path)
		self.n_written = 0

	def write(self, row: dict) -> None:
		# Keep insertion order (summary first); do not alpha-sort keys.
		from prairie_live.trial_record import order_trial_row

		slm_meta = row.get("phase") in ("slm_packed", "slm_single")
		payload = row if slm_meta else order_trial_row(row)
		self._fp.write(json.dumps(payload, ensure_ascii=False) + "\n")
		self._fp.flush()
		self._txt.write_row(payload if slm_meta else row)
		self.n_written += 1

	def close(self) -> None:
		self._fp.close()
		self._txt.close()


def write_scope_xml(groups: list[dict], path: str) -> None:
	"""Write series XML; unlink first so PV never gets a replace-file dialog."""
	xml = groups_to_xml(groups)
	parent = os.path.dirname(path)
	if parent:
		os.makedirs(parent, exist_ok=True)
	try:
		os.unlink(path)
	except FileNotFoundError:
		pass
	with open(path, "w", encoding="utf-8") as f:
		f.write(xml)


def run_sync_loop(
	*,
	points: list[dict],
	meta: dict[str, Any],
	pl: PrairieLink | None,
	log: JsonlLog,
	scope_xml: str | None,
	n_iterations: int,
	n_groups: int,
	group_size: int,
	powers: list[float],
	seed: int,
	trigger: str,
	ttl: Any | None,
	ttl_width_s: float,
	inter_trial_s: float,
	pad_ms: float,
	mock_scores: bool,
	relay: Any | None,
	via_relay: bool,
	f0_s: float,
	f1_s: float,
	frame_poll_s: float,
	disk_radius: int,
	elite_frac: float,
	dry_run: bool,
	stim_mode: str = "series",
	slm_pack: bool = True,
	images_dir: str | None = None,
	on_trial: Callable[[dict], None] | None = None,
) -> list[dict]:
	"""
	Run mapping iterations. Returns all trial rows written this session.

	stim_mode:
	  series — -LoadMarkPoints + -MarkPoints (XML group)
	  slm    — -MarkAllPoints (-slm); packed (default) or one cmd per group

	images_dir:
	  If set, save <images_dir>/<run_id>/tXXXX/{f0,f1,dff}.png per trial
	  (needs relay --grab and live frames).

	trigger:
	  none   — TriggerSelection=None; fires immediately; log t_cmd
	  serial — PFI1 (or meta); arm then DTR pulse
	  wait   — same as serial but no DTR (external TTL)

	via_relay:
	  True — push cmds through the relay (no SMB / local :1236 from analysis)
	"""
	from prairie_live.mark_all_points import stim_params_from_meta
	from prairie_live.trial_images import run_dir as make_run_dir

	rng = random.Random(seed)
	scores: dict[str, float] | None = None
	all_trials: list[dict] = []
	trial_index = 0
	mode = str(stim_mode or "series").lower()
	slm_params = stim_params_from_meta(meta) if mode == "slm" else {}
	# One folder per mp-sync invocation; trials nest underneath.
	run_root: Path | None = None
	if images_dir:
		run_root = make_run_dir(images_dir)
		print(f"trial images → {run_root}")

	trig_sel = str(meta.get("trigger_selection", "None"))
	if trigger == "none":
		trig_sel = "None"
	elif trigger in ("serial", "wait") and trig_sel in ("", "None", "none"):
		# Series / SLM wait for hardware; default PFI1 matches PackIO wiring.
		trig_sel = "PFI1"
	if mode == "slm":
		slm_params["trigger_selection"] = trig_sel

	for it in range(n_iterations):
		groups_pts = form_groups(
			points,
			n_groups=n_groups,
			group_size=group_size,
			scores=scores,
			rng=rng,
			elite_frac=elite_frac,
		)
		print(
			f"\n=== iteration {it + 1}/{n_iterations}  "
			f"groups={n_groups} size={group_size}  "
			f"scored={'yes' if scores else 'random'}  "
			f"stim_mode={mode} ==="
		)
		iter_trials: list[dict] = []

		if mode == "slm":
			for power in powers:
				if slm_pack:
					batch = _run_slm_packed_batch(
						groups_pts=groups_pts,
						power=power,
						it=it,
						trial_index_start=trial_index,
						meta=meta,
						slm_params=slm_params,
						trig_sel=trig_sel,
						trigger=trigger,
						ttl=ttl,
						ttl_width_s=ttl_width_s,
						inter_trial_s=inter_trial_s,
						pad_ms=pad_ms,
						pl=pl,
						relay=relay,
						via_relay=via_relay,
						scope_xml=scope_xml,
						log=log,
						dry_run=dry_run,
						mock_scores=mock_scores,
						f0_s=f0_s,
						f1_s=f1_s,
						frame_poll_s=frame_poll_s,
						disk_radius=disk_radius,
						run_root=run_root,
						on_trial=on_trial,
					)
					iter_trials.extend(batch)
					all_trials.extend(batch)
					trial_index += len(batch)
				else:
					for gi, gpts in enumerate(groups_pts):
						row = _run_slm_single_trial(
							gpts=gpts,
							gi=gi,
							power=power,
							it=it,
							trial_index=trial_index,
							meta=meta,
							slm_params=slm_params,
							trig_sel=trig_sel,
							trigger=trigger,
							ttl=ttl,
							ttl_width_s=ttl_width_s,
							inter_trial_s=inter_trial_s,
							pad_ms=pad_ms,
							pl=pl,
							relay=relay,
							via_relay=via_relay,
							scope_xml=scope_xml,
							log=log,
							dry_run=dry_run,
							mock_scores=mock_scores,
							f0_s=f0_s,
							f1_s=f1_s,
							frame_poll_s=frame_poll_s,
							disk_radius=disk_radius,
							run_root=run_root,
							on_trial=on_trial,
						)
						iter_trials.append(row)
						all_trials.append(row)
						trial_index += 1
		else:
			for gi, gpts in enumerate(groups_pts):
				for power in powers:
					row = _run_series_trial(
						gpts=gpts,
						gi=gi,
						power=power,
						it=it,
						trial_index=trial_index,
						meta=meta,
						trig_sel=trig_sel,
						trigger=trigger,
						ttl=ttl,
						ttl_width_s=ttl_width_s,
						inter_trial_s=inter_trial_s,
						pad_ms=pad_ms,
						pl=pl,
						relay=relay,
						via_relay=via_relay,
						scope_xml=scope_xml,
						log=log,
						dry_run=dry_run,
						mock_scores=mock_scores,
						f0_s=f0_s,
						f1_s=f1_s,
						frame_poll_s=frame_poll_s,
						disk_radius=disk_radius,
						run_root=run_root,
						on_trial=on_trial,
					)
					iter_trials.append(row)
					all_trials.append(row)
					trial_index += 1

		agg = aggregate_point_scores(iter_trials)
		if agg:
			scores = agg
			top = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:5]
			print(f"  scores (top): {top}")
		else:
			print("  no scores this iteration — next groups stay random")

	return all_trials


def _want_frames(
	*,
	relay: Any | None,
	dry_run: bool,
	mock_scores: bool,
	run_root: Path | None,
) -> bool:
	"""Grab F0/F1 when scoring for real or when saving trial PNGs."""
	if relay is None or dry_run:
		return False
	return (not mock_scores) or (run_root is not None)


def _finish_trial_frames(
	row: dict,
	*,
	gpts: list[dict],
	f0_frames: list[np.ndarray],
	f1_frames: list[np.ndarray],
	mock_scores: bool,
	relay: Any | None,
	disk_radius: int,
	run_root: Path | None,
	it: int,
) -> None:
	"""Score + optional PNG dump into <run>/tXXXX/."""
	if mock_scores:
		row["score"] = (
			abs(hash((tuple(row["point_ids"]), row["power"], it))) % 1000 / 1000.0
		)
		row["score_kind"] = "mock"
	elif relay is not None and f0_frames and f1_frames:
		try:
			row["score"] = score_group_dff(
				f0_frames, f1_frames, gpts, radius=disk_radius
			)
			row["score_kind"] = "relay_disk_dff"
		except Exception as exc:
			row["score"] = None
			row["score_kind"] = "error"
			print(f"  score failed: {exc}")
	else:
		row["score_kind"] = "none"

	if run_root is not None and f0_frames and f1_frames:
		from prairie_live.trial_images import save_trial_images

		try:
			paths = save_trial_images(
				run_root,
				trial_index=int(row["trial_index"]),
				frames_f0=f0_frames,
				frames_f1=f1_frames,
				points=gpts,
				radius=disk_radius,
			)
			row["image_paths"] = paths
			print(f"  saved images → {paths['trial_dir']}")
		except Exception as exc:
			# Stim already happened — do not abort the session on PNG errors.
			row["image_paths"] = None
			print(f"  image save failed: {exc}")
	elif run_root is not None:
		row["image_paths"] = None
		print("  images_dir set but no frames (relay --grab + Live/T-series?)")


def _finalize_trial_log(row: dict, *, log: Any, run_root: Path | None) -> None:
	"""Attach summary, write lean JSONL + pretty sidecars, print one line."""
	from prairie_live.trial_record import (
		append_run_summary,
		format_trial_summary,
		write_trial_sidecars,
	)

	row["summary"] = format_trial_summary(row)
	# Drop argv spam from per-trial rows (still on phase=slm_packed).
	row.pop("slm_parts", None)
	row.pop("group_trigger_map", None)

	trial_dir = None
	paths = row.get("image_paths") or {}
	if paths.get("trial_dir"):
		trial_dir = Path(paths["trial_dir"])
	elif run_root is not None:
		trial_dir = Path(run_root) / f"t{int(row['trial_index']):04d}"

	if trial_dir is not None:
		try:
			row["record_paths"] = write_trial_sidecars(
				{**row, "phase": "done"}, trial_dir
			)
		except Exception as exc:
			row["record_paths"] = None
			print(f"  trial record save failed: {exc}")
	if run_root is not None:
		try:
			append_run_summary(Path(run_root), row)
		except Exception as exc:
			print(f"  run summary append failed: {exc}")

	log.write({**row, "phase": "done"})
	print(f"  done: {row['summary']}")


def _run_slm_packed_batch(
	*,
	groups_pts: list[list[dict]],
	power: float,
	it: int,
	trial_index_start: int,
	meta: dict,
	slm_params: dict,
	trig_sel: str,
	trigger: str,
	ttl: Any | None,
	ttl_width_s: float,
	inter_trial_s: float,
	pad_ms: float,
	pl: PrairieLink | None,
	relay: Any | None,
	via_relay: bool,
	scope_xml: str | None,
	log: Any,
	dry_run: bool,
	mock_scores: bool,
	f0_s: float,
	f1_s: float,
	frame_poll_s: float,
	disk_radius: int,
	run_root: Path | None,
	on_trial: Callable[[dict], None] | None,
) -> list[dict]:
	"""
	One -slm command for all groups at this power; one TTL per group.

	trigger_index i (0-based) is the i-th DTR pulse and maps to groups_pts[i].
	"""
	from prairie_live.mark_all_points import pack_mark_all_points

	n = len(groups_pts)
	# PV Delay between packed sets: cover stim + pad so next PFI1 is accepted
	# only after the prior hologram finishes (software also sleeps per pulse).
	delay_ms = (
		float(slm_params.get("duration_ms") or 0)
		+ float(pad_ms)
		+ float(inter_trial_s) * 1000.0
	)
	slm_parts = pack_mark_all_points(
		groups_pts,
		power=power,
		laser=slm_params["laser"],
		duration_ms=slm_params["duration_ms"],
		use_3d=slm_params["use_3d"],
		spiral=slm_params["spiral"],
		spiral_size_um=slm_params["spiral_size_um"],
		spiral_revolutions=slm_params["spiral_revolutions"],
		fov_width_um=slm_params["fov_width_um"],
		trigger_selection=trig_sel,
		delay_ms=delay_ms,
	)
	# Audit XML: one MarkPoint step per group (same order as packed -slm).
	steps = []
	group_map: list[dict] = []
	for gi, gpts in enumerate(groups_pts):
		gname = f"PL_t{trial_index_start + gi:04d}_g{gi + 1}"
		steps.append(
			build_group_step(
				gpts,
				name=gname,
				power=power,
				meta=meta,
				trigger_selection=trig_sel,
			)
		)
		group_map.append(
			{
				"trigger_index": gi,
				"group_index": gi,
				"group_name": gname,
				"point_ids": [str(p["id"]) for p in gpts],
			}
		)
	if scope_xml:
		write_scope_xml(steps, scope_xml)

	t_cmd = time.time()
	# One session-level map so offline analysis can join pulse → group.
	log.write(
		{
			"phase": "slm_packed",
			"summary": (
				f"packed -slm · power {power} · {n} pulses · "
				+ "; ".join(
					f"p{e['trigger_index']}=[{','.join(e['point_ids'])}]"
					for e in group_map
				)
			),
			"iteration": it,
			"power": power,
			"stim_mode": "slm",
			"n_triggers": n,
			"group_trigger_map": group_map,
			"slm_parts": slm_parts,
			"t_cmd": t_cmd,
			"run_root": str(run_root) if run_root else None,
		}
	)
	if run_root is not None:
		from prairie_live.trial_record import format_packed_map

		try:
			(Path(run_root) / "pulse_map.txt").write_text(
				format_packed_map(group_map), encoding="utf-8"
			)
		except Exception as exc:
			print(f"  pulse_map.txt save failed: {exc}")

	if dry_run:
		print(
			f"  [dry] packed -slm power={power} n_groups={n} "
			f"cmd={' '.join(slm_parts)}"
		)
	else:
		_fire_mark_all_points(
			parts=slm_parts,
			wait_ms=float(slm_params.get("initial_delay_ms") or 0),
			pl=pl,
			relay=relay,
			via_relay=via_relay,
		)
		print(f"  packed -slm power={power} n_groups={n} (awaiting {n} triggers)")
		print("  pulse map:")
		for e in group_map:
			print(
				f"    pulse {e['trigger_index']}: "
				f"{e['group_name']} points={e['point_ids']}"
			)

	wait_s = (
		float(slm_params.get("duration_ms") or 0)
		+ float(slm_params.get("initial_delay_ms") or 0)
		+ pad_ms
	) / 1000.0
	want = _want_frames(
		relay=relay, dry_run=dry_run, mock_scores=mock_scores, run_root=run_root
	)
	rows: list[dict] = []
	for gi, gpts in enumerate(groups_pts):
		entry = group_map[gi]
		trial_index = trial_index_start + gi
		row: dict[str, Any] = {
			"trial_index": trial_index,
			"iteration": it,
			"group_index": gi,
			"trigger_index": gi,
			"n_triggers": n,
			"group_name": entry["group_name"],
			"point_ids": entry["point_ids"],
			"power": power,
			"trigger": trigger,
			"trigger_selection": trig_sel,
			"stim_mode": "slm",
			"t_cmd": t_cmd,
			"t_ttl": None,
			"score": None,
			"score_kind": None,
			"image_paths": None,
		}
		log.write({**row, "phase": "armed"})

		f0_frames: list[np.ndarray] = []
		f1_frames: list[np.ndarray] = []
		if want:
			f0_frames = _collect_frames(relay, f0_s, frame_poll_s)

		if trigger == "serial" and ttl is not None and not dry_run:
			# Pulse gi fires packed set gi — identity is our count, not PV.
			row["t_ttl"] = time.time()
			ttl.pulse_dtr(ttl_width_s)
			print(
				f"  trigger {gi}/{n - 1}: trial {trial_index} "
				f"pts={entry['point_ids']}"
			)
		elif trigger == "none":
			row["t_ttl"] = row["t_cmd"]
			print(
				f"  [none] trigger_index={gi} trial {trial_index} "
				f"pts={entry['point_ids']}"
			)
		else:
			# wait: external TTL; stamp when we expect pulse gi.
			row["t_ttl"] = time.time()
			print(
				f"  wait trigger {gi}/{n - 1}: trial {trial_index} "
				f"pts={entry['point_ids']}"
			)

		if want:
			f1_frames = _collect_frames(relay, max(f1_s, wait_s), frame_poll_s)
		else:
			time.sleep(max(wait_s, 0.0))

		_finish_trial_frames(
			row,
			gpts=gpts,
			f0_frames=f0_frames,
			f1_frames=f1_frames,
			mock_scores=mock_scores,
			relay=relay,
			disk_radius=disk_radius,
			run_root=run_root,
			it=it,
		)

		_finalize_trial_log(row, log=log, run_root=run_root)
		if on_trial:
			on_trial(row)
		rows.append(row)
		if inter_trial_s > 0 and gi < n - 1:
			time.sleep(inter_trial_s)
	return rows


def _run_slm_single_trial(
	*,
	gpts: list[dict],
	gi: int,
	power: float,
	it: int,
	trial_index: int,
	meta: dict,
	slm_params: dict,
	trig_sel: str,
	trigger: str,
	ttl: Any | None,
	ttl_width_s: float,
	inter_trial_s: float,
	pad_ms: float,
	pl: PrairieLink | None,
	relay: Any | None,
	via_relay: bool,
	scope_xml: str | None,
	log: Any,
	dry_run: bool,
	mock_scores: bool,
	f0_s: float,
	f1_s: float,
	frame_poll_s: float,
	disk_radius: int,
	run_root: Path | None,
	on_trial: Callable[[dict], None] | None,
) -> dict:
	"""
	One group × power via its own -MarkAllPoints (-slm); one TTL pulse.

	trigger_index is always 0 (single packed set per command).
	"""
	from prairie_live.mark_all_points import pack_mark_all_points

	slm_parts = pack_mark_all_points(
		[gpts],
		power=power,
		laser=slm_params["laser"],
		duration_ms=slm_params["duration_ms"],
		use_3d=slm_params["use_3d"],
		spiral=slm_params["spiral"],
		spiral_size_um=slm_params["spiral_size_um"],
		spiral_revolutions=slm_params["spiral_revolutions"],
		fov_width_um=slm_params["fov_width_um"],
		trigger_selection=trig_sel,
		delay_ms=0.0,
	)
	gname = f"PL_t{trial_index:04d}_g{gi + 1}"
	point_ids = [str(p["id"]) for p in gpts]
	group_map = [
		{
			"trigger_index": 0,
			"group_index": gi,
			"group_name": gname,
			"point_ids": point_ids,
		}
	]
	if scope_xml:
		write_scope_xml(
			[
				build_group_step(
					gpts,
					name=gname,
					power=power,
					meta=meta,
					trigger_selection=trig_sel,
				)
			],
			scope_xml,
		)

	t_cmd = time.time()
	log.write(
		{
			"phase": "slm_single",
			"summary": (
				f"single -slm · power {power} · "
				f"points [{','.join(point_ids)}]"
			),
			"iteration": it,
			"trial_index": trial_index,
			"power": power,
			"stim_mode": "slm",
			"slm_pack": False,
			"n_triggers": 1,
			"group_trigger_map": group_map,
			"slm_parts": slm_parts,
			"t_cmd": t_cmd,
			"run_root": str(run_root) if run_root else None,
		}
	)

	if dry_run:
		print(
			f"  [dry] single -slm trial {trial_index}: {gname} "
			f"power={power} pts={point_ids}"
		)
	else:
		_fire_mark_all_points(
			parts=slm_parts,
			wait_ms=float(slm_params.get("initial_delay_ms") or 0),
			pl=pl,
			relay=relay,
			via_relay=via_relay,
		)
		print(
			f"  single -slm trial {trial_index}: {gname} "
			f"power={power} pts={point_ids}"
		)

	wait_s = (
		float(slm_params.get("duration_ms") or 0)
		+ float(slm_params.get("initial_delay_ms") or 0)
		+ pad_ms
	) / 1000.0
	want = _want_frames(
		relay=relay, dry_run=dry_run, mock_scores=mock_scores, run_root=run_root
	)
	row: dict[str, Any] = {
		"trial_index": trial_index,
		"iteration": it,
		"group_index": gi,
		"trigger_index": 0,
		"n_triggers": 1,
		"group_name": gname,
		"point_ids": point_ids,
		"power": power,
		"trigger": trigger,
		"trigger_selection": trig_sel,
		"stim_mode": "slm",
		"slm_pack": False,
		"t_cmd": t_cmd,
		"t_ttl": None,
		"score": None,
		"score_kind": None,
		"image_paths": None,
	}
	log.write({**row, "phase": "armed"})

	f0_frames: list[np.ndarray] = []
	f1_frames: list[np.ndarray] = []
	if want:
		f0_frames = _collect_frames(relay, f0_s, frame_poll_s)

	if trigger == "serial" and ttl is not None and not dry_run:
		row["t_ttl"] = time.time()
		ttl.pulse_dtr(ttl_width_s)
		print(f"  trigger 0/0: trial {trial_index} pts={point_ids}")
	elif trigger == "none":
		row["t_ttl"] = row["t_cmd"]
		print(f"  [none] trial {trial_index} pts={point_ids}")
	else:
		row["t_ttl"] = time.time()
		print(f"  wait trigger 0/0: trial {trial_index} pts={point_ids}")

	if want:
		f1_frames = _collect_frames(relay, max(f1_s, wait_s), frame_poll_s)
	else:
		time.sleep(max(wait_s, 0.0))

	_finish_trial_frames(
		row,
		gpts=gpts,
		f0_frames=f0_frames,
		f1_frames=f1_frames,
		mock_scores=mock_scores,
		relay=relay,
		disk_radius=disk_radius,
		run_root=run_root,
		it=it,
	)

	_finalize_trial_log(row, log=log, run_root=run_root)
	if on_trial:
		on_trial(row)
	if inter_trial_s > 0:
		time.sleep(inter_trial_s)
	return row


def _run_series_trial(
	*,
	gpts: list[dict],
	gi: int,
	power: float,
	it: int,
	trial_index: int,
	meta: dict,
	trig_sel: str,
	trigger: str,
	ttl: Any | None,
	ttl_width_s: float,
	inter_trial_s: float,
	pad_ms: float,
	pl: PrairieLink | None,
	relay: Any | None,
	via_relay: bool,
	scope_xml: str | None,
	log: Any,
	dry_run: bool,
	mock_scores: bool,
	f0_s: float,
	f1_s: float,
	frame_poll_s: float,
	disk_radius: int,
	run_root: Path | None,
	on_trial: Callable[[dict], None] | None,
) -> dict:
	"""One group × power via -LoadMarkPoints / -MarkPoints."""
	gname = f"PL_t{trial_index:04d}_g{gi + 1}"
	step = build_group_step(
		gpts,
		name=gname,
		power=power,
		meta=meta,
		trigger_selection=trig_sel,
	)
	series = [step]
	point_ids = [str(p["id"]) for p in gpts]
	row: dict[str, Any] = {
		"trial_index": trial_index,
		"iteration": it,
		"group_index": gi,
		"group_name": gname,
		"point_ids": point_ids,
		"power": power,
		"trigger": trigger,
		"trigger_selection": trig_sel,
		"stim_mode": "series",
		"t_cmd": None,
		"t_ttl": None,
		"score": None,
		"score_kind": None,
		"image_paths": None,
	}
	xml = groups_to_xml(series)
	if scope_xml:
		write_scope_xml(series, scope_xml)

	row["t_cmd"] = time.time()
	log.write({**row, "phase": "armed"})

	if dry_run:
		print(f"  [dry] trial {trial_index}: {gname} power={power} pts={point_ids}")
	else:
		_fire_mark_points(
			xml=xml,
			scope_xml=scope_xml,
			pl=pl,
			relay=relay,
			via_relay=via_relay,
		)
		print(f"  trial {trial_index}: -lmp/-mp {gname} power={power} pts={point_ids}")

	want = _want_frames(
		relay=relay, dry_run=dry_run, mock_scores=mock_scores, run_root=run_root
	)
	f0_frames: list[np.ndarray] = []
	f1_frames: list[np.ndarray] = []
	if want:
		f0_frames = _collect_frames(relay, f0_s, frame_poll_s)

	if trigger == "serial" and ttl is not None and not dry_run:
		row["t_ttl"] = time.time()
		ttl.pulse_dtr(ttl_width_s)
	elif trigger == "none":
		row["t_ttl"] = row["t_cmd"]
	else:
		row["t_ttl"] = time.time()

	wait_s = (estimate_series_ms(series) + pad_ms) / 1000.0
	if want:
		f1_frames = _collect_frames(relay, max(f1_s, wait_s), frame_poll_s)
	else:
		time.sleep(max(wait_s, 0.0))

	_finish_trial_frames(
		row,
		gpts=gpts,
		f0_frames=f0_frames,
		f1_frames=f1_frames,
		mock_scores=mock_scores,
		relay=relay,
		disk_radius=disk_radius,
		run_root=run_root,
		it=it,
	)

	_finalize_trial_log(row, log=log, run_root=run_root)
	if on_trial:
		on_trial(row)
	if inter_trial_s > 0:
		time.sleep(inter_trial_s)
	return row


def _fire_mark_all_points(
	*,
	parts: list[str],
	wait_ms: float,
	pl: PrairieLink | None,
	relay: Any | None,
	via_relay: bool,
) -> None:
	"""Send -MarkAllPoints (-slm); prefer relay when via_relay."""
	if via_relay:
		if relay is None:
			raise RuntimeError("via_relay set but relay client is not connected")
		out = relay.mark_all_points(parts, wait_ms=wait_ms)
		if not out.get("ok", False):
			raise RuntimeError(f"relay mark_all_points failed: {out}")
		return
	if pl is None:
		raise RuntimeError("need PrairieLink or --via-relay for stim_mode=slm")
	# TCP path: optional -Wait then -MarkAllPoints as separate commands.
	if wait_ms and float(wait_ms) > 0:
		# PV requires integer ms for -Wait.
		pl.send_command("-Wait", str(int(round(float(wait_ms)))))
	pl.send_command(*parts)


def _fire_mark_points(
	*,
	xml: str,
	scope_xml: str | None,
	pl: PrairieLink | None,
	relay: Any | None,
	via_relay: bool,
) -> None:
	"""Load + fire one series; prefer relay push when via_relay (no SMB)."""
	if via_relay:
		if relay is None:
			raise RuntimeError("via_relay set but relay client is not connected")
		out = relay.load_mark_points(xml)
		if not out.get("ok", False):
			raise RuntimeError(f"relay load_mark_points failed: {out}")
		out = relay.mark_points()
		if not out.get("ok", False):
			raise RuntimeError(f"relay mark_points failed: {out}")
		return
	if pl is None or not scope_xml:
		raise RuntimeError("need PrairieLink + --scope-xml, or --via-relay")
	pl.send_command("-LoadMarkPoints", scope_xml)
	pl.send_command("-MarkPoints")


def _collect_frames(relay, duration_s: float, poll_s: float) -> list[np.ndarray]:
	frames: list[np.ndarray] = []
	t_end = time.monotonic() + max(duration_s, 0.0)
	while time.monotonic() < t_end:
		frm = relay.get_frame()
		if frm is not None:
			frames.append(frm)
		time.sleep(max(poll_s, 0.001))
	return frames


def main(argv: list[str] | None = None) -> None:
	from prairie_live.experiment_config import (
		apply_stim_to_meta,
		default_config_path,
		load_experiment,
		merge_cli_over_config,
	)

	# Pass 1: resolve --config without consuming the rest of the flags.
	pre = argparse.ArgumentParser(add_help=False)
	pre.add_argument("--config", "-c", default=None)
	pre_args, _rest = pre.parse_known_args(argv)
	cfg_path = pre_args.config
	if cfg_path is None and default_config_path().is_file():
		cfg_path = str(default_config_path())
	cfg: dict = {}
	if cfg_path is not None:
		cfg = load_experiment(cfg_path)
		print(f"config: {cfg_path}")

	# Pass 2: CLI defaults=None so omitted flags do not clobber experiment.json.
	p = argparse.ArgumentParser(
		description="Mark Points sync loop: random groups, TTL, JSONL, regroup"
	)
	p.add_argument(
		"--config",
		"-c",
		default=None,
		help="JSON experiment file (default: ./experiment.json if it exists)",
	)
	p.add_argument(
		"--series",
		default=None,
		help="Source MarkPoints.xml (points pool; fat nested <Point> preferred)",
	)
	p.add_argument(
		"--scope-xml",
		default=None,
		help="Writable path PrairieView loads via -LoadMarkPoints (SMB/share). "
		"Not needed with --via-relay.",
	)
	p.add_argument(
		"--via-relay",
		default=None,
		metavar="HOST[:PORT]",
		help="Push trial XML over prairie_live relay (no file share). "
		"Example: 10.33.107.147:25100",
	)
	p.add_argument("--log", default=None, help="JSONL trial log")
	p.add_argument("--host", default=None)
	p.add_argument("--port", type=int, default=None)
	p.add_argument("--password", default=None)
	p.add_argument("--iterations", type=int, default=None)
	p.add_argument("--n-groups", type=int, default=None)
	p.add_argument("--group-size", type=int, default=None)
	p.add_argument(
		"--powers",
		default=None,
		help="Comma-separated UncagingLaserPower values (UI scale: 0,75,140 — not 0–1)",
	)
	p.add_argument("--seed", type=int, default=None)
	p.add_argument(
		"--trigger",
		choices=("none", "serial", "wait"),
		default=None,
		help="none=software fire; serial=DTR after -mp; wait=external TTL",
	)
	p.add_argument("--serial", default=None, help="COM port for DTR (e.g. COM3)")
	p.add_argument("--ttl-width", type=float, default=None)
	p.add_argument("--inter-trial", type=float, default=None)
	p.add_argument("--pad-ms", type=float, default=None)
	p.add_argument("--elite-frac", type=float, default=None)
	p.add_argument(
		"--mock-scores",
		action="store_true",
		help="Fake per-trial scores so regrouping works without imaging",
	)
	p.add_argument(
		"--relay",
		default=None,
		help="host[:port] for disk ΔF/F scoring (defaults to --via-relay if set)",
	)
	p.add_argument("--f0-s", type=float, default=None, help="pre-TTL baseline window")
	p.add_argument("--f1-s", type=float, default=None, help="post-TTL response window")
	p.add_argument("--frame-poll", type=float, default=None)
	p.add_argument("--disk-radius", type=int, default=None)
	p.add_argument(
		"--images-dir",
		default=None,
		help="Save F0/F1/ΔF/F PNGs under <dir>/<run_id>/tXXXX/ "
		"(needs relay --grab + live frames)",
	)
	p.add_argument(
		"--dry-run",
		action="store_true",
		help="Write XML + JSONL only (no PrairieLink / serial)",
	)
	p.add_argument(
		"--inspect",
		action="store_true",
		help="Print points pool from --series and exit",
	)
	p.add_argument(
		"--stim-mode",
		choices=("series", "slm"),
		default=None,
		help="series=-LoadMarkPoints/-MarkPoints; slm=-MarkAllPoints simultaneous",
	)
	slm_pack_g = p.add_mutually_exclusive_group()
	slm_pack_g.add_argument(
		"--slm-pack",
		dest="slm_pack",
		action="store_true",
		default=None,
		help="stim_mode=slm: pack all groups into one -MarkAllPoints (default)",
	)
	slm_pack_g.add_argument(
		"--no-slm-pack",
		dest="slm_pack",
		action="store_false",
		help="stim_mode=slm: one -MarkAllPoints per group (trigger_index always 0)",
	)
	# Parse full argv (includes --config) so --help lists every flag once.
	args = p.parse_args(argv)

	opt = merge_cli_over_config(args, cfg)
	# Hard fallbacks when neither CLI nor config set a value.
	opt.setdefault("log", "markpoints_trials.jsonl")
	opt.setdefault("host", "127.0.0.1")
	opt.setdefault("port", DEFAULT_PORT)
	opt.setdefault("password", "0000")
	opt.setdefault("iterations", 3)
	opt.setdefault("n_groups", 2)
	opt.setdefault("group_size", 9)
	opt.setdefault("seed", 0)
	opt.setdefault("trigger", "none")
	opt.setdefault("ttl_width", 0.01)
	opt.setdefault("inter_trial", 0.5)
	opt.setdefault("pad_ms", 100.0)
	opt.setdefault("elite_frac", 0.4)
	opt.setdefault("f0_s", 0.5)
	opt.setdefault("f1_s", 1.0)
	opt.setdefault("frame_poll", 0.05)
	opt.setdefault("disk_radius", 3)
	opt.setdefault("stim_mode", "series")
	opt.setdefault("slm_pack", True)

	series = opt.get("series")
	if not series:
		raise SystemExit("need --series PATH or series in experiment.json")

	gs = int(opt["group_size"])
	if gs < 3:
		raise SystemExit(f"group_size must be >= 3 (got {gs})")

	steps = load_mark_points_file(series)
	points = extract_unique_points(steps)
	meta = apply_stim_to_meta(template_meta(steps), opt)
	powers = opt["powers"]
	src_kind = "gpl" if str(series).lower().endswith(".gpl") else "xml"
	# Point spiral size from experiment when cloning pool points.
	if opt.get("spiral_size_um") is not None:
		for pt in points:
			pt["spiral_size_um"] = str(opt["spiral_size_um"])
	if opt.get("spiral") is not None:
		for pt in points:
			pt["is_spiral"] = "True" if bool(opt["spiral"]) else "False"

	stim_mode = str(opt.get("stim_mode", "series")).lower()
	if stim_mode not in ("series", "slm"):
		raise SystemExit(f"stim_mode must be 'series' or 'slm', got {stim_mode!r}")

	# .gpl X/Y are often galvo-list coords outside imaging FOV 0–1. -slm treats
	# them as FOV fractions and fires off-screen; use -lmp/-mp like the UI.
	oob = [
		p
		for p in points
		if not (0.0 <= float(p["x"]) <= 1.0 and 0.0 <= float(p["y"]) <= 1.0)
	]
	if stim_mode == "slm" and oob:
		print(
			f"NOTE: {len(oob)}/{len(points)} points have X/Y outside 0–1 "
			"(typical of .gpl galvo lists). -MarkAllPoints would mis-read "
			"those as FOV fractions. Switching stim_mode → series "
			"(-LoadMarkPoints / -MarkPoints), AllPointsAtOnce."
		)
		stim_mode = "series"
		opt["all_points_at_once"] = True
	# Match .gpl Z / Duration when present (after config merge).
	if any(abs(float(p.get("z", 0))) > 1e-6 for p in points):
		opt["use_3d"] = True
		print("NOTE: nonzero Z on points → Use3D=True")
	gpl_durs = [p.get("duration_ms") for p in points if p.get("duration_ms")]
	if gpl_durs and float(opt.get("duration_ms") or 0) in (0.0, 16.92):
		opt["duration_ms"] = float(gpl_durs[0])
		print(f"NOTE: using Duration={opt['duration_ms']} ms from .gpl")
	meta = apply_stim_to_meta(meta, opt)

	print(
		f"points pool: {len(points)} from {src_kind}  powers={powers}  "
		f"trigger={opt['trigger']}  stim_mode={stim_mode}  "
		f"laser={meta.get('uncaging_laser')}  group_size={gs}"
	)
	# Flag .gpl / XML coords that leave the visible FOV (valid for -slm galvos,
	# but disk ΔF/F and PNG marks only work for ~0–1).
	if oob:
		print(
			f"WARNING: {len(oob)}/{len(points)} points have X/Y outside 0–1 "
			"(ok for .gpl/-lmp; imaging-FOV scoring skips them). Examples:"
		)
		for p in oob[:5]:
			print(f"  Point {p['id']}  ({p['x']:.4f}, {p['y']:.4f})")
	if stim_mode == "slm":
		# Spiral µm→FOV fraction needs optical FOV width (not in MarkPoints XML).
		spiral_on = str(meta.get("is_spiral", "True")).lower() in ("true", "1", "yes")
		if spiral_on and meta.get("fov_width_um") in (None, ""):
			raise SystemExit(
				"stim_mode=slm with spiral requires fov_width_um in experiment.json "
				"(µm across the FOV; used to convert spiral_size_um → -slm fraction)"
			)
		pack = bool(opt.get("slm_pack", True))
		if pack:
			print(
				"stim_mode=slm slm_pack=true → one packed -MarkAllPoints per power "
				"(DTR once per group; series XML if scope_xml set)"
			)
		else:
			print(
				"stim_mode=slm slm_pack=false → one -MarkAllPoints per group×power "
				"(DTR once per trial; trigger_index=0)"
			)
	if opt.get("inspect"):
		zero = 0
		oob_n = 0
		for pt in points:
			print(
				f"  Point {pt['id']}  ({pt['x']:.4f}, {pt['y']:.4f}, "
				f"{pt.get('z', 0)})"
			)
			if float(pt.get("x", 0)) == 0.0 and float(pt.get("y", 0)) == 0.0:
				zero += 1
			if not (
				0.0 <= float(pt["x"]) <= 1.0 and 0.0 <= float(pt["y"]) <= 1.0
			):
				oob_n += 1
		if zero:
			print(
				f"WARNING: {zero}/{len(points)} points at (0,0) — "
				"file may be slim/group-name-only or a bad copy. "
				"Prefer a fat MarkPoints.xml with nested <Point X Y Z>."
			)
		if oob_n:
			print(
				f"WARNING: {oob_n}/{len(points)} points have X/Y outside 0–1. "
				"Those land outside the visible imaging FOV — you will not see "
				"spots on the PV display. Re-export points from the UI while "
				"looking at the FOV, or fix the .gpl/.xml."
			)
		elif not zero:
			print(
				f"OK: {len(points)} points with X/Y inside visible FOV (0–1)."
			)
		return

	via_relay = bool(opt.get("via_relay"))
	# series needs a writable XML path or relay; slm only needs relay or TCP.
	if not opt.get("dry_run") and not via_relay:
		if stim_mode == "series" and not opt.get("scope_xml"):
			raise SystemExit(
				"need --via-relay HOST:PORT or --scope-xml PATH (or config)"
			)

	ttl = None
	pl = None
	relay = None
	log_path = str(opt["log"])
	log = JsonlLog(log_path)
	try:
		if opt["trigger"] == "serial" and not opt.get("dry_run"):
			if not opt.get("serial"):
				raise SystemExit("--trigger serial requires --serial COMx (or config)")
			from prairie_live.ttl_serial import SerialTtl

			ttl = SerialTtl(str(opt["serial"]))

		relay_addr = opt.get("via_relay") or opt.get("relay")
		if relay_addr and not opt.get("dry_run"):
			from prairie_live.relay_client import RelayClient

			host, _, port = str(relay_addr).partition(":")
			relay = RelayClient(host, int(port or 25100))
			relay.connect()

		# Direct PrairieLink only when not pushing XML through the relay.
		if not opt.get("dry_run") and not via_relay:
			pl = PrairieLink(
				host=str(opt["host"]),
				port=int(opt["port"]),
				password=str(opt["password"]),
			)

		run_sync_loop(
			points=points,
			meta=meta,
			pl=pl,
			log=log,
			scope_xml=opt.get("scope_xml"),
			n_iterations=int(opt["iterations"]),
			n_groups=int(opt["n_groups"]),
			group_size=int(opt["group_size"]),
			powers=powers,
			seed=int(opt["seed"]),
			trigger=str(opt["trigger"]),
			ttl=ttl,
			ttl_width_s=float(opt["ttl_width"]),
			inter_trial_s=float(opt["inter_trial"]),
			pad_ms=float(opt["pad_ms"]),
			mock_scores=bool(opt.get("mock_scores")),
			relay=relay,
			via_relay=via_relay,
			f0_s=float(opt["f0_s"]),
			f1_s=float(opt["f1_s"]),
			frame_poll_s=float(opt["frame_poll"]),
			disk_radius=int(opt["disk_radius"]),
			elite_frac=float(opt["elite_frac"]),
			dry_run=bool(opt.get("dry_run")),
			stim_mode=stim_mode,
			slm_pack=bool(opt.get("slm_pack", True)),
			images_dir=opt.get("images_dir"),
		)
	finally:
		log.close()
		if ttl is not None:
			ttl.close()
		if relay is not None:
			relay.disconnect()
		if pl is not None:
			pl.close()
		print(f"log: {log_path}")


if __name__ == "__main__":
	main()
