"""Read display fields from a Mark Points XML (FOV is derived, not stored)."""

from __future__ import annotations

from typing import Any

from prairie_live.markpoints import (
	extract_unique_points,
	load_mark_points_file,
	template_meta,
)
from prairie_live.random_groups import catalog_groups_from_steps, classify_series


def _as_ms(raw: Any) -> float | None:
	"""PV mixes ms and seconds. Values <= 1 are treated as seconds."""
	if raw in (None, ""):
		return None
	try:
		val = float(raw)
	except (TypeError, ValueError):
		return None
	if abs(val) <= 1.0:
		return val * 1000.0
	return val


def summarize_fat_xml(path: str) -> dict[str, Any]:
	"""Points/groups + timing/spiral from the series; optical FOV estimated if possible."""
	steps = load_mark_points_file(path)
	kind = classify_series(steps)
	points: list[dict] = []
	n_catalog = 0
	slim = False
	if kind == "group_catalog":
		catalog = catalog_groups_from_steps(steps)
		n_catalog = len(catalog)
		slim = all(bool(g.get("slim")) for g in catalog)
		n_points = len({pid for g in catalog for pid in g["point_ids"]})
		try:
			points = extract_unique_points(steps)
		except ValueError:
			points = []
	else:
		points = extract_unique_points(steps)
		n_points = len(points)

	rich = next(
		(
			s
			for s in steps
			if s.get("points")
			and len(s.get("points", [])) >= 1
			and float((s["points"][0] or {}).get("x", 0)) != 0
		),
		None,
	)
	meta = template_meta([rich] if rich else steps)
	pt0 = points[0] if points else {}
	spiral_um = pt0.get("spiral_size_um")
	try:
		spiral_um_f = float(spiral_um) if spiral_um not in (None, "") else None
	except (TypeError, ValueError):
		spiral_um_f = None
	try:
		sw = float(pt0.get("spiral_width") or 0) or None
	except (TypeError, ValueError):
		sw = None
	try:
		sh = float(pt0.get("spiral_height") or 0) or None
	except (TypeError, ValueError):
		sh = None
	fov_w = fov_h = pixel = None
	if spiral_um_f and sw:
		fov_w = spiral_um_f / sw
	if spiral_um_f and sh:
		fov_h = spiral_um_f / sh
	if fov_w:
		pixel = fov_w / 512.0
	if kind == "group_catalog" and slim:
		fov_note = (
			f"Group catalog ({n_catalog} groups, Indices only) — "
			"Generate builds a random sequence; keep your .gpl loaded in PV"
		)
	elif kind == "group_catalog":
		fov_note = f"Group catalog ({n_catalog} groups) — Generate builds a random sequence"
	elif fov_w:
		fov_note = (
			"FOV µm estimated from SpiralSizeInMicrons / SpiralWidth "
			"(not a Prairie XML field)"
		)
	else:
		fov_note = "FOV µm / pixel size are not in the Mark Points XML"
	return {
		"path": path,
		"n_points": n_points,
		"n_groups": n_catalog or None,
		"series_kind": kind,
		"slim": slim,
		"duration_ms": _as_ms(meta.get("duration")),
		"inter_point_delay_ms": _as_ms(meta.get("inter_point_delay")),
		"initial_delay_ms": _as_ms(meta.get("initial_delay")),
		"spiral_revolutions": (
			float(meta["spiral_revolutions"])
			if meta.get("spiral_revolutions") not in (None, "")
			else None
		),
		"spiral": str(pt0.get("is_spiral", "True")).lower() in ("true", "1", "yes"),
		"spiral_size_um": spiral_um_f,
		"fov_width_um": fov_w,
		"fov_height_um": fov_h,
		"pixel_size_um": pixel,
		"laser": meta.get("uncaging_laser"),
		"repetitions": int(meta.get("repetitions") or 1),
		"source": "xml",
		"fov_note": fov_note,
	}
