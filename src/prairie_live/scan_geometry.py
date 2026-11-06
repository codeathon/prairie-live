"""Map Mark Points X/Y onto the live image (0–1 of width/height).

PV saves Mark Points positions in galvo units, the same units as its scan
state. currentScanAmplitude is the half-width: the frame spans
currentScanCenter ± currentScanAmplitude on each axis (rig, zoom 1: ±4.2, with
picked points out to ±3.75). A negative amplitude means that axis is reversed.
Points already inside 0–1 are image fractions and are used as is.

Optical zoom shrinks the imaged FOV (µm) and should shrink the galvo span of
the frame the same way. Some PV builds keep reporting the zoom-1 amplitude
while Live is at Zoom 1.5 — we detect that and scale mapping by optical zoom
using the scope defaults (757.8 µm FOV, 1.48 µm/px at zoom 1.5).
"""

from __future__ import annotations

from typing import Any

from prairie_live.lab_defaults import (
	DEFAULT_FOV_HEIGHT_UM,
	DEFAULT_FOV_WIDTH_UM,
	DEFAULT_IMAGE_PX,
	DEFAULT_OPTICAL_ZOOM,
	DEFAULT_PIXEL_SIZE_UM,
	DEFAULT_SCAN_AMP_ZOOM1,
)

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


def _try_state(client, key: str, index: str | None = None) -> float | None:
	try:
		out = client.get_state(key, index) if index is not None else client.get_state(key)
	except Exception:
		return None
	if not out.get("ok", True):
		return None
	return _num(out.get("value"))


def effective_scan_amplitude(
	amp: float,
	*,
	optical_zoom: float,
	amp_zoom1: float = DEFAULT_SCAN_AMP_ZOOM1,
) -> float:
	"""Half-width to use for galvo→FOV mapping at the current optical zoom.

	If PV already scaled ``currentScanAmplitude`` with zoom, keep it. If it still
	looks like the zoom-1 value (±15%) while zoom > 1, divide by zoom so
	overlays match the narrower Live FOV.
	"""
	z = float(optical_zoom or 1.0)
	a = float(amp)
	if z <= 1.0001 or not amp_zoom1:
		return a
	ref = abs(float(amp_zoom1))
	got = abs(a)
	if ref > 0 and abs(got - ref) / ref <= 0.15:
		expected = ref / z
		return expected if a >= 0 else -expected
	return a


def optics_from_geometry(geom: dict[str, Any] | None) -> dict[str, Any]:
	"""Flatten live optics for the GUI caption / API."""
	g = geom or {}
	zoom = float(g.get("optical_zoom") or DEFAULT_OPTICAL_ZOOM)
	fov_w = float(g.get("fov_width_um") or DEFAULT_FOV_WIDTH_UM)
	fov_h = float(g.get("fov_height_um") or DEFAULT_FOV_HEIGHT_UM)
	pix = float(g.get("pixel_size_um") or DEFAULT_PIXEL_SIZE_UM)
	w = int(g.get("frame_w") or DEFAULT_IMAGE_PX)
	h = int(g.get("frame_h") or DEFAULT_IMAGE_PX)
	amp_x = g.get("map_amp_x", g.get("amp_x"))
	amp_y = g.get("map_amp_y", g.get("amp_y"))
	return {
		"optical_zoom": zoom,
		"fov_width_um": fov_w,
		"fov_height_um": fov_h,
		"pixel_size_um": pix,
		"image_width_px": w,
		"image_height_px": h,
		"amp_x": amp_x,
		"amp_y": amp_y,
		"map_amp_x": g.get("map_amp_x", amp_x),
		"map_amp_y": g.get("map_amp_y", amp_y),
	}


def format_optics_line(geom: dict[str, Any] | None, frame_wh: tuple[int, int] | None = None) -> str:
	"""Short Live caption: size · FOV µm · µm/px · zoom · map amp."""
	info = optics_from_geometry(geom)
	if frame_wh:
		info["image_width_px"], info["image_height_px"] = int(frame_wh[0]), int(frame_wh[1])
	amp_x = info.get("map_amp_x")
	amp_y = info.get("map_amp_y")
	amp_bit = ""
	if amp_x is not None and amp_y is not None:
		amp_bit = f" · map ±{abs(float(amp_x)):.3g}/±{abs(float(amp_y)):.3g} V"
	return (
		f"{info['image_width_px']}×{info['image_height_px']} · "
		f"FOV {info['fov_width_um']:.1f} µm · "
		f"{info['pixel_size_um']:.3f} µm/px · "
		f"zoom {info['optical_zoom']:g}"
		f"{amp_bit}"
	)


