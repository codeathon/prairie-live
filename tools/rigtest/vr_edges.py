"""Pair photostim TTLs with SLM-out and laser edges in a PrairieView Voltage Recording.

    py vr_edges.py rec.csv                       # list columns + edges on each
    py vr_edges.py rec.csv --ttl "AI 2" --slm "AI 1" --laser "AI 3"
    py vr_edges.py <run>_Cycle00001_VoltageRecording_001   # PV binary file

Binary recordings (IsCSV=false): rate, enabled inputs and sample count come
from the matching ``…_VoltageRecording_001.xml``; the sample format (int16 /
float32, header, interleaving) is detected and printed. Thresholds are
relative, so edge times are right even if the volt scale of a binary file is
only approximate.

For every TTL it reports: SLM-out edges since the previous TTL / in the window,
the delay to laser-on and how long the laser stayed on, and a verdict:
  OK    laser came on within --window ms of the TTL
  MISS  TTL arrived but no laser-on followed (group not fired)
Without --laser it falls back to TTL -> SLM-out pairing.

Thresholds are automatic per channel (30 % of its min-to-max swing), because
the signals differ a lot: RS-232 TTL (-1 V..+12 V), SLM out (0..5 V),
Pockels/laser command (0..~0.25 V at power 100). Override with
--ttl-thresh / --slm-thresh / --laser-thresh (volts).
"""

from __future__ import annotations

import argparse
import csv


def _session_info(path):
	"""Rate, enabled input names (channel order), SamplesAcquired from PV's xml."""
	import os
	import xml.etree.ElementTree as ET

	xml_path = path + ".xml"
	if not os.path.isfile(xml_path):
		raise SystemExit(f"binary recording needs its session file: {xml_path}")
	root = ET.parse(xml_path).getroot()
	rate = float(root.findtext(".//Rate") or 0)
	names = []
	for sig in root.iter("VRecSignal"):
		if (sig.findtext("Enabled") or "").strip().lower() == "true":
			ch = int(sig.findtext("Channel") or len(names))
			names.append((ch, (sig.findtext("Name") or f"ch{ch}").strip()))
	names = [n for _, n in sorted(names)]
	acquired = int(root.findtext(".//SamplesAcquired") or 0)
	if not rate or not names:
		raise SystemExit(f"{xml_path}: missing Rate or enabled inputs")
	return rate, names, acquired


