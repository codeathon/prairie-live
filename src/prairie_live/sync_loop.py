"""Synchronized Mark Points mapping loop: groups → TTL → JSONL → regroup.

Flow per mapping iteration
--------------------------
1. Build pseudo-random groups from the points pool (or score-biased groups).
2. stim_mode=series: each (group × power) → -lmp/-mp + optional DTR.
   stim_mode=slm + serial: one group at a time — arm -MarkAllPoints
   (Trig In 1), one DTR, burn, restore LiveScan, next group.
   Optional slm_pack=true packs groups (not used with serial / GUI).
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
from prairie_live.trial_images import disk_mean


def form_groups(
	points: list[dict],
	*,
	n_groups: int,
	group_size: int,
	scores: dict[str, float] | None = None,
	rng: random.Random,
	elite_frac: float = 0.4,
	cover_all: bool = False,
) -> list[list[dict]]:
	"""
	Partition points into n_groups of group_size.

	cover_all:
	  Unique partition — every point once, each group >= group_size (extras
	  make some groups 4+). No wrap, no duplicate runs.
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
	if cover_all:
		from prairie_live.gui_session import group_sizes_for_pool

		sizes = group_sizes_for_pool(
			len(pool), min_size=group_size, n_groups=n_groups
		)
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
			order = elite + rest
		else:
			order = list(pool)
			rng.shuffle(order)
		groups = []
		i = 0
		for sz in sizes:
			groups.append(order[i : i + sz])
			i += sz
		return groups

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
	"""Mean on-target ΔF/F per point (prefers per-id point_scores)."""
	acc: dict[str, list[float]] = {}
	for row in trials:
		ps = row.get("point_scores")
		if isinstance(ps, dict) and ps:
			for pid, val in ps.items():
				acc.setdefault(str(pid), []).append(float(val))
			continue
		score = row.get("score")
		if score is None:
			continue
		for pid in row.get("point_ids", []):
			acc.setdefault(str(pid), []).append(float(score))
	return {pid: float(np.mean(vals)) for pid, vals in acc.items()}


def score_points_dff(
	frames_f0: list[np.ndarray],
	frames_f1: list[np.ndarray],
	points: list[dict],
	radius: int = 3,
) -> dict[str, float]:
	"""Per-point disk ΔF/F from the existing f0/f1 grab (omit OOB / dead baseline)."""
	if not frames_f0 or not frames_f1:
		return {}
	f0 = np.mean(np.stack(frames_f0, axis=0), axis=0)
	f1 = np.mean(np.stack(frames_f1, axis=0), axis=0)
	out: dict[str, float] = {}
	n_oob = 0
	for i, pt in enumerate(points):
		b = disk_mean(f0, pt["x"], pt["y"], radius)
		a = disk_mean(f1, pt["x"], pt["y"], radius)
		if b is None or a is None:
			n_oob += 1
			continue
		if b <= 0:
			continue
		out[str(pt.get("id", i))] = float((a - b) / b)
	if n_oob:
		print(
			f"  score: skipped {n_oob}/{len(points)} points outside the imaging FOV"
		)
	return out


