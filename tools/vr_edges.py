"""Pair photostim TTLs with SLM-out and laser edges in a PrairieView Voltage Recording.

    py vr_edges.py <run folder>  --ttl "AI 2" --slm "AI 1"
    py vr_edges.py <run folder>  --ttl "AI 2" --slm "AI 1" --laser "AI 3"
    py vr_edges.py <run folder>                 # just list inputs + edge counts
    add --verbose to also list every edge
    add --rows to print the raw samples around every TTL / SLM-out change
        (--context N samples before/after each change, default 3)

Accepts a run folder, the binary ``…_VoltageRecording_001`` file, any file in
the run folder, or a .csv. Vectorised (numpy), so long recordings are quick.

Binary recordings (IsCSV=false), confirmed on PV 6.1 (Nov 3 2026): int16
little-endian, enabled inputs interleaved in channel order, data from byte 0,
SamplesAcquired samples per input, then padding. Rate, input names and
SamplesAcquired come from the matching ``…_VoltageRecording_001.xml``.
Volts are approximate (raw x 10/32768); thresholds are relative (30 % of each
input's swing), so edge times are exact regardless of scale.

Per TTL verdict:
  OK    laser came on within --window ms of the TTL (with --laser), or an
        SLM-out edge followed it (without --laser)
  MISS  nothing followed the TTL
Note: the next group's mask can load right after the previous burn, so with
SLM out alone a TTL without its own SLM edge is not proof of a miss.
"""

from __future__ import annotations

import argparse
import glob
import os
import xml.etree.ElementTree as ET

import numpy as np

# ---------------------------------------------------------------- loading


def _session_info(path: str):
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


