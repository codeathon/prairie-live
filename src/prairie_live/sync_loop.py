"""Synchronized Mark Points mapping loop: groups → TTL → JSONL → regroup.

Flow per mapping iteration
--------------------------
1. Build pseudo-random groups from the points pool (or score-biased groups).
2. For each (group × power) trial: author JSONL identity, write one-step
   MarkPoints.xml, -LoadMarkPoints, -MarkPoints, optional serial DTR pulse,
   wait estimated duration, optional relay disk ΔF/F.
3. Aggregate per-point scores; next iteration reuses top responders plus
   random fill.

Group↔TTL identity is authored here (trial_index ≡ TTL edge index). PV does
not report which group fired.
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
	if n_groups < 1 or group_size < 1:
		raise ValueError("n_groups and group_size must be >= 1")
	if not points:
		raise ValueError("empty points pool")

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


def disk_mean(frame: np.ndarray, x_norm: float, y_norm: float, radius: int) -> float:
	"""Mean intensity in a disk; x/y are FOV-normalized [0,1] (Prairie style)."""
	h, w = frame.shape[:2]
	cx = int(round(float(x_norm) * (w - 1)))
	cy = int(round(float(y_norm) * (h - 1)))
	yy, xx = np.ogrid[:h, :w]
	mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= radius ** 2
	if not np.any(mask):
		return float(frame[cy, cx])
	return float(frame[mask].mean())


def score_group_dff(
	frames_f0: list[np.ndarray],
	frames_f1: list[np.ndarray],
	points: list[dict],
	radius: int = 3,
) -> float:
	"""Mean ΔF/F across group points (on-target)."""
	if not frames_f0 or not frames_f1:
		return 0.0
	f0 = np.mean(np.stack(frames_f0, axis=0), axis=0)
	f1 = np.mean(np.stack(frames_f1, axis=0), axis=0)
	vals = []
	for pt in points:
		b = disk_mean(f0, pt["x"], pt["y"], radius)
		a = disk_mean(f1, pt["x"], pt["y"], radius)
		if b <= 0:
			continue
		vals.append((a - b) / b)
	return float(np.mean(vals)) if vals else 0.0


class JsonlLog:
	"""Append-only trial log; trial_index is the join key to TTL edge k."""

	def __init__(self, path: str | Path):
		self.path = Path(path)
		self.path.parent.mkdir(parents=True, exist_ok=True)
		self._fp = open(self.path, "a", encoding="utf-8")
		self.n_written = 0

	def write(self, row: dict) -> None:
		self._fp.write(json.dumps(row, sort_keys=True) + "\n")
		self._fp.flush()
		self.n_written += 1

	def close(self) -> None:
		self._fp.close()


def write_scope_xml(groups: list[dict], path: str) -> None:
	"""Write series XML to a path PrairieView can read (often on the scope)."""
	xml = groups_to_xml(groups)
	parent = os.path.dirname(path)
	if parent:
		os.makedirs(parent, exist_ok=True)
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
	on_trial: Callable[[dict], None] | None = None,
) -> list[dict]:
	"""
	Run mapping iterations. Returns all trial rows written this session.

	trigger:
	  none   — TriggerSelection=None; -mp fires immediately; log t_cmd
	  serial — TriggerSelection from meta (e.g. PFI1); -mp arms, then DTR pulse
	  wait   — same as serial but no DTR (external PsychoPy/PackIO provides TTL)

	via_relay:
	  True — push XML + -lmp/-mp through the relay (no SMB share needed)
	"""
	rng = random.Random(seed)
	scores: dict[str, float] | None = None
	all_trials: list[dict] = []
	trial_index = 0

	trig_sel = str(meta.get("trigger_selection", "None"))
	if trigger == "none":
		trig_sel = "None"
	elif trigger in ("serial", "wait") and trig_sel in ("", "None", "none"):
		# Series waits for hardware; default PFI1 matches common PackIO wiring.
		trig_sel = "PFI1"

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
			f"scored={'yes' if scores else 'random'} ==="
		)
		iter_trials: list[dict] = []

		for gi, gpts in enumerate(groups_pts):
			for power in powers:
				gname = f"Group {gi + 1}"
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
					"t_cmd": None,
					"t_ttl": None,
					"score": None,
					"score_kind": None,
				}

				xml = groups_to_xml(series)
				if scope_xml:
					write_scope_xml(series, scope_xml)
				row["t_cmd"] = time.time()
				log.write({**row, "phase": "armed"})

				if dry_run:
					print(
						f"  [dry] trial {trial_index}: {gname} "
						f"power={power} pts={point_ids}"
					)
				else:
					_fire_mark_points(
						xml=xml,
						scope_xml=scope_xml,
						pl=pl,
						relay=relay,
						via_relay=via_relay,
					)
					print(
						f"  trial {trial_index}: -lmp/-mp {gname} "
						f"power={power} pts={point_ids}"
					)

				# Capture F0 before TTL when relay scoring is on.
				f0_frames: list[np.ndarray] = []
				f1_frames: list[np.ndarray] = []
				if relay is not None and not dry_run and not mock_scores:
					f0_frames = _collect_frames(relay, f0_s, frame_poll_s)

				if trigger == "serial" and ttl is not None and not dry_run:
					row["t_ttl"] = time.time()
					ttl.pulse_dtr(ttl_width_s)
				elif trigger == "none":
					# Software fire: treat command time as stim time.
					row["t_ttl"] = row["t_cmd"]
				else:
					# wait: external TTL; still stamp wall clock near -mp.
					row["t_ttl"] = time.time()

				wait_s = (estimate_series_ms(series) + pad_ms) / 1000.0
				if relay is not None and not dry_run and not mock_scores:
					f1_frames = _collect_frames(relay, max(f1_s, wait_s), frame_poll_s)
				else:
					time.sleep(max(wait_s, 0.0))

				if mock_scores:
					# Deterministic-ish mock so regrouping is testable without frames.
					row["score"] = abs(hash((tuple(point_ids), power, it))) % 1000 / 1000.0
					row["score_kind"] = "mock"
				elif relay is not None and f0_frames and f1_frames:
					row["score"] = score_group_dff(
						f0_frames, f1_frames, gpts, radius=disk_radius
					)
					row["score_kind"] = "relay_disk_dff"
				else:
					row["score_kind"] = "none"

				log.write({**row, "phase": "done"})
				if on_trial:
					on_trial(row)
				iter_trials.append(row)
				all_trials.append(row)
				trial_index += 1
				if inter_trial_s > 0:
					time.sleep(inter_trial_s)

		agg = aggregate_point_scores(iter_trials)
		if agg:
			scores = agg
			top = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:5]
			print(f"  scores (top): {top}")
		else:
			print("  no scores this iteration — next groups stay random")

	return all_trials


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
	p = argparse.ArgumentParser(
		description="Mark Points sync loop: random groups, TTL, JSONL, regroup"
	)
	p.add_argument(
		"--series",
		required=True,
		help="Source MarkPoints.xml (points pool; fat nested <Point> preferred)",
	)
	p.add_argument(
		"--scope-xml",
		help="Writable path PrairieView loads via -LoadMarkPoints (SMB/share). "
		"Not needed with --via-relay.",
	)
	p.add_argument(
		"--via-relay",
		metavar="HOST[:PORT]",
		help="Push trial XML over prairie_live relay (no file share). "
		"Example: 10.33.107.147:25100",
	)
	p.add_argument("--log", default="markpoints_trials.jsonl", help="JSONL trial log")
	p.add_argument("--host", default="127.0.0.1")
	p.add_argument("--port", type=int, default=DEFAULT_PORT)
	p.add_argument("--password", default="0000")
	p.add_argument("--iterations", type=int, default=3)
	p.add_argument("--n-groups", type=int, default=2)
	p.add_argument("--group-size", type=int, default=9)
	p.add_argument(
		"--powers",
		default="0,0.75",
		help="Comma-separated UncagingLaserPower values per group",
	)
	p.add_argument("--seed", type=int, default=0)
	p.add_argument(
		"--trigger",
		choices=("none", "serial", "wait"),
		default="none",
		help="none=software fire; serial=DTR after -mp; wait=external TTL",
	)
	p.add_argument("--serial", help="COM port for DTR photostim pulse (e.g. COM3)")
	p.add_argument("--ttl-width", type=float, default=0.01)
	p.add_argument("--inter-trial", type=float, default=0.5)
	p.add_argument("--pad-ms", type=float, default=100.0)
	p.add_argument("--elite-frac", type=float, default=0.4)
	p.add_argument(
		"--mock-scores",
		action="store_true",
		help="Fake per-trial scores so regrouping works without imaging",
	)
	p.add_argument(
		"--relay",
		help="host[:port] for disk ΔF/F scoring (defaults to --via-relay if set)",
	)
	p.add_argument("--f0-s", type=float, default=0.5, help="pre-TTL baseline window")
	p.add_argument("--f1-s", type=float, default=1.0, help="post-TTL response window")
	p.add_argument("--frame-poll", type=float, default=0.05)
	p.add_argument("--disk-radius", type=int, default=3)
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
	args = p.parse_args(argv)

	steps = load_mark_points_file(args.series)
	points = extract_unique_points(steps)
	meta = template_meta(steps)
	powers = [float(x) for x in args.powers.split(",") if x.strip()]

	print(f"points pool: {len(points)}  powers={powers}  trigger={args.trigger}")
	if args.inspect:
		zero = 0
		for pt in points:
			print(
				f"  Point {pt['id']}  ({pt['x']:.4f}, {pt['y']:.4f}, "
				f"{pt.get('z', 0)})"
			)
			if float(pt.get("x", 0)) == 0.0 and float(pt.get("y", 0)) == 0.0:
				zero += 1
		if zero:
			print(
				f"WARNING: {zero}/{len(points)} points at (0,0) — "
				"file may be slim/group-name-only or a bad copy. "
				"Prefer a fat MarkPoints.xml with nested <Point X Y Z>."
			)
		else:
			print(f"OK: {len(points)} points with FOV coordinates.")
		return

	via_relay = bool(args.via_relay)
	if not args.dry_run and not via_relay and not args.scope_xml:
		raise SystemExit("need --via-relay HOST:PORT or --scope-xml PATH")

	ttl = None
	pl = None
	relay = None
	log = JsonlLog(args.log)
	try:
		if args.trigger == "serial" and not args.dry_run:
			if not args.serial:
				raise SystemExit("--trigger serial requires --serial COMx")
			from prairie_live.ttl_serial import SerialTtl

			ttl = SerialTtl(args.serial)

		relay_addr = args.via_relay or args.relay
		if relay_addr and not args.dry_run:
			from prairie_live.relay_client import RelayClient

			host, _, port = relay_addr.partition(":")
			relay = RelayClient(host, int(port or 25100))
			relay.connect()

		# Direct PrairieLink only when not pushing XML through the relay.
		if not args.dry_run and not via_relay:
			pl = PrairieLink(host=args.host, port=args.port, password=args.password)

		run_sync_loop(
			points=points,
			meta=meta,
			pl=pl,
			log=log,
			scope_xml=args.scope_xml,
			n_iterations=args.iterations,
			n_groups=args.n_groups,
			group_size=args.group_size,
			powers=powers,
			seed=args.seed,
			trigger=args.trigger,
			ttl=ttl,
			ttl_width_s=args.ttl_width,
			inter_trial_s=args.inter_trial,
			pad_ms=args.pad_ms,
			mock_scores=args.mock_scores,
			relay=relay,
			via_relay=via_relay,
			f0_s=args.f0_s,
			f1_s=args.f1_s,
			frame_poll_s=args.frame_poll,
			disk_radius=args.disk_radius,
			elite_frac=args.elite_frac,
			dry_run=args.dry_run,
		)
	finally:
		log.close()
		if ttl is not None:
			ttl.close()
		if relay is not None:
			relay.disconnect()
		if pl is not None:
			pl.close()
		print(f"log: {args.log}")


if __name__ == "__main__":
	main()
