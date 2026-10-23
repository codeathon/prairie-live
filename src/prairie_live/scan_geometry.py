"""Map Mark Points X/Y onto the live image (0–1 of width/height).

PV saves Mark Points positions in galvo units, the same units as its scan
state. currentScanAmplitude is the half-width: the frame spans
currentScanCenter ± currentScanAmplitude on each axis (rig, zoom 1: ±4.2, with
picked points out to ±3.75). A negative amplitude means that axis is reversed.
Points already inside 0–1 are image fractions and are used as is.
"""

from __future__ import annotations

from typing import Any

# Any: relay client with get_state (avoid importing relay_client here).

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


def galvo_to_fraction(x: float, y: float, geom: dict[str, float]) -> tuple[float, float]:
	"""Galvo voltage → image fraction; amplitude is the half-width of the scan."""
	ax = 2.0 * float(geom["amp_x"])
	ay = 2.0 * float(geom["amp_y"])
	return (
		0.5 + (float(x) - float(geom["ctr_x"])) / ax,
		0.5 + (float(y) - float(geom["ctr_y"])) / ay,
	)


def fraction_to_galvo(fx: float, fy: float, geom: dict[str, float]) -> tuple[float, float]:
	"""Image fraction → galvo voltage for the current scan window."""
	ax = 2.0 * float(geom["amp_x"])
	ay = 2.0 * float(geom["amp_y"])
	return (
		float(geom["ctr_x"]) + (float(fx) - 0.5) * ax,
		float(geom["ctr_y"]) + (float(fy) - 0.5) * ay,
	)


def points_for_image(
	points: list[dict] | None, geom: dict[str, float] | None
) -> list[dict]:
	"""Copies of points with X/Y as 0–1 image fractions (for ΔF/F disks and rings).

	Already-fraction points are returned unchanged. Galvo points need geom;
	without it they are left as-is (disk_mean then skips them as OOB).
	"""
	if not points:
		return []
	if is_fraction_space(points) or not geom:
		return list(points)
	out: list[dict] = []
	for p in points:
		row = dict(p)
		fx, fy = galvo_to_fraction(float(p["x"]), float(p["y"]), geom)
		row["x"], row["y"] = fx, fy
		row["x_galvo"] = float(p["x"])
		row["y_galvo"] = float(p["y"])
		row["coord_space"] = "fov"
		# SpiralSize on PV-saved galvo XML is already galvo volts.
		if row.get("spiral_size_fov") not in (None, "") or row.get("spiral_width") not in (
			None,
			"",
		):
			row["spiral_space"] = "galvo"
		out.append(row)
	return out


def prepare_points_for_live_fov(
	points: list[dict],
	relay: Any | None,
) -> tuple[list[dict], str]:
	"""Lock Mark Points to the live FOV (0–1), matching the cyan overlay.

	Galvo XML is mapped through live ``currentScanAmplitude`` / ``Center``.
	Series later converts these fractions back to galvo at fire time so burns
	land on the same spots the overlay shows — even after you pan/zoom.
	"""
	if not points:
		return [], "no points"
	if is_fraction_space(points):
		out = []
		for p in points:
			row = dict(p)
			row["coord_space"] = "fov"
			# SpiralWidth on FOV XMLs is a fraction of the FOV, not galvo volts.
			if row.get("spiral_width") not in (None, "") or row.get("spiral_size_um") not in (
				None,
				"",
			):
				row["spiral_space"] = "fov"
			out.append(row)
		return out, "points already in FOV fractions (0–1); fire tracks live FOV"
	if relay is None:
		raise ValueError(
			"galvo Mark Points need the relay (Live + scan range) to convert "
			"to FOV fractions"
		)
	geom = fetch_scan_geometry(relay)
	mapped = points_for_image(points, geom)
	n_out = sum(
		1
		for p in mapped
		if not (0.0 <= float(p["x"]) <= 1.0 and 0.0 <= float(p["y"]) <= 1.0)
	)
	note = (
		f"mapped {len(mapped)} galvo points → live FOV via scan "
		f"X {geom['ctr_x']:g}±{geom['amp_x']:g}, "
		f"Y {geom['ctr_y']:g}±{geom['amp_y']:g}"
	)
	if n_out:
		note += f" · {n_out} still outside 0–1 at this zoom (will not burn on FOV)"
	return mapped, note


def prepare_points_for_slm(
	points: list[dict],
	relay: Any | None,
) -> tuple[list[dict], str]:
	"""Alias: ``-MarkAllPoints`` needs FOV fractions (same as live FOV lock)."""
	return prepare_points_for_live_fov(points, relay)