def load_binary(path: str):
	rate, names, per = _session_info(path)
	nch = len(names)
	size = os.path.getsize(path)
	per = min(per, size // (2 * nch))
	raw = np.fromfile(path, dtype="<i2", count=per * nch)
	data = raw.reshape(per, nch).astype(np.float32) * (10.0 / 32768.0)
	print(f"binary: {per} samples x {nch} inputs @ {rate:g} Hz = {per / rate:.3f} s")
	return rate, names, data


def load_csv(path: str):
	with open(path, encoding="utf-8-sig") as f:
		head = [h.strip() for h in f.readline().split(",")]
	arr = np.genfromtxt(path, delimiter=",", skip_header=1, dtype=np.float64)
	arr = arr[~np.isnan(arr).any(axis=1)]
	t = arr[:, 0]
	rate = 1000.0 / float(np.median(np.diff(t[:1000]))) if len(t) > 1 else 0.0
	print(f"csv: {len(t)} samples x {len(head) - 1} inputs @ {rate:.0f} Hz (if time is ms)")
	return rate, head[1:], arr[:, 1:].astype(np.float32)


def resolve_recording(path: str) -> str:
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
		print(f"note: {len(hits)} recordings in folder, using the first")
	print(f"recording: {hits[0]}")
	return hits[0]


def load(path: str):
	path = resolve_recording(path)
	if path.lower().endswith(".csv"):
		return load_csv(path)
	return load_binary(path)


# ---------------------------------------------------------------- edges


def find_edges(v: np.ndarray, dt_ms: float, thresh: float | None):
	"""Rising-edge times, falling-edge times (ms) and the threshold used."""
	lo, hi = float(v.min()), float(v.max())
	if thresh is None:
		if hi - lo < 0.02:
			return np.array([]), np.array([]), None
		thresh = lo + 0.3 * (hi - lo)
	high = v > thresh
	d = np.diff(high.astype(np.int8))
	ups = (np.flatnonzero(d == 1) + 1) * dt_ms
	downs = (np.flatnonzero(d == -1) + 1) * dt_ms
	return ups, downs, thresh


def widths(ups: np.ndarray, downs: np.ndarray) -> np.ndarray:
	if not len(ups):
		return np.array([])
	idx = np.searchsorted(downs, ups, side="right")
	w = np.full(len(ups), np.nan)
	ok = idx < len(downs)
	w[ok] = downs[idx[ok]] - ups[ok]
	return w


def describe(name, v, ups, downs, th, verbose):
	if th is None:
		print(f"  {name:<14} range {v.min():7.3f}..{v.max():7.3f} V  FLAT")
		return
	w = widths(ups, downs)
	wtxt = f"width {np.nanmedian(w):.3f} ms" if np.isfinite(w).any() else ""
	print(
		f"  {name:<14} range {v.min():7.3f}..{v.max():7.3f} V  thr {th:6.3f} V  "
		f"{len(ups):4d} edges  {wtxt}"
	)
	if verbose:
		for i, u in enumerate(ups):
			gap = f"  gap {u - ups[i - 1]:9.2f} ms" if i else ""
			print(f"      #{i + 1:3d} t={u:10.2f} ms  width {w[i]:8.3f} ms{gap}")


def print_change_rows(data, names, dt, sig, cols, k):
	"""Raw samples around each rising/falling edge of the chosen inputs."""
	marks: dict[int, list[str]] = {}
	for role, (ups, downs) in sig.items():
		for times, kind in ((ups, "rise"), (downs, "fall")):
			for t_ms in times:
				i = int(round(t_ms / dt))
				marks.setdefault(i, []).append(f"{role} {kind}")
	if not marks:
		print("\n(no changes to show)")
		return
	show = sorted({j for i in marks for j in range(i - k, i + k + 1)
		if 0 <= j < len(data)})
	order = [n for n in (cols.get("TTL"), cols.get("SLM out"), cols.get("laser")) if n]
	order += [n for n in names if n not in order]
	idx = [names.index(n) for n in order]
	print(f"\nraw samples around each change (+/- {k} samples, {dt:.3f} ms apart):")
	print(f"  {'sample':>9} {'time ms':>11} " + " ".join(f"{n:>9}" for n in order))
	prev = None
	for j in show:
		if prev is not None and j != prev + 1:
			print("  " + "-" * (22 + 10 * len(order)))
		vals = " ".join(f"{data[j, c]:9.3f}" for c in idx)
		tag = ("  <- " + ", ".join(marks[j])) if j in marks else ""
		print(f"  {j:9d} {j * dt:11.3f} {vals}{tag}")
		prev = j


# ---------------------------------------------------------------- main


def main():
	ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
	ap.add_argument("path", help="run folder, VoltageRecording file, or .csv")
	ap.add_argument("--ttl", help="input carrying the photostim TTL (Trig In 1)")
	ap.add_argument("--slm", help="input carrying SLM Out")
	ap.add_argument("--laser", help="input carrying the uncaging laser / Pockels command")
	ap.add_argument("--ttl-thresh", type=float, default=None)
	ap.add_argument("--slm-thresh", type=float, default=None)
	ap.add_argument("--laser-thresh", type=float, default=None)
	ap.add_argument("--window", type=float, default=500.0,
		help="ms after a TTL in which laser-on (or SLM out) must start")
	ap.add_argument("--verbose", action="store_true", help="list every edge")
	ap.add_argument("--rows", action="store_true",
		help="print raw samples around every TTL / SLM-out / laser change")
	ap.add_argument("--context", type=int, default=3,
		help="samples before/after each change for --rows (default 3)")
	a = ap.parse_args()

	rate, names, data = load(a.path)
	dt = 1000.0 / rate

	def col(name):
		if name not in names:
			raise SystemExit(f"input {name!r} not found; inputs are: {names}")
		return data[:, names.index(name)]

	print("\ninputs:")
	if not (a.ttl or a.slm or a.laser):
		for i, n in enumerate(names):
			ups, downs, th = find_edges(data[:, i], dt, None)
			describe(n, data[:, i], ups, downs, th, a.verbose)
		print("\nNext: re-run with --ttl / --slm (/ --laser) set to the right inputs.")
		return

	sig = {}
	for role, name, th in (
		("TTL", a.ttl, a.ttl_thresh),
		("SLM out", a.slm, a.slm_thresh),
		("laser", a.laser, a.laser_thresh),
	):
		if name:
			v = col(name)
			ups, downs, used_th = find_edges(v, dt, th)
			sig[role] = (ups, downs)
			describe(f"{role} [{name}]", v, ups, downs, used_th, a.verbose)

	if a.rows:
		print_change_rows(data, names, dt, sig,
			{"TTL": a.ttl, "SLM out": a.slm, "laser": a.laser}, a.context)

	ttl = sig.get("TTL", (np.array([]), None))[0]
	slm = sig.get("SLM out", (np.array([]), None))[0]
	las_up, las_dn = sig.get("laser", (np.array([]), np.array([])))
	if not len(ttl):
		print("\nno TTL edges found")
		return

	print(f"\nper TTL (window {a.window:g} ms; SLM times relative to that TTL):")
	used = np.zeros(len(las_up), bool)
	ok = miss = 0
	lat = []
	for i, tt in enumerate(ttl):
		nxt = ttl[i + 1] if i + 1 < len(ttl) else np.inf
		here = slm[(slm >= tt - 1.0) & (slm < nxt)]
		slm_txt = ", ".join(f"+{s - tt:.2f}" for s in here[:6]) or "none"
		if len(here) > 6:
			slm_txt += f", ... ({len(here)} total)"
		line = f"  TTL {i + 1:3d} at {tt / 1000:9.3f} s  SLM out [{slm_txt}] ms"
		if a.laser:
			cand = np.flatnonzero(~used & (las_up >= tt) & (las_up <= tt + a.window))
			if not len(cand):
				miss += 1
				print(line + "  ->  MISS: no laser-on")
				continue
			j = cand[0]
			used[j] = True
			w = widths(las_up[j:j + 1], las_dn)[0]
			lat.append(las_up[j] - tt)
			ok += 1
			print(line + f"  ->  OK: laser +{las_up[j] - tt:.2f} ms, on {w:.2f} ms")
		else:
			if len(here) and here[0] - tt <= a.window:
				ok += 1
				lat.append(here[0] - tt)
				print(line + "  ->  OK")
			else:
				miss += 1
				print(line + "  ->  no SLM out after this TTL")

	before = slm[slm < ttl[0] - 1.0]
	if len(before):
		print(f"  {len(before)} SLM out edge(s) BEFORE the first TTL "
			f"(first at {before[0] / 1000:.3f} s)")
	extra = int((~used).sum()) if a.laser else 0
	what = "laser" if a.laser else "SLM out"
	print(
		f"\nSUMMARY: {len(ttl)} TTL | {len(slm)} SLM out | {ok} OK | {miss} MISS ({what})"
		+ (f" | {extra} laser-on with no TTL" if a.laser else "")
	)
	if lat:
		ls = np.sort(np.array(lat))
		print(f"  TTL -> {what}: min {ls[0]:.2f}  median {np.median(ls):.2f}  "
			f"max {ls[-1]:.2f} ms")
	if not a.laser and miss:
		print("  (a missing SLM edge alone is not proof the group did not fire: "
			"the next mask can load right after the previous burn)")


if __name__ == "__main__":
	main()
