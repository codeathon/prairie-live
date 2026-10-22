"""Map Mark Points X/Y onto the live image (0–1 of width/height).

PV saves Mark Points positions in galvo units (rig: about ±3.5), the same
units as its scan state: the frame spans currentScanCenter ± currentScanAmplitude/2
on each axis, and a negative amplitude means that axis is scanned reversed.
Points already inside 0–1 are image fractions and are used as is.
"""

from __future__ import annotations

from typing import Any

_KEYS = (
	("amp_x", "currentScanAmplitude", "XAxis"),
	("amp_y", "currentScanAmplitude", "YAxis"),
	("ctr_x", "currentScanCenter", "XAxis"),
	("ctr_y", "currentScanCenter", "YAxis"),
)


def _num(text: Any) -> float | None:
	for tok in str(text or "").replace(",", " ").split():
		try:
			return float(tok)
		except ValueError:
			continue
	return None


def fetch_scan_geometry(client) -> dict[str, float] | None:
	"""Scan amplitude/center from PV via the relay; None if PV does not answer."""
	geom: dict[str, float] = {}
	for name, key, index in _KEYS:
		out = client.get_state(key, index)
		val = _num(out.get("value")) if out.get("ok", True) else None
		if val is None:
			return None
		geom[name] = val
	if not geom["amp_x"] or not geom["amp_y"]:
		return None
	return geom


def is_fraction_space(points: list[dict]) -> bool:
	return bool(points) and all(
		0.0 <= float(p["x"]) <= 1.0 and 0.0 <= float(p["y"]) <= 1.0 for p in points
	)


def overlay_points(points: list[dict], geom: dict[str, float] | None) -> dict:
	"""Points as image fractions for drawing, plus a note when they cannot be placed."""
	if is_fraction_space(points):
		return {"points": [_row(p, float(p["x"]), float(p["y"]), None) for p in points], "note": ""}
	if not geom:
		return {
			"points": [],
			"note": "Mark Points are in galvo units; waiting for PV scan range from the relay",
		}
	ax, ay = geom["amp_x"], geom["amp_y"]
	rows = []
	for p in points:
		fx = 0.5 + (float(p["x"]) - geom["ctr_x"]) / ax
		fy = 0.5 + (float(p["y"]) - geom["ctr_y"]) / ay
		size = p.get("spiral_size_fov") or p.get("spiral_width")
		r = abs(float(size) / ax) / 2 if size not in (None, "") else None
		rows.append(_row(p, fx, fy, r))
	n_out = sum(1 for r in rows if not (0 <= r["fx"] <= 1 and 0 <= r["fy"] <= 1))
	note = (
		f"PV scan X {ax:g} @ {geom['ctr_x']:g}, Y {ay:g} @ {geom['ctr_y']:g}"
		+ (f" · {n_out} point(s) outside the current scan" if n_out else "")
	)
	return {"points": rows, "note": note}


def _row(p: dict, fx: float, fy: float, r: float | None) -> dict:
	return {
		"id": str(p.get("id")),
		"name": str(p.get("name") or p.get("id")),
		"fx": round(fx, 5),
		"fy": round(fy, 5),
		"r": round(r, 5) if r else None,
	}