def load_binary(path, dtype=None, header=None):
	"""Decode a PV binary Voltage Recording; return (head, rows) like load()."""
	import os

	import numpy as np

	rate, names, acquired = _session_info(path)
	nch = len(names)
	size = os.path.getsize(path)
	cands = []
	for dt in ([dtype] if dtype else ["<i2", "<f4", "<i4", ">i2", ">f4"]):
		item = np.dtype(dt).itemsize
		for n_vals in {acquired, acquired * nch}:
			if not n_vals:
				continue
			extra = size - n_vals * item
			if extra < 0:
				continue
			offs = [header] if header is not None else sorted({0, extra})
			for off in offs:
				if off + n_vals * item > size:
					continue
				for layout in ("interleaved", "blocked"):
					cands.append((dt, n_vals, off, layout))
	if not cands:
		raise SystemExit(
			f"{path}: {size} bytes does not fit {acquired} samples x {nch} inputs; "
			"pass --dtype/--header"
		)

	def decode(c):
		dt, n_vals, off, layout = c
		raw = np.fromfile(path, dtype=dt, count=n_vals, offset=off).astype(float)
		per = n_vals // nch
		raw = raw[: per * nch]
		if layout == "interleaved":
			arr = raw.reshape(per, nch)
		else:
			arr = raw.reshape(nch, per).T
		return arr

	def score(arr):
		if not np.all(np.isfinite(arr)):
			return -1e9
		rng = np.ptp(arr, axis=0)
		rng[rng == 0] = 1.0
		# Real signals are mostly flat with a few steps: small sample-to-sample
		# change relative to their range. Wrong layouts look like noise.
		rough = np.median(np.abs(np.diff(arr, axis=0)), axis=0) / rng
		big = np.abs(arr).max()
		penalty = 0 if big < 1e6 else 10
		return -float(np.mean(rough)) - penalty

	scored = sorted(((score(decode(c)), c) for c in cands), reverse=True)
	best = scored[0][1]
	arr = decode(best)
	dt, n_vals, off, layout = best
	# Same format, other header position, nearly as good → can't tell from data.
	twins = [
		c for sc, c in scored[1:]
		if c[0] == dt and c[1] == n_vals and c[3] == layout and c[2] != off
		and abs(sc - scored[0][0]) < 0.05 * abs(scored[0][0]) + 1e-9
	]
	if twins and header is None:
		print(
			f"WARNING: {size - n_vals * np.dtype(dt).itemsize} extra bytes could be a "
			f"header or a trailer; using header {off} B. If times look shifted, "
			f"re-run with --header {twins[0][2]}"
		)
	scale = 1.0
	if np.dtype(dt).kind == "i":
		# Assume +/-10 V full scale; edge times do not depend on this.
		scale = 10.0 / (2 ** (8 * np.dtype(dt).itemsize - 1))
	arr = arr * scale
	print(
		f"binary: {dt} {layout}, header {off} B, {arr.shape[0]} samples x {nch} "
		f"@ {rate:g} Hz = {arr.shape[0] / rate:.3f} s"
		+ (" (volts assume +/-10 V full scale)" if scale != 1.0 else "")
	)
	t = (np.arange(arr.shape[0]) * (1000.0 / rate)).tolist()
	rows = [[ti, *map(float, r)] for ti, r in zip(t, arr)]
	return ["Time(ms)", *names], rows


def load(path, dtype=None, header=None):
	if not path.lower().endswith(".csv"):
		return load_binary(path, dtype, header)
	with open(path, newline="", encoding="utf-8-sig") as f:
		rows = list(csv.reader(f))
	head = [h.strip() for h in rows[0]]
	data = []
	for r in rows[1:]:
		try:
			data.append([float(v) for v in r[: len(head)]])
		except ValueError:
			continue
	return head, data


def auto_thresh(v):
	lo, hi = min(v), max(v)
	if hi - lo < 0.02:
		return None  # flat channel
	return lo + 0.3 * (hi - lo)


def edges(t, v, thresh):
	ups, downs = [], []
	hi = v[0] > thresh
	for i in range(1, len(v)):
		now = v[i] > thresh
		if now and not hi:
			ups.append(t[i])
		elif hi and not now:
			downs.append(t[i])
		hi = now
	return ups, downs


