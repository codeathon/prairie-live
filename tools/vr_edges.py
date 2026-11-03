"""Pair photostim TTLs with SLM-out and laser edges in a PrairieView Voltage Recording.

    py vr_edges.py rec.csv                       # list columns + edges on each
    py vr_edges.py rec.csv --ttl "AI 2" --slm "AI 1" --laser "AI 3"
    py vr_edges.py <run>_Cycle00001_VoltageRecording_001   # PV binary file

Binary recordings (IsCSV=false): int16 little-endian, inputs interleaved,
data from byte 0 (confirmed on the rig, PV 6.1). Rate, input names and
SamplesAcquired come from the matching ``…_VoltageRecording_001.xml``. Volts
are approximate (raw x 10/32768); thresholds are relative, so edge times are
exact regardless of scale.

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
	chans = []
	for sig in root.iter("VRecSignal"):
		if (sig.findtext("Enabled") or "").strip().lower() == "true":
			ch = int(sig.findtext("Channel") or len(chans))
			chans.append((ch, (sig.findtext("Name") or f"ch{ch}").strip()))
	names = [n for _, n in sorted(chans)]
	acquired = int(root.findtext(".//SamplesAcquired") or 0)
	if not rate or not names or not acquired:
		raise SystemExit(f"{xml_path}: missing Rate, enabled inputs or SamplesAcquired")
	return rate, names, acquired


def load_binary(path):
	"""PV binary Voltage Recording → (head, rows) like the CSV loader.

	Layout confirmed on PV 6.1 (Nov 3 2026 rig file): int16 little-endian,
	enabled inputs interleaved in channel order, data from byte 0,
	SamplesAcquired samples per input, then padding.
	"""
	import os
	import struct

	rate, names, per = _session_info(path)
	nch = len(names)
	need = per * nch * 2
	size = os.path.getsize(path)
	if size < need:
		per = size // (2 * nch)
		need = per * nch * 2
		print(f"note: file holds only {per} samples per input")
	with open(path, "rb") as f:
		raw = f.read(need)
	vals = struct.unpack(f"<{per * nch}h", raw)
	scale = 10.0 / 32768.0  # approximate volts; thresholds are relative
	dt = 1000.0 / rate
	rows = [
		[i * dt, *(vals[i * nch + c] * scale for c in range(nch))]
		for i in range(per)
	]
	print(
		f"binary: int16 interleaved, {per} samples x {nch} inputs @ {rate:g} Hz "
		f"= {per / rate:.3f} s ({size - need} B padding; volts approximate)"
	)
	return ["Time(ms)", *names], rows


def resolve_recording(path):
	"""Accept a run folder or any file in it; return the VoltageRecording data file."""
	import glob
	import os

	if os.path.isdir(path):
		folder = path
	elif path.lower().endswith(".csv") or path.endswith("_VoltageRecording_001"):
		return path
	else:
		folder = os.path.dirname(path) or "."
	hits = sorted(
		glob.glob(os.path.join(folder, "*_VoltageRecording_0*.csv"))
		+ [
			f for f in glob.glob(os.path.join(folder, "*_VoltageRecording_0*"))
			if os.path.splitext(f)[1] == ""
		]
	)
	if not hits:
		raise SystemExit(f"no *_VoltageRecording_* data file in {folder}")
	if len(hits) > 1:
		print(f"note: {len(hits)} recordings in folder, using {os.path.basename(hits[0])}")
	print(f"recording: {hits[0]}")
	return hits[0]


def load(path):
	path = resolve_recording(path)
	if not path.lower().endswith(".csv"):
		return load_binary(path)
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

	head, data = load(a.csv)
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
