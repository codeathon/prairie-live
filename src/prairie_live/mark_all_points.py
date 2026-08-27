"""Build PrairieView -MarkAllPoints (-slm) script argument lists.

Official help: simultaneous SLM multi-spot (or mask). Not an \"enable SLM\" flag.
Multiple hologram sets may be packed into one command; each set can wait on
PFI1, with Delay ms between sets (omit Delay on the last).
"""

from __future__ import annotations

from typing import Any


def spiral_size_fov_frac(size_um: float, fov_width_um: float) -> float:
	"""Convert spiral size µm → FOV fraction 0–1 expected by -slm."""
	fov = float(fov_width_um)
	if fov <= 0:
		raise ValueError(f"fov_width_um must be > 0, got {fov_width_um!r}")
	return float(size_um) / fov


def build_set_tokens(
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
	One hologram block (no leading -MarkAllPoints, no trailing Delay).

	Shape (2D + spiral + trigger):
	  N False x0 y0 … xN-1 yN-1 duration_ms laser power True spiral_frac revs Trigger
	"""
	if not points:
		raise ValueError("MarkAllPoints set needs at least one point")
	laser = str(laser).strip()
	if not laser:
		raise ValueError("laser name is empty")

	parts: list[str] = [
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

	parts.append(str(trigger_selection or "None"))
	return parts


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
	"""Single-set -MarkAllPoints token list (compat wrapper around pack)."""
	return pack_mark_all_points(
		[points],
		power=power,
		laser=laser,
		duration_ms=duration_ms,
		use_3d=use_3d,
		spiral=spiral,
		spiral_size_um=spiral_size_um,
		spiral_revolutions=spiral_revolutions,
		fov_width_um=fov_width_um,
		trigger_selection=trigger_selection,
		delay_ms=0.0,
	)


def pack_mark_all_points(
	groups: list[list[dict]],
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
	delay_ms: float = 0.0,
) -> list[str]:
	"""
	Pack many simultaneous sets into one -slm command.

	PV grammar: after each set's Trigger, append Delay ms except on the last
	set. Software maps trigger pulse i → groups[i] (same order as packed).
	"""
	if not groups:
		raise ValueError("pack_mark_all_points needs at least one group")
	parts: list[str] = ["-MarkAllPoints"]
	last = len(groups) - 1
	for i, gpts in enumerate(groups):
		parts.extend(
			build_set_tokens(
				gpts,
				power=power,
				laser=laser,
				duration_ms=duration_ms,
				use_3d=use_3d,
				spiral=spiral,
				spiral_size_um=spiral_size_um,
				spiral_revolutions=spiral_revolutions,
				fov_width_um=fov_width_um,
				trigger_selection=trigger_selection,
			)
		)
		# Omit Delay on the last repetition (PV requirement).
		if i < last:
			parts.append(str(int(round(float(delay_ms)))))
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
