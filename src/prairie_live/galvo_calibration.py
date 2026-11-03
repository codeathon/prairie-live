"""Galvo volts ↔ live-image fraction calibration (per rig condition).

Why
---
``-gts currentScanAmplitude`` is the *imaging* scan width. Mark Points X/Y are
*uncaging* targets, and with **Use SLM** (NeuraLight) their volts-per-image
scale differs from the imaging scan. Two real PrairieView pairs on this rig, both
at zoom 1 with ``-gts`` = 4.2035577285:

* Oct 2026 pair (Use SLM likely off): FOV = 0.5 + galvo / (1 × amp)
* Nov 3 2026 pair (Use SLM on):      FOV = 0.5 + galvo / (2 × amp)

So the conversion is ``FOV = 0.5 + (galvo − center) / (k · amp)`` with a factor
``k`` that depends on the rig condition. ``k`` is measured, not assumed:
``python -m prairie_live calibrate --gpl points.gpl --run-xml run_MarkPoints.xml``
fits it from a point list (galvo) and PV's run output (image fractions) and
saves it to ``~/.prairie_live/calibration.json``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Measured defaults (see module docstring). Keys: condition name → k (x, y).
DEFAULT_K: dict[str, tuple[float, float]] = {
	"slm": (2.0, 2.0),
	"galvo": (1.0, 1.0),
}
DEFAULT_CONDITION = "slm"  # lab rig runs Mark Points with Use SLM ticked

_state: dict[str, Any] = {"k": (1.0, 1.0), "condition": "unset", "source": "module default"}


def calibration_path() -> Path:
	env = os.environ.get("PRAIRIE_LIVE_CALIBRATION")
	if env:
		return Path(env)
	return Path.home() / ".prairie_live" / "calibration.json"


def galvo_k() -> tuple[float, float]:
	"""Current (kx, ky). Width in galvo volts = k × currentScanAmplitude."""
	return _state["k"]


def describe() -> str:
	kx, ky = _state["k"]
	return f"galvo k=({kx:g},{ky:g}) [{_state['condition']}; {_state['source']}]"


def set_galvo_k(kx: float, ky: float | None = None, *, condition: str = "manual",
	source: str = "set_galvo_k") -> None:
	ky = kx if ky is None else ky
	if not kx or not ky:
		raise ValueError("galvo k must be non-zero")
	_state.update(k=(float(kx), float(ky)), condition=condition, source=source)


def load_saved() -> dict[str, Any]:
	p = calibration_path()
	try:
		return json.loads(p.read_text(encoding="utf-8"))
	except (OSError, ValueError):
		return {}


def activate(condition: str | None = None) -> str:
	"""Pick k for ``condition`` ('slm' | 'galvo'): saved calibration first, else default."""
	cond = (condition or DEFAULT_CONDITION).lower()
	saved = load_saved().get(cond)
	if isinstance(saved, dict) and saved.get("kx") and saved.get("ky"):
		set_galvo_k(saved["kx"], saved["ky"], condition=cond,
			source=f"{calibration_path()} ({saved.get('date', 'undated')})")
	elif cond in DEFAULT_K:
		kx, ky = DEFAULT_K[cond]
		set_galvo_k(kx, ky, condition=cond, source="lab default (Nov 3 2026 pair)")
	else:
		raise ValueError(f"unknown calibration condition {condition!r}")
	return describe()


def fit_pairs(
	galvo: list[tuple[float, float]], fov: list[tuple[float, float]]
) -> dict[str, float]:
	"""Per-axis least squares FOV = a·galvo + b → width = 1/a, center = (0.5−b)/a.

	Returns slope, width, center, and max residual (image fraction) per axis.
	Also reports cross-axis terms so rotation would be noticed.
	"""
	import numpy as np

	g = np.asarray(galvo, float)
	f = np.asarray(fov, float)
	if g.shape != f.shape or g.shape[0] < 2:
		raise ValueError("need ≥2 matching (galvo, fov) pairs")
	out: dict[str, float] = {"n": int(g.shape[0])}
	A = np.c_[g, np.ones(len(g))]
	for i, ax in enumerate("xy"):
		coef, *_ = np.linalg.lstsq(A, f[:, i], rcond=None)
		a_main = coef[i]
		a_cross = coef[1 - i]
		pred = A @ coef
		out[f"{ax}_slope"] = float(a_main)
		out[f"{ax}_cross"] = float(a_cross)
		out[f"{ax}_width"] = float(1.0 / a_main)
		out[f"{ax}_center"] = float((0.5 - coef[2]) / a_main)
		out[f"{ax}_max_resid"] = float(np.max(np.abs(pred - f[:, i])))
	return out


def _pairs_from_files(gpl: str, run_xml: str) -> tuple[list, list, list[str]]:
	"""Match points by order of index: .gpl Index 0.. ↔ run XML <Point Index 1..>."""
	import xml.etree.ElementTree as ET

	gp = [
		e for e in ET.parse(gpl).getroot().iter()
		if e.tag.endswith("PVGalvoPoint") and "X" in e.attrib
	]
	gp.sort(key=lambda e: int(float(e.get("Index", "0"))))
	rp: dict[int, ET.Element] = {}
	for e in ET.parse(run_xml).getroot().iter():
		if e.tag.endswith("Point") and "X" in e.attrib and "Index" in e.attrib:
			rp.setdefault(int(float(e.get("Index"))), e)
	run = [rp[k] for k in sorted(rp)]
	if len(gp) != len(run):
		raise ValueError(f"{gpl}: {len(gp)} points but {run_xml}: {len(run)} points")
	galvo = [(float(e.get("X")), float(e.get("Y"))) for e in gp]
	fov = [(float(e.get("X")), float(e.get("Y"))) for e in run]
	names = [e.get("Name") or e.get("Index") for e in gp]
	for (fx, fy) in fov:
		if not (-0.5 <= fx <= 1.5 and -0.5 <= fy <= 1.5):
			raise ValueError(f"{run_xml} does not hold image fractions (got {fx}, {fy})")
	return galvo, fov, names


def main(argv: list[str] | None = None) -> None:
	import argparse
	import datetime

	p = argparse.ArgumentParser(
		description="Fit galvo→image calibration from a PV point list + PV run output"
	)
	p.add_argument("--gpl", required=True, help="Mark Points point list export (galvo volts)")
	p.add_argument("--run-xml", required=True,
		help="run folder …_Cycle00001_MarkPoints.xml for the SAME points (image fractions)")
	p.add_argument("--amp", type=float, default=None,
		help="currentScanAmplitude at that time (-gts); default: ask --relay")
	p.add_argument("--relay", default=None, help="relay host:port to read -gts amplitude")
	p.add_argument("--condition", default=DEFAULT_CONDITION, choices=sorted(DEFAULT_K),
		help="slm = Use SLM ticked; galvo = Use SLM off")
	p.add_argument("--save", action="store_true", help=f"write to {calibration_path()}")
	a = p.parse_args(argv)

	galvo, fov, names = _pairs_from_files(a.gpl, a.run_xml)
	fit = fit_pairs(galvo, fov)
	amp_x = amp_y = a.amp
	if amp_x is None and a.relay:
		from prairie_live.relay_client import RelayClient
		from prairie_live.scan_geometry import fetch_scan_geometry

		host, _, port = a.relay.partition(":")
		c = RelayClient(host, int(port or 25100))
		c.connect()
		try:
			g = fetch_scan_geometry(c)
		finally:
			c.disconnect()
		amp_x, amp_y = g["amp_x"], g["amp_y"]
	print(f"{fit['n']} points: {', '.join(map(str, names))}")
	for ax in "xy":
		print(
			f"  {ax}: width {fit[f'{ax}_width']:.6g} V  center {fit[f'{ax}_center']:.6g} V  "
			f"cross-term {fit[f'{ax}_cross']:.3g}  max error {fit[f'{ax}_max_resid']:.2g}"
		)
	if max(abs(fit["x_cross"]), abs(fit["y_cross"])) > 1e-3:
		print("  WARNING: cross-axis terms → scan rotation; per-axis k will not be exact")
	if amp_x is None:
		print("No --amp / --relay: cannot express as k × currentScanAmplitude.")
		return
	kx = fit["x_width"] / float(amp_x)
	ky = fit["y_width"] / float(amp_y)
	print(f"  currentScanAmplitude X {amp_x:g}, Y {amp_y:g}  →  k = ({kx:.6g}, {ky:.6g})")
	if a.save:
		path = calibration_path()
		data = load_saved()
		data[a.condition] = {
			"kx": kx, "ky": ky, "amp_x": float(amp_x), "amp_y": float(amp_y),
			"center_x": fit["x_center"], "center_y": fit["y_center"],
			"date": datetime.date.today().isoformat(),
			"gpl": os.path.abspath(a.gpl), "run_xml": os.path.abspath(a.run_xml),
		}
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(json.dumps(data, indent=2), encoding="utf-8")
		print(f"saved '{a.condition}' calibration → {path}")


if __name__ == "__main__":
	main()