def channel(name, t, v, thresh):
	th = thresh if thresh is not None else auto_thresh(v)
	print(f"\n{name}: range {min(v):.3f}..{max(v):.3f} V", end="")
	if th is None:
		print("  - FLAT (no signal on this input)")
		return [], []
	ups, downs = edges(t, v, th)
	print(f"  threshold {th:.3f} V  ->  {len(ups)} rising edges")
	for i, u in enumerate(ups):
		w = next((d - u for d in downs if d > u), None)
		gap = f"  gap {u - ups[i - 1]:9.2f} ms" if i else ""
		ws = f"  width {w:8.3f} ms" if w is not None else ""
		print(f"  #{i + 1:3d}  t={u:10.2f} ms{ws}{gap}")
	return ups, downs


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("csv", help="recording: .csv, or PV binary …_VoltageRecording_001")
	ap.add_argument("--dtype", default=None, help="binary: force e.g. <i2 or <f4")
	ap.add_argument("--header", type=int, default=None, help="binary: header bytes")
	ap.add_argument("--ttl", help="column of the photostim TTL (Trig In 1)")
	ap.add_argument("--slm", help="column of SLM Out")
	ap.add_argument("--laser", help="column of the Pockels / uncaging laser command")
	ap.add_argument("--ttl-thresh", type=float, default=None)
	ap.add_argument("--slm-thresh", type=float, default=None)
	ap.add_argument("--laser-thresh", type=float, default=None)
	ap.add_argument(
		"--window",
		type=float,
		default=500.0,
		help="ms after a TTL in which laser-on (or SLM out) must start",
	)
	a = ap.parse_args()

	head, data = load(a.csv, a.dtype, a.header)
	print("columns:", head, f"| {len(data)} samples", end="")
	if len(data) > 1 and data[1][0] > data[0][0]:
		print(f" | {1000.0 / (data[1][0] - data[0][0]):.0f} Hz (if time is ms)")
	else:
		print()
	t = [r[0] for r in data]

	def col(name):
		if name not in head:
			raise SystemExit(f"column {name!r} not found; columns are: {head}")
		i = head.index(name)
		return [r[i] for r in data]

	if not (a.ttl or a.slm or a.laser):
		for i, h in enumerate(head[1:], start=1):
			channel(h, t, [r[i] for r in data], None)
		print("\nNext: re-run with --ttl / --slm / --laser set to the right columns.")
		return

	ttl = channel(f"TTL [{a.ttl}]", t, col(a.ttl), a.ttl_thresh)[0] if a.ttl else []
	slm = channel(f"SLM out [{a.slm}]", t, col(a.slm), a.slm_thresh)[0] if a.slm else []
	las_up, las_dn = (
		channel(f"laser [{a.laser}]", t, col(a.laser), a.laser_thresh)
		if a.laser
		else ([], [])
	)
	if not ttl:
		return

	print(f"\nPer TTL (window {a.window:g} ms):")
	ok = miss = 0
	used_l = set()
	lat = []
	for i, tt in enumerate(ttl):
		nxt = ttl[i + 1] if i + 1 < len(ttl) else float("inf")
		slm_here = [s for s in slm if tt - 1.0 <= s < nxt]
		slm_txt = ", ".join(f"+{s - tt:.2f}" for s in slm_here) or "none"
		line = f"  TTL {i + 1:3d} t={tt:10.2f} ms  SLM out [{slm_txt}] ms"
		if a.laser:
			j = next(
				(
					k
					for k, u in enumerate(las_up)
					if k not in used_l and tt <= u <= tt + a.window
				),
				None,
			)
			if j is None:
				miss += 1
				print(line + "  ->  MISS: no laser-on")
				continue
			used_l.add(j)
			u = las_up[j]
			w = next((d - u for d in las_dn if d > u), None)
			lat.append(u - tt)
			ok += 1
			ws = f", on {w:.2f} ms" if w is not None else ""
			print(line + f"  ->  OK: laser +{u - tt:.2f} ms{ws}")
		else:
			if slm_here and slm_here[0] - tt <= a.window:
				ok += 1
				lat.append(slm_here[0] - tt)
				print(line + "  ->  SLM out after TTL")
			else:
				miss += 1
				print(line + "  ->  no SLM out after this TTL (see note)")
	extra = [u for k, u in enumerate(las_up) if k not in used_l] if a.laser else []
	for u in extra:
		print(f"  laser-on t={u:10.2f} ms with NO TTL before it")
	what = "laser" if a.laser else "SLM out"
	summary = (
		f"\nSUMMARY: {len(ttl)} TTL | {len(slm)} SLM out | "
		f"{ok} OK | {miss} MISS ({what})"
	)
	if a.laser:
		summary += f" | {len(extra)} unexplained laser-on"
	print(summary)
	if lat:
		ls = sorted(lat)
		print(
			f"  TTL->{what} ms: min {ls[0]:.2f}  median {ls[len(ls) // 2]:.2f}  "
			f"max {ls[-1]:.2f}"
		)
	if not a.laser:
		print(
			"  Note: the next group's mask may load right after the previous burn,"
			" so a TTL without its own SLM out is not proof of a miss - add --laser."
		)


if __name__ == "__main__":
	main()