def score_group_dff(
	frames_f0: list[np.ndarray],
	frames_f1: list[np.ndarray],
	points: list[dict],
	radius: int = 3,
) -> float:
	"""Mean of per-point f0/f1 disk ΔF/F (same grab as score_points_dff)."""
	vals = score_points_dff(frames_f0, frames_f1, points, radius=radius)
	return float(np.mean(list(vals.values()))) if vals else 0.0


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

		slm_meta = row.get("phase") in ("slm_packed", "slm_single", "recommendation")
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
	slm_pack: bool = False,
	images_dir: str | None = None,
	visual_cfg: Any | None = None,
	on_trial: Callable[[dict], None] | None = None,
	stim_dff_min: float = 0.0,
	show_candidate_gui: bool = True,
	should_stop: Callable[[], bool] | None = None,
	cover_all: bool = False,
) -> list[dict]:
	"""
	Run mapping iterations. Returns all trial rows written this session.

	stim_mode:
	  series — -LoadMarkPoints + -MarkPoints (XML group)
	  slm    — -MarkAllPoints (-slm); packed (default) or one cmd per group

	images_dir:
	  If set, save <images_dir>/<run_id>/tXXXX_pYYY/{f0,f1,dff}.png per trial
	  (needs relay --grab and live frames).

	trigger:
	  none   — TriggerSelection=None; fires immediately; log t_cmd
	  serial — Trig In 1 (or meta); arm then DTR pulse
	  wait   — same as serial but no DTR (external TTL)

	via_relay:
	  True — push cmds through the relay (no SMB / local :1236 from analysis)
	"""
	from prairie_live.mark_all_points import stim_params_from_meta
	from prairie_live.trial_images import run_dir as make_run_dir
	from prairie_live.visual_stim import VisualConfig, open_visual_stim, visual_config_from_dict

	if isinstance(visual_cfg, dict):
		vcfg = visual_config_from_dict(visual_cfg)
	elif visual_cfg is not None:
		vcfg = visual_cfg
	else:
		vcfg = VisualConfig()
	visual_stim = open_visual_stim(vcfg)

	rng = random.Random(seed)
	scores: dict[str, float] | None = None
	all_trials: list[dict] = []
	# 1-based so the GUI shows trial 1/N … N/N (not 0/N … N-1/N).
	trial_index = 1
	mode = str(stim_mode or "series").lower()
	slm_params = stim_params_from_meta(meta) if mode == "slm" else {}
	# One folder per mp-sync invocation; trials nest underneath.
	run_root: Path | None = None
	if images_dir:
		run_root = make_run_dir(images_dir)
		print(f"trial images → {run_root}")

	from prairie_live.lab_defaults import (
		DEFAULT_TRIGGER_FREQUENCY,
		DEFAULT_TRIGGER_SELECTION,
		normalize_trigger_selection,
		trigger_identifier_for,
	)

	trig_sel = str(meta.get("trigger_selection", "None"))
	if trigger == "none":
		trig_sel = "None"
		meta["trigger_selection"] = "None"
		meta["trigger_frequency"] = "None"
		# File often still has Trig In 1's GUID — that + Frequency=None crashes PV.
		meta["trigger_identifier"] = "00000000-0000-0000-0000-000000000000"
	elif trigger in ("serial", "wait"):
		# Intermediate XML / -slm tokens must use the Bruker BNC name + GUID.
		trig_sel = normalize_trigger_selection(trig_sel) or DEFAULT_TRIGGER_SELECTION
		meta["trigger_selection"] = trig_sel
		meta["trigger_identifier"] = trigger_identifier_for(
			trig_sel, meta.get("trigger_identifier")
		)
		# One DTR pulse releases the whole group (matches PV Wait for Trigger).
		if str(meta.get("trigger_frequency") or "None") in ("None", "none", ""):
			meta["trigger_frequency"] = DEFAULT_TRIGGER_FREQUENCY
	if mode == "slm":
		slm_params["trigger_selection"] = trig_sel

	stopped = False

	def _stop() -> bool:
		nonlocal stopped
		if bool(should_stop and should_stop()):
			stopped = True
			return True
		return False

	def _abort_pv_on_stop() -> None:
		"""Cancel armed -slm / -mp on the scope when the GUI hits Stop."""
		if dry_run:
			return
		try:
			from prairie_live.pv_cleanup import abort_and_clear_stim

			if via_relay and relay is not None:
				out = abort_and_clear_stim(relay)
			elif pl is not None:
				out = abort_and_clear_stim(pl)
			else:
				return
			print("PV stop clear:", ", ".join(out.get("notes") or []))
		except Exception as exc:
			print(f"PV stop clear failed: {exc}")

	try:
		for it in range(n_iterations):
			if _stop():
				print("session stopped")
				break
			groups_pts = form_groups(
				points,
				n_groups=n_groups,
				group_size=group_size,
				scores=scores,
				rng=rng,
				elite_frac=elite_frac,
				cover_all=cover_all,
			)
			print(
				f"\n=== iteration {it + 1}/{n_iterations}  "
				f"groups={n_groups} size={group_size}  "
				f"scored={'yes' if scores else 'random'}  "
				f"stim_mode={mode} ==="
			)
			iter_trials: list[dict] = []

			if mode == "slm":
				# Simultaneous group burns via AllPointsAtOnce (-lmp/-mp), not
				# -MarkAllPoints. Galvo-saved XML + -slm FOV fractions misplaced
				# burns on this rig (off the cyan rings / only a subset visible).
				# APA uses the same galvo X/Y as series so the whole group lands
				# on the overlay at once; one DTR per group when trigger=serial.
				_ = slm_pack  # packed -MarkAllPoints retired; APA path only
				print(
					"stim_mode=slm → AllPointsAtOnce (-LoadMarkPoints/-MarkPoints) "
					"per group (simultaneous multi-spot on galvo FOV)"
				)
				for power in powers:
					if _stop():
						print("session stopped")
						break
					for gi, gpts in enumerate(groups_pts):
						if _stop():
							print("session stopped")
							break
						print(
							f"  group {gi + 1}/{len(groups_pts)}: simultaneous "
							f"APA pts={[str(p['id']) for p in gpts]} power={power}"
						)
						try:
							row = _run_series_trial(
								gpts=gpts,
								point_pool=points,
								gi=gi,
								n_groups=len(groups_pts),
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
								visual_stim=visual_stim,
								visual_cfg=vcfg,
								rng=rng,
								simultaneous=True,
							)
						except Exception as exc:
							# Dialog / timeout: Abort and STOP. Continuing to the
							# next group queues leftover -lmp/-mp that keep firing
							# after the operator restarts PV.
							print(
								f"  ERROR: group {gi + 1}/{len(groups_pts)} "
								f"failed ({exc}); clearing PV and stopping session "
								f"(no leftover groups)"
							)
							from prairie_live.pv_cleanup import abort_and_clear_stim

							if relay is not None and hasattr(relay, "abort"):
								abort_and_clear_stim(relay, settle_s=0.5)
							elif pl is not None:
								try:
									pl.send_command("-Silent", "-Abort")
									pl.send_command("-LiveScan")
								except Exception:
									pass
							# finally: closes visual_stim; do not queue more groups.
							return all_trials
						iter_trials.append(row)
						all_trials.append(row)
						trial_index += 1
			else:
				# Full FOV at each power (same order as SLM): every point × every power.
				for power in powers:
					if _stop():
						print("session stopped")
						break
					for gi, gpts in enumerate(groups_pts):
						if _stop():
							print("session stopped")
							break
						row = _run_series_trial(
							gpts=gpts,
							point_pool=points,
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
							visual_stim=visual_stim,
							visual_cfg=vcfg,
							rng=rng,
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
	finally:
		if stopped:
			_abort_pv_on_stop()
		visual_stim.close()

	from prairie_live.candidate_view import maybe_show_candidates
	from prairie_live.candidates import write_session_candidates
	from prairie_live.trial_record import write_session_recommendation

	write_session_recommendation(all_trials, log=log, run_root=run_root)
	out_dir = run_root if run_root is not None else Path(log.path).parent
	ranked = write_session_candidates(
		all_trials,
		points,
		meta=meta,
		stim_dff_min=float(stim_dff_min),
		log=log,
		run_root=run_root,
		out_dir=out_dir,
	)
	fov = _last_f0_from_trials(all_trials)
	xml_out = Path(out_dir) / "next_series.xml"
	maybe_show_candidates(
		ranked,
		meta=meta,
		fov=fov,
		out_xml=xml_out,
		show=bool(show_candidate_gui) and not dry_run,
	)
	return all_trials


def _last_f0_from_trials(trials: list[dict]) -> np.ndarray | None:
	for row in reversed(trials):
		paths = row.get("image_paths") or {}
		p = paths.get("f0")
		if not p:
			continue
		try:
			from matplotlib.image import imread

			arr = imread(p)
			if arr.ndim == 3:
				return arr[..., 0]
			return arr
		except Exception:
			continue
	return None


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


def _run_stim_epoch(
	row: dict,
	*,
	trigger: str,
	ttl: Any | None,
	ttl_width_s: float,
	dry_run: bool,
	visual_stim: Any,
	visual_cfg: Any,
	rng: random.Random,
	relay: Any | None,
	want: bool,
	f0_s: float,
	f1_s: float,
	frame_poll_s: float,
	wait_s: float,
	trigger_msg: str = "",
	n_pulses: int = 1,
	wait_between_pulses_s: float = 0.0,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
	"""
	Visual + TTL epoch: legacy RTS-held grating with DTR at visual_lead_ms.

	When visual is off: f0 → DTR (× n_pulses, matching per-point FirstRepetition
	in a PV-saved series) → f1.
	"""
	f0_frames: list[np.ndarray] = []
	f1_frames: list[np.ndarray] = []
	n_pulses = max(int(n_pulses), 1)

	if visual_cfg.enabled:
		params = visual_stim.pick_trial(rng)
		row["visual_ori_deg"] = params.visual_ori_deg
		row["visual_contrast_pct"] = params.visual_contrast_pct
		row["visual_stim_index"] = params.visual_stim_index
		dtr_width = visual_cfg.dtr_width_s
		lead_frame = visual_cfg.lead_frame
		dtr_fired = False

		visual_stim.run_isi()

		def on_rts_on() -> None:
			if ttl is not None and not dry_run:
				row["t_rts_mono"] = ttl.set_rts(True)

		def on_rts_off() -> None:
			if ttl is not None and not dry_run:
				ttl.set_rts(False)

		def on_frame(frame: int) -> None:
			nonlocal dtr_fired
			if (
				frame == lead_frame
				and trigger == "serial"
				and ttl is not None
				and not dry_run
				and not dtr_fired
			):
				row["t_ttl"] = time.time()
				row["t_ttl_mono"] = ttl.pulse_dtr(dtr_width)
				dtr_fired = True
				if trigger_msg:
					print(trigger_msg)
			elif trigger == "none" and frame == lead_frame and not dtr_fired:
				row["t_ttl"] = row.get("t_cmd")
				dtr_fired = True
				if trigger_msg:
					print(trigger_msg)
			elif trigger == "wait" and frame == lead_frame and not dtr_fired:
				row["t_ttl"] = time.time()
				dtr_fired = True
				if trigger_msg:
					print(trigger_msg)

		def poll() -> None:
			if not want or relay is None:
				return
			frm = relay.get_frame()
			if frm is None:
				return
			if not dtr_fired:
				f0_frames.append(frm)
			else:
				f1_frames.append(frm)

		visual_stim.run_grating_epoch(
			params,
			on_rts_on=on_rts_on,
			on_frame=on_frame,
			on_rts_off=on_rts_off,
			poll=poll,
		)
		if want and relay is not None:
			f1_frames.extend(_collect_frames(relay, max(f1_s, wait_s), frame_poll_s))
		elif wait_s > 0:
			time.sleep(max(wait_s, 0.0))
		return f0_frames, f1_frames

	if want:
		f0_frames = _collect_frames(relay, f0_s, frame_poll_s)

	gap = max(float(wait_between_pulses_s), 0.0)
	for i in range(n_pulses):
		msg = trigger_msg
		if n_pulses > 1 and trigger_msg:
			msg = f"{trigger_msg}  (pulse {i}/{n_pulses - 1})"
		if trigger == "serial" and ttl is not None and not dry_run:
			row["t_ttl"] = time.time()
			t_mono = ttl.pulse_dtr(ttl_width_s)
			if i == 0:
				row["t_ttl_mono"] = t_mono
			if msg:
				print(msg)
		elif trigger == "none":
			row["t_ttl"] = row.get("t_cmd")
			if msg and i == 0:
				print(msg)
		else:
			row["t_ttl"] = time.time()
			if msg and i == 0:
				print(msg)
		# Let this point finish before the next FirstRepetition wait.
		if i < n_pulses - 1 and gap > 0:
			if want and relay is not None:
				f1_frames.extend(_collect_frames(relay, gap, frame_poll_s))
			else:
				time.sleep(gap)

	if want:
		f1_frames.extend(_collect_frames(relay, max(f1_s, wait_s), frame_poll_s))
	elif wait_s > 0:
		time.sleep(max(wait_s, 0.0))
	return f0_frames, f1_frames


def _image_points(
	gpts: list[dict],
	point_pool: list[dict] | None,
	relay: Any | None,
) -> tuple[list[dict], list[dict] | None]:
	"""Map galvo Mark Points onto the imaging FOV for ΔF/F disks and PNG rings."""
	from prairie_live.scan_geometry import (
		fetch_scan_geometry,
		is_fraction_space,
		points_for_image,
	)

	if is_fraction_space(gpts):
		return gpts, point_pool
	geom = None
	if relay is not None:
		try:
			geom = fetch_scan_geometry(relay)
		except Exception as exc:
			print(f"  score: cannot read PV scan range ({exc}); galvo points stay OOB")
	if geom is None:
		return gpts, point_pool
	return points_for_image(gpts, geom), points_for_image(point_pool, geom)


def _finish_trial_frames(
	row: dict,
	*,
	gpts: list[dict],
	point_pool: list[dict] | None,
	f0_frames: list[np.ndarray],
	f1_frames: list[np.ndarray],
	mock_scores: bool,
	relay: Any | None,
	disk_radius: int,
	run_root: Path | None,
	it: int,
) -> None:
	"""Score + optional PNG dump into <run>/tXXXX/ (f0/f1 disk ΔF/F)."""
	row.setdefault("repetitions", 1)
	score_pts, _pool_pts = _image_points(gpts, point_pool, relay)
	if mock_scores:
		row["point_scores"] = {
			str(p["id"]): abs(hash((str(p["id"]), row.get("power"), it))) % 1000 / 1000.0
			for p in gpts
		}
		row["score"] = float(np.mean(list(row["point_scores"].values())))
		row["score_kind"] = "mock"
	elif relay is not None and f0_frames and f1_frames:
		try:
			row["point_scores"] = score_points_dff(
				f0_frames, f1_frames, score_pts, radius=disk_radius
			)
			vals = list(row["point_scores"].values())
			row["score"] = float(np.mean(vals)) if vals else 0.0
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
				fired_points=score_pts,
				power=float(row["power"]) if row.get("power") is not None else None,
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
		from prairie_live.trial_images import trial_dir_name

		pwr = row.get("power")
		power = float(pwr) if pwr is not None else None
		trial_dir = Path(run_root) / trial_dir_name(int(row["trial_index"]), power)

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
	point_pool: list[dict],
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
	visual_stim: Any,
	visual_cfg: Any,
	rng: random.Random,
) -> list[dict]:
	"""
	One -slm command for all groups at this power; one TTL per group.

	trigger_index i (0-based) is the i-th DTR pulse and maps to groups_pts[i].
	"""
	from prairie_live.mark_all_points import pack_mark_all_points

	n = len(groups_pts)
	# PV Delay between packed sets: must cover the hologram + a settle so the
	# next Trig In 1 wait is armed before the next DTR (too-short Delay → only
	# the first group burns). Software sleeps the same gap between pulses.
	delay_ms = (
		float(slm_params.get("duration_ms") or 0)
		+ float(pad_ms)
		+ float(inter_trial_s) * 1000.0
		+ 250.0  # settle after burn before next wait-for-trigger
	)
	# One LiveScan + geometry read for the whole packed command (same FOV).
	flat = [p for g in groups_pts for p in g]
	fire_flat, spiral_fov = _slm_fire_points(
		flat,
		relay=relay,
		pl=pl,
		via_relay=via_relay,
		slm_params=slm_params,
		dry_run=dry_run,
		log_prefix="  ",
	)
	fire_groups: list[list[dict]] = []
	i = 0
	for gpts in groups_pts:
		n_g = len(gpts)
		fire_groups.append(fire_flat[i : i + n_g])
		i += n_g
	# -slm wants UI UncagingLaserPower (160), not series XML fraction (0.4).
	# trigger_count=1: one Trig In 1 edge releases each packed hologram/group.
	slm_pwr = _slm_script_power(power, meta)
	print(f"  SLM power (UI scale) → -slm {slm_pwr:g}")
	slm_parts = pack_mark_all_points(
		fire_groups,
		power=slm_pwr,
		laser=slm_params["laser"],
		duration_ms=slm_params["duration_ms"],
		use_3d=slm_params["use_3d"],
		spiral=slm_params["spiral"],
		spiral_size_um=slm_params["spiral_size_um"],
		spiral_revolutions=slm_params["spiral_revolutions"],
		fov_width_um=slm_params["fov_width_um"],
		spiral_size_fov=spiral_fov,
		trigger_selection=trig_sel,
		trigger_count=1,
		delay_ms=delay_ms,
	)
	# Audit XML: one MarkPoint step per group (same order as packed -slm).
	steps = []
	group_map: list[dict] = []
	groups_pts = fire_groups
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

	wait_s = (
		float(slm_params.get("duration_ms") or 0)
		+ float(slm_params.get("initial_delay_ms") or 0)
		+ pad_ms
	) / 1000.0
	# PV Delay between packed sets — gap between Trig In 1 edges.
	gap_s = max(float(delay_ms) / 1000.0, wait_s)
	mp_timeout = max(180.0, gap_s * n + 60.0)
	want = _want_frames(
		relay=relay, dry_run=dry_run, mock_scores=mock_scores, run_root=run_root
	)
	# Serial: pulse every Trig In 1 *while* -slm blocks (same burn fix as series).
	dtr_during_slm = (
		not dry_run
		and trigger == "serial"
		and ttl is not None
		and not visual_cfg.enabled
	)
	pulse_times: list[float] = []
	f0_shared: list = []

	if dry_run:
		print(
			f"  [dry] packed -slm power={power} n_groups={n} "
			f"cmd={' '.join(slm_parts)}"
		)
	elif dtr_during_slm:
		from prairie_live.stim_fire import run_while_pulsing_dtr

		init_ms = float(slm_params.get("initial_delay_ms") or 0)
		_pre_stim_delay(wait_ms=init_ms, pl=pl, via_relay=via_relay)

		def _do_packed() -> None:
			_fire_mark_all_points(
				parts=slm_parts,
				wait_ms=0.0,
				pl=pl,
				relay=relay,
				via_relay=via_relay,
				no_wait=False,
				timeout=mp_timeout,
			)

		def _on_pulse(i: int) -> None:
			pulse_times.append(time.time())
			entry = group_map[i]
			print(
				f"  trigger {i}/{n - 1}: trial {trial_index_start + i} "
				f"pts={entry['point_ids']}"
			)

		# Timing: one planned DTR per group, spaced by PV Delay (gap_s).
		# No rescue pulses — an extra edge would release the next group early.
		if hasattr(ttl, "set_dtr"):
			ttl.set_dtr(False)
		print(
			f"  packed -slm power={power} (-slm {slm_pwr:g}) n_groups={n} "
			f"(1 DTR/group, gap={gap_s:.3f}s, Delay={delay_ms:.0f}ms)"
		)
		print("  pulse map:")
		for e in group_map:
			print(
				f"    pulse {e['trigger_index']}/{n - 1}: "
				f"{e['group_name']} points={e['point_ids']}"
			)
		# f0 while Live is still on — -slm often kills LiveScan.
		if want:
			f0_shared = _collect_frames(relay, f0_s, frame_poll_s)
		run_while_pulsing_dtr(
			_do_packed,
			ttl=ttl,
			n_pulses=n,
			ttl_width_s=ttl_width_s,
			gap_s=gap_s if n > 1 else 0.0,
			arm_delay_s=0.4,
			rescue_window_s=0.0,
			join_timeout_s=mp_timeout,
			on_pulse=_on_pulse,
		)
		if hasattr(ttl, "set_dtr"):
			ttl.set_dtr(False)
		_ensure_live_after_stim(relay=relay, pl=pl, via_relay=via_relay)
	else:
		if want and not dry_run:
			f0_shared = _collect_frames(relay, f0_s, frame_poll_s)
		_fire_mark_all_points(
			parts=slm_parts,
			wait_ms=float(slm_params.get("initial_delay_ms") or 0),
			pl=pl,
			relay=relay,
			via_relay=via_relay,
			no_wait=False,
			timeout=mp_timeout,
		)
		print(f"  packed -slm power={power} n_groups={n} (awaiting {n} triggers)")
		print("  pulse map:")
		for e in group_map:
			print(
				f"    pulse {e['trigger_index']}: "
				f"{e['group_name']} points={e['point_ids']}"
			)
		if not dry_run:
			_ensure_live_after_stim(relay=relay, pl=pl, via_relay=via_relay)

	rows: list[dict] = []
	f1_shared: list = []
	if want and not dry_run:
		f1_shared = _collect_frames(relay, max(f1_s, 0.2), frame_poll_s)
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
			"repetitions": int(meta.get("repetitions", 1)),
		}
		log.write({**row, "phase": "armed"})

		if trigger == "serial":
			trigger_msg = (
				f"  trigger {gi}/{n - 1}: trial {trial_index} "
				f"pts={entry['point_ids']}"
			)
		elif trigger == "none":
			trigger_msg = (
				f"  [none] trigger_index={gi} trial {trial_index} "
				f"pts={entry['point_ids']}"
			)
		else:
			trigger_msg = (
				f"  wait trigger {gi}/{n - 1}: trial {trial_index} "
				f"pts={entry['point_ids']}"
			)

		f0_frames: list = list(f0_shared) if f0_shared else []
		f1_frames: list = list(f1_shared) if f1_shared else []
		if dtr_during_slm:
			# DTRs already fired during -slm; do not pulse again.
			if gi < len(pulse_times):
				row["t_ttl"] = pulse_times[gi]
		else:
			f0_frames, f1_frames = _run_stim_epoch(
				row,
				trigger=trigger,
				ttl=ttl,
				ttl_width_s=ttl_width_s,
				dry_run=dry_run,
				visual_stim=visual_stim,
				visual_cfg=visual_cfg,
				rng=rng,
				relay=relay,
				want=want,
				f0_s=f0_s,
				f1_s=f1_s,
				frame_poll_s=frame_poll_s,
				wait_s=wait_s,
				trigger_msg=trigger_msg,
			)
			if trigger_msg and not visual_cfg.enabled and trigger != "serial":
				print(trigger_msg)

		_finish_trial_frames(
			row,
			gpts=gpts,
			point_pool=point_pool,
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
		if inter_trial_s > 0 and gi < n - 1 and not dtr_during_slm:
			time.sleep(inter_trial_s)
	return rows


def _run_slm_single_trial(
	*,
	gpts: list[dict],
	point_pool: list[dict],
	gi: int,
	n_groups: int = 1,
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
	visual_stim: Any,
	visual_cfg: Any,
	rng: random.Random,
) -> dict:
	"""
	One group × power via its own -MarkAllPoints (-slm); one TTL pulse.

	``trigger_index`` / ``n_triggers`` are the group index among this power's
	groups (so logs show pulse 0/1, 1/1 — not 0/0 for every trial).
	"""
	from prairie_live.mark_all_points import pack_mark_all_points

	n_groups = max(int(n_groups), 1)
	# Remap with live scan range so -slm X/Y match the cyan overlay rings.
	fire_pts, spiral_fov = _slm_fire_points(
		gpts,
		relay=relay,
		pl=pl,
		via_relay=via_relay,
		slm_params=slm_params,
		dry_run=dry_run,
	)
	slm_pwr = _slm_script_power(power, meta)
	print(f"  SLM power (UI scale) → -slm {slm_pwr:g}")
	slm_parts = pack_mark_all_points(
		[fire_pts],
		power=slm_pwr,
		laser=slm_params["laser"],
		duration_ms=slm_params["duration_ms"],
		use_3d=slm_params["use_3d"],
		spiral=slm_params["spiral"],
		spiral_size_um=slm_params["spiral_size_um"],
		spiral_revolutions=slm_params["spiral_revolutions"],
		fov_width_um=slm_params["fov_width_um"],
		spiral_size_fov=spiral_fov,
		trigger_selection=trig_sel,
		trigger_count=1,
		delay_ms=0.0,
	)
	gname = f"PL_t{trial_index:04d}_g{gi + 1}"
	point_ids = [str(p["id"]) for p in fire_pts]
	group_map = [
		{
			"trigger_index": gi,
			"group_index": gi,
			"group_name": gname,
			"point_ids": point_ids,
		}
	]
	if scope_xml:
		write_scope_xml(
			[
				build_group_step(
					fire_pts,
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
			"n_triggers": n_groups,
			"group_trigger_map": group_map,
			"slm_parts": slm_parts,
			"t_cmd": t_cmd,
			"run_root": str(run_root) if run_root else None,
		}
	)

	wait_s = (
		float(slm_params.get("duration_ms") or 0)
		+ float(slm_params.get("initial_delay_ms") or 0)
		+ pad_ms
	) / 1000.0
	want = _want_frames(
		relay=relay, dry_run=dry_run, mock_scores=mock_scores, run_root=run_root
	)
	# Score/PNG rings use the same FOV fractions we sent to -slm.
	gpts = fire_pts
	row: dict[str, Any] = {
		"trial_index": trial_index,
		"iteration": it,
		"group_index": gi,
		"trigger_index": gi,
		"n_triggers": n_groups,
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
		"repetitions": int(meta.get("repetitions", 1)),
	}
	log.write({**row, "phase": "armed"})

	last = max(n_groups - 1, 0)
	if trigger == "serial":
		trigger_msg = f"  trigger {gi}/{last}: trial {trial_index} pts={point_ids}"
	elif trigger == "none":
		trigger_msg = f"  [none] trial {trial_index} pts={point_ids}"
	else:
		trigger_msg = f"  wait trigger {gi}/{last}: trial {trial_index} pts={point_ids}"

	f0_frames: list = []
	f1_frames: list = []
	if want and not dry_run and not visual_cfg.enabled:
		f0_frames = _collect_frames(relay, f0_s, frame_poll_s)

	if dry_run:
		print(
			f"  [dry] single -slm trial {trial_index}: {gname} "
			f"power={power} pts={point_ids}"
		)
	elif trigger == "serial" and ttl is not None and not visual_cfg.enabled:
		from prairie_live.stim_fire import run_while_pulsing_dtr

		# Idle-low before arm so the next rising edge is clean.
		if hasattr(ttl, "set_dtr"):
			ttl.set_dtr(False)
		init_ms = float(slm_params.get("initial_delay_ms") or 0)
		_pre_stim_delay(wait_ms=init_ms, pl=pl, via_relay=via_relay)

		def _do_slm() -> None:
			_fire_mark_all_points(
				parts=slm_parts,
				wait_ms=0.0,
				pl=pl,
				relay=relay,
				via_relay=via_relay,
				no_wait=False,
				timeout=max(180.0, wait_s + 60.0),
			)

		print(
			f"  group {gi + 1}/{n_groups}: arm -slm "
			f"(Trig In 1) → 1 DTR → burn pts={point_ids} "
			f"power={power:g} (~{wait_s:.2f}s)"
		)
		# Exactly one planned DTR; no rescue edges (those stole later groups).
		run_while_pulsing_dtr(
			_do_slm,
			ttl=ttl,
			n_pulses=1,
			ttl_width_s=ttl_width_s,
			arm_delay_s=0.4,
			rescue_window_s=0.0,
			join_timeout_s=max(180.0, wait_s + 60.0),
			row=row,
			on_pulse=lambda _i: print(
				f"  group {gi + 1}/{n_groups}: DTR pulsed → pts={point_ids}"
			),
		)
		if hasattr(ttl, "set_dtr"):
			ttl.set_dtr(False)
		# -slm stops LiveScan → black power tiles unless we restart first.
		_ensure_live_after_stim(relay=relay, pl=pl, via_relay=via_relay)
		if want:
			f1_frames = _collect_frames(relay, max(f1_s, 0.2), frame_poll_s)
		# Settle before the next group's -slm arms Trig In 1.
		time.sleep(max(float(inter_trial_s), 0.35))
	else:
		_fire_mark_all_points(
			parts=slm_parts,
			wait_ms=float(slm_params.get("initial_delay_ms") or 0),
			pl=pl,
			relay=relay,
			via_relay=via_relay,
			no_wait=False,
			timeout=max(180.0, wait_s + 60.0),
		)
		print(
			f"  single -slm trial {trial_index}: {gname} "
			f"power={power} pts={point_ids}"
		)
		if not dry_run:
			_ensure_live_after_stim(relay=relay, pl=pl, via_relay=via_relay)
		f0_b, f1_frames = _run_stim_epoch(
			row,
			trigger=trigger,
			ttl=ttl,
			ttl_width_s=ttl_width_s,
			dry_run=dry_run,
			visual_stim=visual_stim,
			visual_cfg=visual_cfg,
			rng=rng,
			relay=relay,
			want=want,
			f0_s=0.0 if f0_frames else f0_s,
			f1_s=f1_s,
			frame_poll_s=frame_poll_s,
			wait_s=wait_s,
			trigger_msg=trigger_msg,
		)
		if not f0_frames:
			f0_frames = f0_b
		if trigger_msg and not visual_cfg.enabled and trigger != "serial":
			print(trigger_msg)

	_finish_trial_frames(
		row,
		gpts=gpts,
		point_pool=point_pool,
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
	# Serial path already settled after DTR; avoid a second full inter_trial wait.
	serial_settled = (
		trigger == "serial" and ttl is not None and not dry_run and not visual_cfg.enabled
	)
	if inter_trial_s > 0 and not serial_settled:
		time.sleep(inter_trial_s)
	return row


def _galvo_points_for_simultaneous(gpts: list[dict]) -> list[dict]:
	"""Prefer stored galvo volts so AllPointsAtOnce matches the cyan overlay."""
	out: list[dict] = []
	for p in gpts:
		row = dict(p)
		gx, gy = p.get("x_galvo"), p.get("y_galvo")
		if gx not in (None, "") and gy not in (None, ""):
			row["x"], row["y"] = float(gx), float(gy)
			row["coord_space"] = "galvo"
		out.append(row)
	return out


def _run_series_trial(
	*,
	gpts: list[dict],
	point_pool: list[dict],
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
	visual_stim: Any,
	visual_cfg: Any,
	rng: random.Random,
	simultaneous: bool = False,
	n_groups: int = 1,
) -> dict:
	"""One group × power via -LoadMarkPoints / -MarkPoints.

	simultaneous=True (stim_mode=slm): AllPointsAtOnce fat XML so every point
	in the group burns together at native galvo positions (overlay-aligned).
	"""
	gname = f"PL_t{trial_index:04d}_g{gi + 1}"
	fire_pts = gpts
	if simultaneous:
		# Restore galvo from x_galvo when present; else map FOV→galvo for fire.
		fire_pts = _galvo_points_for_simultaneous(gpts)
		need_map = any(
			p.get("coord_space") == "fov"
			or (
				0.0 <= float(p["x"]) <= 1.0
				and 0.0 <= float(p["y"]) <= 1.0
				and p.get("x_galvo") in (None, "")
			)
			for p in fire_pts
		)
		if need_map and not dry_run and relay is not None:
			from prairie_live.scan_geometry import points_to_galvo_for_series

			fire_pts, fov_note = points_to_galvo_for_series(
				fire_pts,
				relay,
				fov_width_um=(
					float(meta["fov_width_um"])
					if meta.get("fov_width_um") not in (None, "")
					else None
				),
			)
			print(f"  {fov_note}")
	elif (
		not dry_run
		and relay is not None
		and gpts
		and any(p.get("coord_space") == "fov" for p in gpts)
	):
		from prairie_live.scan_geometry import points_to_galvo_for_series

		# FOV-locked pool (coord_space=fov) is 0–1; series XML needs live galvo.
		fire_pts, fov_note = points_to_galvo_for_series(
			gpts,
			relay,
			fov_width_um=(
				float(meta["fov_width_um"])
				if meta.get("fov_width_um") not in (None, "")
				else None
			),
		)
		if trial_index == 0 or gi == 0:
			print(f"  {fov_note}")
			for p in fire_pts[:3]:
				print(
					f"    Point {p['id']}  FOV ({float(p.get('x_fov', 0)):.3f}, "
					f"{float(p.get('y_fov', 0)):.3f}) → galvo "
					f"({float(p['x']):.3f}, {float(p['y']):.3f}) "
					f"spiral={p.get('spiral_size_fov') or '—'}"
				)
	series_meta = dict(meta)
	if simultaneous:
		# Fat layout + APA: one MarkPointElement, all group Points at once.
		# (pv_saved per-point XML fires sequentially — not a simultaneous group.)
		series_meta["all_points_at_once"] = "True"
		series_meta["pv_saved_format"] = False
		# One hologram per TTL. File Repetitions=3 + FirstRepetition can leave
		# PV waiting on extra edges / MarkPoints folder dialogs → stuck on g1.
		series_meta["repetitions"] = 1
	else:
		# Sequential series: APA+spiral after a prior -slm run has crashed PV.
		series_meta["all_points_at_once"] = "False"
	if trigger == "none":
		series_meta["trigger_selection"] = "None"
		series_meta["trigger_frequency"] = "None"
		series_meta["trigger_identifier"] = "00000000-0000-0000-0000-000000000000"
	# Keep file spiral timing when present — experiment 100ms/5 revs on a
	# PV-saved 36ms/8 spiral has also tripped the spiral DAC.
	if meta.get("pv_saved_format") or simultaneous:
		for pt in fire_pts:
			if pt.get("duration") not in (None, ""):
				series_meta["duration"] = float(pt["duration"])
				break
		for pt in fire_pts:
			if pt.get("spiral_revolutions") not in (None, ""):
				series_meta["spiral_revolutions"] = str(pt["spiral_revolutions"])
				break
		for pt in fire_pts:
			ipd = pt.get("inter_point_delay")
			if ipd not in (None, ""):
				series_meta["inter_point_delay"] = str(ipd)
				break
	step = build_group_step(
		fire_pts,
		name=gname,
		power=power,
		meta=series_meta,
		trigger_selection=trig_sel if trigger != "none" else "None",
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
		"stim_mode": "slm" if simultaneous else "series",
		"all_points_at_once": bool(simultaneous),
		"t_cmd": None,
		"t_ttl": None,
		"score": None,
		"score_kind": None,
		"image_paths": None,
		"repetitions": int(series_meta.get("repetitions", meta.get("repetitions", 1))),
	}
	xml = groups_to_xml(series)
	if scope_xml:
		write_scope_xml(series, scope_xml)

	# One DTR per group trial (FirstRepetition only on the group's first point).
	n_pulses = 1
	wait_s = (estimate_series_ms(series) + pad_ms) / 1000.0
	# APA is short; don't sit 3+ minutes on an E:\Markpoints modal.
	mp_timeout = (
		max(45.0, wait_s + 20.0) if simultaneous else max(180.0, wait_s + 60.0)
	)

	want = _want_frames(
		relay=relay, dry_run=dry_run, mock_scores=mock_scores, run_root=run_root
	)
	# Baseline frames before arming Mark Points.
	f0_frames: list = []
	if want and not dry_run and not visual_cfg.enabled:
		f0_frames = _collect_frames(relay, f0_s, frame_poll_s)

	row["t_cmd"] = time.time()
	log.write({**row, "phase": "armed"})

	trig_msg = ""
	if trigger == "serial":
		trig_msg = f"  trigger: trial {trial_index} pts={point_ids}"
	elif trigger == "none":
		trig_msg = f"  [none] trial {trial_index} pts={point_ids}"

	f1_frames: list = []
	if dry_run:
		print(f"  [dry] trial {trial_index}: {gname} power={power} pts={point_ids}")
	elif trigger == "serial" and ttl is not None and not visual_cfg.enabled:
		# Load first, then pulse DTR only while -MarkPoints waits on Trig In 1.
		# Bundling -lmp inside the DTR window made the edge arrive during load.
		from prairie_live.stim_fire import run_while_pulsing_dtr

		_load_mark_points(
			xml=xml,
			scope_xml=scope_xml,
			pl=pl,
			relay=relay,
			via_relay=via_relay,
		)

		def _do_mp() -> None:
			_mark_points_only(
				pl=pl,
				relay=relay,
				via_relay=via_relay,
				no_wait=False,
				timeout=mp_timeout,
			)

		# Timing: load done → -mp starts → arm_delay (PV enters FirstRepetition
		# wait on the group's first point) → one DTR → join until group DONE.
		how = "simultaneous APA" if simultaneous else "series"
		print(
			f"  trial {trial_index}: -lmp/-mp {gname} ({how}) power={power} "
			f"pts={point_ids} (1 DTR after arm, then wait ~{wait_s:.2f}s)"
		)
		# Join longer than mp_timeout so the worker can TimeoutError → drop
		# script session → Abort before we declare "still running". That stops
		# leftover -MarkPoints from surviving a PV restart.
		run_while_pulsing_dtr(
			_do_mp,
			ttl=ttl,
			n_pulses=1,
			ttl_width_s=ttl_width_s,
			gap_s=0.0,
			arm_delay_s=0.25,
			rescue_window_s=1.5,
			join_timeout_s=mp_timeout + 20.0,
			row=row,
			on_pulse=lambda _i: print(trig_msg),
		)
		if want:
			f1_frames = _collect_frames(relay, max(f1_s, 0.2), frame_poll_s)
	else:
		_fire_mark_points(
			xml=xml,
			scope_xml=scope_xml,
			pl=pl,
			relay=relay,
			via_relay=via_relay,
			no_wait=False,
			timeout=mp_timeout,
		)
		how = "simultaneous APA" if simultaneous else "series"
		print(
			f"  trial {trial_index}: -lmp/-mp {gname} ({how}) "
			f"power={power} pts={point_ids}"
		)
		f0_b, f1_frames = _run_stim_epoch(
			row,
			trigger=trigger,
			ttl=ttl,
			ttl_width_s=ttl_width_s,
			dry_run=dry_run,
			visual_stim=visual_stim,
			visual_cfg=visual_cfg,
			rng=rng,
			relay=relay,
			want=want,
			f0_s=0.0 if f0_frames else f0_s,
			f1_s=f1_s,
			frame_poll_s=frame_poll_s,
			wait_s=wait_s,
			trigger_msg=trig_msg,
			n_pulses=n_pulses,
			wait_between_pulses_s=0.0,
		)
		if not f0_frames:
			f0_frames = f0_b

	if simultaneous:
		# Log group i/N (same as former -slm single path).
		row["trigger_index"] = gi
		row["n_triggers"] = max(int(n_groups), 1)
		# APA / Mark Points often drop LiveScan — restore FOV for power tiles.
		_ensure_live_after_stim(relay=relay, pl=pl, via_relay=via_relay)
	else:
		row["n_triggers"] = n_pulses
		row["trigger_index"] = 0

	_finish_trial_frames(
		row,
		gpts=gpts,
		point_pool=point_pool,
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


def _pre_stim_delay(*, wait_ms: float, pl: PrairieLink | None, via_relay: bool) -> None:
	"""Finish InitialDelay *before* the DTR arm window (so the edge is not early)."""
	ms = float(wait_ms or 0)
	if ms <= 0:
		return
	if via_relay or pl is None:
		# No bare -Wait on the relay API; delaying the -slm send is equivalent.
		time.sleep(ms / 1000.0)
		return
	pl.send_command("-Wait", str(int(round(ms))))


def _ensure_live_after_stim(
	*,
	relay: Any | None,
	pl: PrairieLink | None,
	via_relay: bool,
	settle_s: float = 0.4,
	label: str = "post-stim",
) -> None:
	"""-MarkAllPoints / -Abort often kill LiveScan → black power-tile frames.

	Restart Live and wait briefly so the relay grabber has non-zero FOV again.
	Also used *before* SLM fire so ``currentScanAmplitude`` matches the overlay.
	"""
	if via_relay and relay is not None and hasattr(relay, "start_live"):
		try:
			out = relay.start_live()
			print(f"  {label}: -LiveScan (FOV / scan range)")
			if isinstance(out, dict) and out.get("ok") is False:
				print(f"  WARNING: -LiveScan failed: {out}")
		except Exception as exc:
			print(f"  WARNING: -LiveScan ({label}) failed: {exc}")
	elif pl is not None:
		try:
			pl.send_command("-LiveScan")
			print(f"  {label}: -LiveScan (FOV / scan range)")
		except Exception as exc:
			print(f"  WARNING: -LiveScan ({label}) failed: {exc}")
	if settle_s > 0:
		time.sleep(float(settle_s))


def _slm_fire_points(
	gpts: list[dict],
	*,
	relay: Any | None,
	pl: PrairieLink | None,
	via_relay: bool,
	slm_params: dict,
	dry_run: bool,
	log_prefix: str = "  ",
) -> tuple[list[dict], float | None]:
	"""LiveScan on → remap galvo→FOV with *current* amp/center for -slm.

	Session-start FOV lock alone is not enough: it can run on a stale scan
	range right after -Abort, so burns land on the FOV but miss the cyan rings.
	"""
	from prairie_live.scan_geometry import points_for_slm_fire

	if dry_run or relay is None:
		# Dry-run / no relay: keep whatever fractions the session lock produced.
		return list(gpts), None
	_ensure_live_after_stim(
		relay=relay,
		pl=pl,
		via_relay=via_relay,
		# Short settle: amp/center must match the live overlay before -slm.
		settle_s=0.35,
		label="pre-slm",
	)
	fire_pts, spiral_fov, note = points_for_slm_fire(
		gpts,
		relay,
		spiral_size_um=slm_params.get("spiral_size_um"),
		fov_width_um=slm_params.get("fov_width_um"),
	)
	print(f"{log_prefix}{note}")
	for p in fire_pts[:5]:
		gx, gy = p.get("x_galvo"), p.get("y_galvo")
		if gx is not None and gy is not None:
			print(
				f"{log_prefix}  P{p['id']} galvo ({float(gx):.4f},{float(gy):.4f}) "
				f"→ FOV ({float(p['x']):.4f},{float(p['y']):.4f})"
			)
		else:
			print(
				f"{log_prefix}  P{p['id']} FOV ({float(p['x']):.4f},{float(p['y']):.4f})"
			)
	return fire_pts, spiral_fov


def _slm_script_power(power: float, meta: dict | None = None) -> float:
	"""Power token for ``-MarkAllPoints``.

	PV's script ``-slm`` takes the same UI UncagingLaserPower scale as the Mark
	Points window (e.g. 160), *not* the 0–1 fraction written into series XML
	(``UncagingLaserPower="0.4"``). Sending the fraction made SLM look like it
	ran with no visible burns.
	"""
	del meta  # series XML scale only — kept for call-site compatibility
	return float(power)


def _fire_mark_all_points(
	*,
	parts: list[str],
	wait_ms: float,
	pl: PrairieLink | None,
	relay: Any | None,
	via_relay: bool,
	no_wait: bool = False,
	timeout: float = 180.0,
) -> None:
	"""Send -MarkAllPoints (-slm); prefer relay when via_relay.

	Keep ``no_wait=False`` for photostim: pulse DTR while this blocks (stim_fire).
	For serial+DTR, call ``_script_wait`` first, then this with ``wait_ms=0``.
	"""
	if via_relay:
		if relay is None:
			raise RuntimeError("via_relay set but relay client is not connected")
		out = relay.mark_all_points(
			parts, wait_ms=wait_ms, no_wait=no_wait, timeout=timeout
		)
		if not out.get("ok", False):
			raise RuntimeError(f"relay mark_all_points failed: {out}")
		return
	if pl is None:
		raise RuntimeError("need PrairieLink or --via-relay for stim_mode=slm")
	tokens = [str(p) for p in parts]
	if wait_ms and float(wait_ms) > 0:
		pl.send_command("-Wait", str(int(round(float(wait_ms)))))
	if no_wait:
		pl.send_command("-Silent", "-NoWait", *tokens)
	else:
		pl.send_command("-Silent", *tokens)


def _load_mark_points(
	*,
	xml: str,
	scope_xml: str | None,
	pl: PrairieLink | None,
	relay: Any | None,
	via_relay: bool,
) -> None:
	"""-LoadMarkPoints only (finish before starting the DTR arm window)."""
	if via_relay:
		if relay is None:
			raise RuntimeError("via_relay set but relay client is not connected")
		out = relay.load_mark_points(xml)
		if not out.get("ok", False):
			raise RuntimeError(f"relay load_mark_points failed: {out}")
		return
	if pl is None or not scope_xml:
		raise RuntimeError("need PrairieLink + --scope-xml, or --via-relay")
	# -Silent: avoid E:\Markpoints "file already present" modal on reload.
	pl.send_command("-Silent", "-LoadMarkPoints", scope_xml)


def _mark_points_only(
	*,
	pl: PrairieLink | None,
	relay: Any | None,
	via_relay: bool,
	no_wait: bool = False,
	timeout: float = 180.0,
) -> None:
	"""-MarkPoints only (series already loaded)."""
	if via_relay:
		if relay is None:
			raise RuntimeError("via_relay set but relay client is not connected")
		out = relay.mark_points(no_wait=no_wait, timeout=timeout)
		if not out.get("ok", False):
			raise RuntimeError(f"relay mark_points failed: {out}")
		return
	if pl is None:
		raise RuntimeError("need PrairieLink or --via-relay")
	if no_wait:
		pl.send_command("-Silent", "-NoWait", "-MarkPoints")
	else:
		pl.send_command("-Silent", "-MarkPoints")


def _fire_mark_points(
	*,
	xml: str,
	scope_xml: str | None,
	pl: PrairieLink | None,
	relay: Any | None,
	via_relay: bool,
	no_wait: bool = False,
	timeout: float = 180.0,
) -> None:
	"""Load + fire one series; prefer relay push when via_relay (no SMB).

	``no_wait=False`` (default): block until DONE. For serial+DTR prefer
	``_load_mark_points`` then ``run_while_pulsing_dtr(_mark_points_only)``.
	"""
	_load_mark_points(
		xml=xml, scope_xml=scope_xml, pl=pl, relay=relay, via_relay=via_relay
	)
	_mark_points_only(
		pl=pl, relay=relay, via_relay=via_relay, no_wait=no_wait, timeout=timeout
	)


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
	p.add_argument("--repetitions", type=int, default=None)
	p.add_argument("--stim-dff-min", type=float, default=None)
	p.add_argument(
		"--no-candidate-gui",
		action="store_true",
		help="Write candidates.json/txt/XML only; do not open the FOV picker",
	)
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
	visual_g = p.add_mutually_exclusive_group()
	visual_g.add_argument(
		"--visual",
		dest="visual_enabled",
		action="store_true",
		default=None,
		help="Enable embedded PsychoPy gratings (analysis PC, second monitor)",
	)
	visual_g.add_argument(
		"--no-visual",
		dest="visual_enabled",
		action="store_false",
		help="Disable PsychoPy gratings (default when not in config)",
	)
	p.add_argument("--visual-screen", type=int, default=None)
	p.add_argument("--visual-refresh-hz", type=float, default=None)
	p.add_argument("--visual-isi-s", type=float, default=None)
	p.add_argument(
		"--visual-lead-ms",
		type=float,
		default=None,
		help="DTR delay after grating/RTS onset (legacy stimOnTime @ refresh)",
	)
	p.add_argument("--visual-dtr-frames", type=int, default=None)
	p.add_argument("--visual-grating-frames", type=int, default=None)
	# Parse full argv (includes --config) so --help lists every flag once.
	args = p.parse_args(argv)

	opt = merge_cli_over_config(args, cfg)
	if getattr(args, "no_candidate_gui", False):
		opt["no_candidate_gui"] = True
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
	opt.setdefault("repetitions", 1)
	opt.setdefault("stim_dff_min", 0.0)
	opt.setdefault("stim_mode", "series")
	opt.setdefault("slm_pack", False)
	opt.setdefault("visual_enabled", False)

	execute_session(opt)


def execute_session(
	opt: dict[str, Any],
	*,
	on_trial: Callable[[dict], None] | None = None,
	show_candidate_gui: bool | None = None,
	should_stop: Callable[[], bool] | None = None,
) -> list[dict]:
	"""Load Fat XML, connect (or dry-run), run the mapping loop.

	Shared by ``mp-sync`` CLI and the desktop GUI.
	"""
	from prairie_live.experiment_config import apply_stim_to_meta, powers_to_list

	opt.setdefault("log", "trials.jsonl")
	opt.setdefault("host", "127.0.0.1")
	opt.setdefault("port", DEFAULT_PORT)
	opt.setdefault("password", "0000")
	opt.setdefault("iterations", 1)
	opt.setdefault("n_groups", 2)
	opt.setdefault("group_size", 3)
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
	opt.setdefault("repetitions", 1)
	opt.setdefault("stim_dff_min", 0.0)
	opt.setdefault("stim_mode", "series")
	opt.setdefault("slm_pack", False)
	if "powers" in opt:
		opt["powers"] = powers_to_list(opt["powers"])
	else:
		opt["powers"] = [0.0, 75.0]

	series = opt.get("series")
	if not series:
		raise SystemExit("need --series PATH or series in experiment.json")

	gs = int(opt["group_size"])
	if gs < 3:
		raise SystemExit(f"group_size must be >= 3 (got {gs})")

	steps = load_mark_points_file(series)
	points = extract_unique_points(steps)
	meta = apply_stim_to_meta(template_meta(steps), opt)
	# PV-saved / .gpl: Repetitions + spiral dwell live in the Mark Points file.
	# GUI/lab defaults must not rewrite them (that combo crashed PV spiral DAC).
	if not meta.get("pv_saved_format") and opt.get("repetitions") is not None:
		meta["repetitions"] = max(int(opt["repetitions"]), 1)
	powers = opt["powers"]
	src_kind = "gpl" if str(series).lower().endswith(".gpl") else "xml"
	# Point spiral size from lab defaults only when the file omits SpiralSize.
	if opt.get("spiral_size_um") is not None:
		for pt in points:
			if pt.get("spiral_size_fov") in (None, ""):
				pt["spiral_size_um"] = str(opt["spiral_size_um"])
	if opt.get("spiral") is not None and not meta.get("pv_saved_format"):
		for pt in points:
			pt["is_spiral"] = "True" if bool(opt["spiral"]) else "False"

	stim_mode = str(opt.get("stim_mode", "series")).lower()
	if stim_mode not in ("series", "slm"):
		raise SystemExit(f"stim_mode must be 'series' or 'slm', got {stim_mode!r}")

	# .gpl / PV-saved series often store galvo volts (outside 0–1). SLM locks
	# those to the live FOV (0–1) so burns match the cyan overlay; series keeps
	# native X/Y/SpiralSize and does not remap.
	oob = [
		p
		for p in points
		if not (0.0 <= float(p["x"]) <= 1.0 and 0.0 <= float(p["y"]) <= 1.0)
	]
	# .gpl Z is the absolute focus the points were drawn at, not an offset;
	# Use3D with it can refocus the uncaging beam away from the sample.
	if any(abs(float(p.get("z", 0))) > 1e-6 for p in points) and not opt.get("use_3d"):
		print("NOTE: points carry Z; Use3D stays off (fire at the current focus)")
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
	if meta.get("pv_saved_format"):
		print(
			"Mark Points XML is source of truth for Duration / SpiralRevs / "
			f"IPD / Repetitions "
			f"(duration={meta.get('duration')} ms, "
			f"revs={meta.get('spiral_revolutions')}, "
			f"ipd={meta.get('inter_point_delay')}, "
			f"reps={meta.get('repetitions')})"
		)
	if oob:
		print(
			f"NOTE: {len(oob)}/{len(points)} points are in galvo units (outside 0–1). "
			f"{stim_mode} keeps native X/Y/SpiralSize (same as the cyan overlay)."
		)
		for p in oob[:5]:
			print(f"  Point {p['id']}  ({p['x']:.4f}, {p['y']:.4f})")
	if stim_mode == "slm":
		if str(opt.get("trigger") or "") == "serial":
			print(
				"stim_mode=slm + serial → each group: AllPointsAtOnce "
				"(-lmp/-mp) → 1 DTR → whole group burns together"
			)
		else:
			print(
				"stim_mode=slm → each group: AllPointsAtOnce (-lmp/-mp) "
				"simultaneous multi-spot on galvo FOV"
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
		return []

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
			print(
				f"TTL: {opt['serial']} DTR → wire to PV Trig In 1 "
				f"(width={float(opt.get('ttl_width') or 0.01):g}s). "
				"If no burns: try trigger=none once to confirm laser/FOV, "
				"then check COM + BNC."
			)

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

		# Prior Run (often stim_mode=slm) can leave -MarkAllPoints armed; the
		# next series -MarkPoints then hits "another task already in progress"
		# and/or E:\Markpoints folder dialogs. Clear once here, not per trial.
		if not opt.get("dry_run"):
			from prairie_live.pv_cleanup import clear_prior_stim_on_client

			clr = clear_prior_stim_on_client(relay, pl)
			print(
				"session start: clear leftover PV stim → "
				+ ", ".join(str(n) for n in (clr.get("notes") or []))
			)

		# SLM = AllPointsAtOnce at native galvo (same coords as working series).
		# Do not FOV-lock / -MarkAllPoints here — that misplaced burns vs overlay.
		if stim_mode in ("slm", "series") and oob:
			print(
				f"{stim_mode}: firing {len(oob)} galvo points as-is "
				f"(PV-saved X/Y/SpiralSize); no FOV remapping"
			)

		show = (
			show_candidate_gui
			if show_candidate_gui is not None
			else (not bool(opt.get("no_candidate_gui")))
		)
		return run_sync_loop(
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
			slm_pack=bool(opt.get("slm_pack", False)),
			images_dir=opt.get("images_dir"),
			visual_cfg=opt,
			stim_dff_min=float(opt.get("stim_dff_min") or 0.0),
			show_candidate_gui=show,
			on_trial=on_trial,
			should_stop=should_stop,
			cover_all=bool(opt.get("cover_all")),
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
