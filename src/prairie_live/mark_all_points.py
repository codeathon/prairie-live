"""Build PrairieView -MarkAllPoints (-slm) script argument lists.

Official help: simultaneous SLM multi-spot (or mask). Not an \"enable SLM\" flag.
"""

from __future__ import annotations

from typing import Any


def spiral_size_fov_frac(size_um: float, fov_width_um: float) -> float:
	"""Convert spiral size µm → FOV fraction 0–1 expected by -slm."""
	fov = float(fov_width_um)
	if fov <= 0:
		raise ValueError(f"fov_width_um must be > 0, got {fov_width_um!r}")
	return float(size_um) / fov


def build_mark_all_points_parts(
	points: list[dict],
	*,
	power: float,
	laser: str = "Monaco",
	duration_ms: float = 16.92,
	use_3d: bool = False,
	spiral: bool = True,
	spiral_size_um: float | None = 54.5,
	spiral_revolutions: float | int = 8,
	fov_width_um: float | None = None,
	trigger_selection: str = "PFI1",
) -> list[str]:
	"""
	Token list for -MarkAllPoints / -slm (SOH-joined on TCP, space-joined on COM).

	Shape (2D + spiral + trigger):
	  -MarkAllPoints N False x0 y0 … xN-1 yN-1
	  duration_ms laser power True spiral_frac revolutions Trigger
	"""
	if not points:
		raise ValueError("MarkAllPoints needs at least one point")
	laser = str(laser).strip()
	if not laser:
		raise ValueError("laser name is empty")

	parts: list[str] = [
		"-MarkAllPoints",
		str(len(points)),
		"True" if use_3d else "False",
	]
	for pt in points:
		parts.append(_fmt_num(pt["x"]))
		parts.append(_fmt_num(pt["y"]))
		if use_3d:
			parts.append(_fmt_num(pt.get("z", 0)))

	parts.append(_fmt_num(duration_ms))
	parts.append(laser)
	parts.append(_fmt_num(power))

	if spiral:
		if spiral_size_um is None:
			raise ValueError("spiral=True requires spiral_size_um")
		if fov_width_um is None:
			raise ValueError(
				"spiral=True requires fov_width_um in experiment.json "
				"(to convert spiral_size_um → FOV fraction for -slm)"
			)
		frac = spiral_size_fov_frac(float(spiral_size_um), float(fov_width_um))
		parts.extend(["True", _fmt_num(frac), str(int(spiral_revolutions))])

	trig = str(trigger_selection or "None")
	parts.append(trig)
	return parts


def parts_to_com_command(parts: list[str]) -> str:
	"""COM SendScriptCommands uses spaces (quote any token that needs it)."""
	out = []
	for p in parts:
		if any(c.isspace() for c in p):
			out.append(f'"{p}"')
		else:
			out.append(p)
	return " ".join(out)


def stim_params_from_meta(meta: dict[str, Any]) -> dict[str, Any]:
	"""Pull -slm fields from Mark Points meta / experiment overlay."""
	use_3d = str(meta.get("use_3d", "False")).lower() in ("true", "1", "yes")
	spiral = str(meta.get("is_spiral", "True")).lower() in ("true", "1", "yes")
	fov = meta.get("fov_width_um")
	size = meta.get("spiral_size_um")
	return {
		"laser": str(meta.get("uncaging_laser", "Monaco")),
		"duration_ms": float(meta.get("duration", 16.92)),
		"use_3d": use_3d,
		"spiral": spiral,
		"spiral_size_um": float(size) if size not in (None, "") else None,
		"spiral_revolutions": int(float(meta.get("spiral_revolutions", 8))),
		"fov_width_um": float(fov) if fov not in (None, "") else None,
		"trigger_selection": str(meta.get("trigger_selection", "PFI1")),
		"initial_delay_ms": float(meta.get("initial_delay_ms", 0) or 0),
	}


def _fmt_num(v: Any) -> str:
	x = float(v)
	# Compact but stable for script parsing.
	s = f"{x:.6g}"
	return s
