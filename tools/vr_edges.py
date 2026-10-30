"""Find SLM-out (and optional laser-gate) edges in a PrairieView Voltage Recording CSV.

    python vr_edges.py VoltageRecording_001.csv
    python vr_edges.py rec.csv --slm "Input 0" --laser "Input 1" --thresh 1.5

Prints each rising edge time (ms from recording start), pulse widths, and the gaps
between edges, so you can check: did every group fire, and how far apart.
PV CSVs: first column is time (ms); other columns are inputs. Column names vary
by version, so pass --slm / --laser if the defaults (2nd / 3rd column) are wrong.
"""

from __future__ import annotations

import argparse
import csv


def load(path):
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


def report(name, t, v, thresh):
	ups, downs = edges(t, v, thresh)
	print(f"\n{name}: {len(ups)} rising edges (threshold {thresh} V, range "
		f"{min(v):.2f}…{max(v):.2f} V)")
	for i, u in enumerate(ups):
		w = next((d - u for d in downs if d > u), None)
		gap = f"  gap {u - ups[i - 1]:9.2f} ms" if i else ""
		ws = f"  width {w:7.2f} ms" if w is not None else ""
		print(f"  #{i + 1:3d}  t={u:10.2f} ms{ws}{gap}")
	return ups


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("csv")
	ap.add_argument("--slm", help="column name for SLM out (default: 2nd column)")
	ap.add_argument("--laser", help="column name for laser gate (optional)")
	ap.add_argument("--thresh", type=float, default=1.5, help="volts")
	a = ap.parse_args()
	head, data = load(a.csv)
	print("columns:", head, f"· {len(data)} samples")
	t = [r[0] for r in data]

	def col(name, default_idx):
		i = head.index(name) if name else default_idx
		return head[i], [r[i] for r in data]

	n, v = col(a.slm, 1)
	slm = report(f"SLM out [{n}]", t, v, a.thresh)
	if a.laser or len(head) > 2:
		n2, v2 = col(a.laser, 2)
		las = report(f"laser [{n2}]", t, v2, a.thresh)
		if slm and las:
			print("\nlaser-on lag after each SLM edge:")
			for i, s in enumerate(slm):
				nxt = next((x for x in las if x >= s), None)
				print(f"  group {i + 1}: " + (f"{nxt - s:.2f} ms" if nxt is not None else "no laser edge"))


if __name__ == "__main__":
	main()
