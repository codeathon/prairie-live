"""Map Mark Points X/Y onto the live image (0–1 of width/height).

PV saves Mark Points positions in galvo units, the same units as its scan
state. currentScanAmplitude is the half-width: the frame spans
currentScanCenter ± currentScanAmplitude on each axis (rig, zoom 1: ±4.2, with
picked points out to ±3.75). A negative amplitude means that axis is reversed.
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


def fetch_scan_geometry(client) -> dict[str, float]:
	"""Scan amplitude/center from PV via the relay; ValueError says which read failed."""
	geom: dict[str, float] = {}
	for name, key, index in _KEYS:
		out = client.get_state(key, index)
		if not out.get("ok", True):
			raise ValueError(f"{key} {index}: {out.get('error') or 'relay error'}")
		val = _num(out.get("value"))
		if val is None:
			raise ValueError(f"{key} {index}: PV replied {out.get('value')!r}")
		geom[name] = val
	if not geom["amp_x"] or not geom["amp_y"]:
		raise ValueError(f"scan amplitude is 0 (X {geom['amp_x']}, Y {geom['amp_y']})")
	return geom


def is_fraction_space(points: list[dict]) -> bool:
	return bool(points) and all(
		0.0 <= float(p["x"]) <= 1.0 and 0.0 <= float(p["y"]) <= 1.0 for p in points
	)


def overlay_points(
	points: list[dict], geom: dict[str, float] | None, error: str = ""
) -> dict:
	"""Points as image fractions for drawing, plus a note when they cannot be placed."""
	if is_fraction_space(points):
		return {"points": [_row(p, float(p["x"]), float(p["y"]), None) for p in points], "note": ""}
	if not geom:
		why = f" ({error})" if error else ""
		return {
			"points": [],
			"note": "Mark Points are in galvo units; waiting for PV scan range "
			f"from the relay{why}",
		}
	ax, ay = 2.0 * geom["amp_x"], 2.0 * geom["amp_y"]
	rows = []
	for p in points:
		fx = 0.5 + (float(p["x"]) - geom["ctr_x"]) / ax
		fy = 0.5 + (float(p["y"]) - geom["ctr_y"]) / ay
		size = p.get("spiral_size_fov") or p.get("spiral_width")
		r = abs(float(size) / ax) / 2 if size not in (None, "") else None
		rows.append(_row(p, fx, fy, r))
	n_out = sum(1 for r in rows if not (0 <= r["fx"] <= 1 and 0 <= r["fy"] <= 1))
	note = (
		f"PV scan X {geom['ctr_x']:g} ± {geom['amp_x']:g}, "
		f"Y {geom['ctr_y']:g} ± {geom['amp_y']:g}"
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