def _clamp_galvo(v: float, ctr: float, amp: float) -> float:
	"""Keep galvo within the live scan ± a little margin (DAC Int32 overflow)."""
	half = abs(float(amp))
	lo, hi = float(ctr) - half * 1.05, float(ctr) + half * 1.05
	# Absolute DAC rail ~±10 V on this class of scanners.
	lo, hi = max(lo, -10.0), min(hi, 10.0)
	return min(max(float(v), lo), hi)


def _spiral_size_galvo(p: dict, geom: dict[str, float]) -> str | None:
	"""SpiralSize for series XML must be galvo units, never µm."""
	ax = abs(2.0 * float(geom["amp_x"]))
	raw = p.get("spiral_size_fov")
	if raw in (None, ""):
		raw = p.get("spiral_width")
	space = str(p.get("spiral_space") or "")
	if raw not in (None, ""):
		s = float(raw)
		if space == "fov":
			s = s * ax
		elif space == "galvo":
			pass
		elif s > ax * 1.5:
			# Looks like µm or a bad token — convert via optics if possible.
			um = p.get("spiral_size_um")
			fov_um = p.get("fov_width_um")
			if um not in (None, "") and fov_um not in (None, ""):
				s = (float(um) / float(fov_um)) * ax
			else:
				return None
		s = min(max(s, 0.0), ax)
		return f"{s:.6g}"
	um = p.get("spiral_size_um")
	fov_um = p.get("fov_width_um")
	if um not in (None, "") and fov_um not in (None, ""):
		s = min(max((float(um) / float(fov_um)) * ax, 0.0), ax)
		return f"{s:.6g}"
	return None


def points_to_galvo_for_series(
	points: list[dict],
	relay: Any | None,
	*,
	fov_width_um: float | None = None,
) -> tuple[list[dict], str]:
	"""FOV-fraction points → galvo voltages for ``-LoadMarkPoints`` on the live FOV.

	Also rewrites spiral size into galvo units and clamps X/Y so PV's spiral
	DAC path cannot Int32-overflow (seen when switching SLM → series).
	"""
	if not points:
		return [], "no points"
	if not is_fraction_space(points) and not any(
		p.get("coord_space") == "fov" for p in points
	):
		return list(points), "series using native galvo X/Y (not FOV-locked)"
	if relay is None:
		raise ValueError(
			"FOV-locked series needs the relay to convert fractions → galvo "
			"for the current scan window"
		)
	geom = fetch_scan_geometry(relay)
	out: list[dict] = []
	n_out = 0
	for p in points:
		row = dict(p)
		if fov_width_um is not None:
			row["fov_width_um"] = float(fov_width_um)
		fx, fy = float(p["x"]), float(p["y"])
		if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
			n_out += 1
		gx, gy = fraction_to_galvo(fx, fy, geom)
		gx = _clamp_galvo(gx, geom["ctr_x"], geom["amp_x"])
		gy = _clamp_galvo(gy, geom["ctr_y"], geom["amp_y"])
		row["x_fov"], row["y_fov"] = fx, fy
		row["x"], row["y"] = gx, gy
		row["coord_space"] = "galvo"
		# Drop µm SpiralSizeInMicrons on galvo XML — PV spiral DAC mis-reads it
		# after an SLM run and can throw Int32 overflow.
		row["spiral_size_um"] = ""
		sg = _spiral_size_galvo(row, geom)
		if sg is not None:
			row["spiral_size_fov"] = sg
			row["spiral_width"] = sg
			row["spiral_height"] = sg
			row["spiral_space"] = "galvo"
		else:
			row["spiral_size_fov"] = ""
			row["spiral_width"] = ""
			row["spiral_height"] = ""
		out.append(row)
	note = (
		f"series fire: FOV→galvo via scan "
		f"X {geom['ctr_x']:g}±{geom['amp_x']:g}, "
		f"Y {geom['ctr_y']:g}±{geom['amp_y']:g}"
	)
	if n_out:
		note += f" · {n_out} FOV point(s) outside 0–1"
	return out, note


def overlay_points(
	points: list[dict], geom: dict[str, float] | None, error: str = ""
) -> dict:
	"""Points as image fractions for drawing, plus a note when they cannot be placed.

	No note when the pool is empty — a geometry-only note was shrinking the live
	FOV about a second after load, before any Mark Points file was chosen.
	"""
	if not points:
		return {"points": [], "note": ""}
	if is_fraction_space(points):
		return {"points": [_row(p, float(p["x"]), float(p["y"]), None) for p in points], "note": ""}
	if not geom:
		why = f" ({error})" if error else ""
		return {
			"points": [],
			"note": "Mark Points are in galvo units; waiting for PV scan range "
			f"from the relay{why}",
		}
	ax = 2.0 * geom["amp_x"]
	rows = []
	for p in points:
		fx, fy = galvo_to_fraction(float(p["x"]), float(p["y"]), geom)
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