def fetch_scan_geometry(client) -> dict[str, float]:
	"""Scan amplitude/center (+ optical FOV when PV answers) via the relay."""
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

	# One zoom for caption and galvo→FOV map (never map at silent 1.0 while
	# the caption shows DEFAULT_OPTICAL_ZOOM — that clusters Mark Points).
	zoom_pv = _try_state(client, "opticalZoom")
	zoom = (
		float(zoom_pv)
		if zoom_pv and zoom_pv > 0
		else float(DEFAULT_OPTICAL_ZOOM)
	)
	geom["optical_zoom"] = zoom

	um_x = _try_state(client, "micronsPerPixel", "XAxis")
	if um_x is None:
		um_x = _try_state(client, "micronsPerPixel")
	um_y = _try_state(client, "micronsPerPixel", "YAxis")
	if um_y is None:
		um_y = um_x

	px_w = _try_state(client, "pixelsPerLine")
	px_h = _try_state(client, "linesPerFrame")
	if px_w and px_w > 0:
		geom["frame_w"] = float(px_w)
	if px_h and px_h > 0:
		geom["frame_h"] = float(px_h)

	if um_x and um_x > 0:
		geom["pixel_size_um"] = float(um_x)
		fw = float(px_w) if px_w and px_w > 0 else float(DEFAULT_IMAGE_PX)
		geom["fov_width_um"] = float(um_x) * fw
	else:
		geom["pixel_size_um"] = float(DEFAULT_PIXEL_SIZE_UM)
		geom["fov_width_um"] = float(DEFAULT_FOV_WIDTH_UM)
	if um_y and um_y > 0:
		fh = float(px_h) if px_h and px_h > 0 else float(DEFAULT_IMAGE_PX)
		geom["fov_height_um"] = float(um_y) * fh
	else:
		geom["fov_height_um"] = float(DEFAULT_FOV_HEIGHT_UM)

	geom["map_amp_x"] = effective_scan_amplitude(
		geom["amp_x"], optical_zoom=zoom
	)
	geom["map_amp_y"] = effective_scan_amplitude(
		geom["amp_y"], optical_zoom=zoom
	)
	return geom


def is_fraction_space(points: list[dict]) -> bool:
	return bool(points) and all(
		0.0 <= float(p["x"]) <= 1.0 and 0.0 <= float(p["y"]) <= 1.0 for p in points
	)


def galvo_to_fraction(x: float, y: float, geom: dict[str, float]) -> tuple[float, float]:
	"""Galvo voltage → image fraction; amplitude is the half-width of the scan."""
	ax = 2.0 * float(geom.get("map_amp_x", geom["amp_x"]))
	ay = 2.0 * float(geom.get("map_amp_y", geom["amp_y"]))
	return (
		0.5 + (float(x) - float(geom["ctr_x"])) / ax,
		0.5 + (float(y) - float(geom["ctr_y"])) / ay,
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
		out.append(row)
	return out


def prepare_points_for_slm(
	points: list[dict],
	relay: Any | None,
) -> tuple[list[dict], str]:
	"""FOV-fraction copies for ``-MarkAllPoints`` (PV cannot save FOV coords).

	Galvo-list XML is mapped via live ``currentScanAmplitude`` / ``Center``.
	Raises ValueError when points are galvo-space but geometry is unavailable.
	"""
	if not points:
		return [], "no points"
	if is_fraction_space(points):
		return list(points), "points already in FOV fractions (0–1)"
	if relay is None:
		raise ValueError(
			"galvo Mark Points need the relay (Live + scan range) to convert "
			"to FOV fractions for -MarkAllPoints"
		)
	geom = fetch_scan_geometry(relay)
	mapped = points_for_image(points, geom)
	n_out = sum(
		1
		for p in mapped
		if not (0.0 <= float(p["x"]) <= 1.0 and 0.0 <= float(p["y"]) <= 1.0)
	)
	note = (
		f"mapped {len(mapped)} galvo points → FOV via scan "
		f"X {geom['ctr_x']:g}±{geom.get('map_amp_x', geom['amp_x']):g}, "
		f"Y {geom['ctr_y']:g}±{geom.get('map_amp_y', geom['amp_y']):g}"
		f" (zoom {geom.get('optical_zoom', 1):g})"
	)
	if n_out:
		note += f" · {n_out} still outside 0–1 at this zoom"
	return mapped, note


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
		rows = []
		for p in points:
			size = p.get("spiral_size_fov") or p.get("spiral_width")
			r = abs(float(size)) / 2.0 if size not in (None, "") else None
			rows.append(_row(p, float(p["x"]), float(p["y"]), r))
		note = ""
		if geom:
			note = format_optics_line(geom)
		return {"points": rows, "note": note}
	if not geom:
		why = f" ({error})" if error else ""
		return {
			"points": [],
			"note": "Mark Points are in galvo units; waiting for PV scan range "
			f"from the relay{why}",
		}
	ax = 2.0 * float(geom.get("map_amp_x", geom["amp_x"]))
	rows = []
	for p in points:
		fx, fy = galvo_to_fraction(float(p["x"]), float(p["y"]), geom)
		size = p.get("spiral_size_fov") or p.get("spiral_width")
		r = abs(float(size) / ax) / 2 if size not in (None, "") else None
		rows.append(_row(p, fx, fy, r))
	n_out = sum(1 for r in rows if not (0 <= r["fx"] <= 1 and 0 <= r["fy"] <= 1))
	note = format_optics_line(geom)
	raw_x, raw_y = geom["amp_x"], geom["amp_y"]
	map_x = geom.get("map_amp_x", raw_x)
	map_y = geom.get("map_amp_y", raw_y)
	if abs(float(map_x) - float(raw_x)) > 1e-6 or abs(float(map_y) - float(raw_y)) > 1e-6:
		note += f" · PV amp ±{abs(float(raw_x)):.3g} scaled for zoom"
	if n_out:
		note += f" · {n_out} point(s) outside the current scan"
	return {"points": rows, "note": note}


def _row(p: dict, fx: float, fy: float, r: float | None) -> dict:
	return {
		"id": str(p.get("id")),
		"name": str(p.get("name") or p.get("id")),
		"fx": round(fx, 5),
		"fy": round(fy, 5),
		"r": round(r, 5) if r else None,
	}
