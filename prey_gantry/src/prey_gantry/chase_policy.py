"""Soft keep-away hunt: nudge slightly off the ferret, stay engaged, dodge walls.

Why: discrete 200–600 mm flees ended the chase. This policy holds a preferred
gap, only inches away when pressed, and steers toward arena center near edges.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from prey_gantry.config import ChasePolicyConfig
from prey_gantry.tracking_frame import TrackingFrame, TrialPhase


def _clamp01(v: float) -> float:
	return max(0.0, min(1.0, v))


def _clampf(v: float, lo: float, hi: float) -> float:
	return max(lo, min(hi, v))


def _norm(x: float, y: float) -> tuple[float, float, float]:
	n = math.hypot(x, y)
	if n < 1e-6:
		return 0.0, 0.0, 0.0
	return x / n, y / n, n


@dataclass
class ChaseDecision:
	decision_time_ns: int = 0
	target_vx_mm_s: float = 0.0
	target_vy_mm_s: float = 0.0
	flee_direction_deg: float = 0.0
	threat: float = 0.0
	dist_threat: float = 0.0
	cone_threat: float = 0.0
	approach_threat: float = 0.0
	enable_motion: bool = False
	use_planned_flee: bool = False
	flee_x_mm: float = 0.0
	flee_y_mm: float = 0.0
	flee_mm: float = 0.0
	flee_speed_mm_s: float = 0.0
	gap_error_mm: float = 0.0
	wall_push: float = 0.0
	reason: str = "idle"


def fill_tracking_derived(frame: TrackingFrame) -> None:
	"""pylon-track fill_tracking_derived: bearing 0=right, 90=up (Y flipped)."""
	frame.distance_mm = -1.0
	frame.bearing_deg = 0.0
	frame.closing_speed_mm_s = 0.0
	if not frame.both_valid():
		return
	dx = frame.prey.x_mm - frame.ferret.x_mm
	dy = frame.prey.y_mm - frame.ferret.y_mm
	dist = math.hypot(dx, dy)
	frame.distance_mm = dist
	frame.bearing_deg = math.degrees(math.atan2(-dy, dx))
	heading = math.radians(frame.ferret.direction_deg)
	vx = math.cos(heading) * frame.ferret.speed_mm_s
	vy = -math.sin(heading) * frame.ferret.speed_mm_s
	if dist > 1e-3:
		frame.closing_speed_mm_s = (vx * dx + vy * dy) / dist


def compute_chase_decision(
	scene: TrackingFrame,
	cfg: ChasePolicyConfig,
	width_mm: float,
	height_mm: float,
) -> ChaseDecision:
	out = ChaseDecision(decision_time_ns=scene.host_time_ns)
	if scene.trial_phase != TrialPhase.running:
		out.reason = "trial_not_running"
		return out
	if not scene.both_valid():
		out.reason = "tracks_invalid"
		return out
	if scene.quality.ferret_confidence < 0.3 or scene.quality.prey_confidence < 0.3:
		out.reason = "low_track_confidence"
		return out

	out.enable_motion = True
	ax, ay, reason = _engage_accel(scene, cfg, width_mm, height_mm, out)
	out.reason = reason
	# Why: convert soft accel (mm/s² scale) to a capped velocity command for Zaber.
	vx = ax * cfg.velocity_gain_s
	vy = ay * cfg.velocity_gain_s
	spd = math.hypot(vx, vy)
	cap = cfg.max_engage_speed_mm_s
	if spd > cap and spd > 1e-6:
		s = cap / spd
		vx *= s
		vy *= s
	out.target_vx_mm_s = vx
	out.target_vy_mm_s = vy
	out.flee_direction_deg = math.degrees(math.atan2(-vy, vx)) if spd > 1 else 0.0
	out.threat = _clamp01(out.dist_threat)
	return out


def _engage_accel(
	scene: TrackingFrame,
	cfg: ChasePolicyConfig,
	width_mm: float,
	height_mm: float,
	out: ChaseDecision,
) -> tuple[float, float, str]:
	px, py = scene.prey.x_mm, scene.prey.y_mm
	fx, fy = scene.ferret.x_mm, scene.ferret.y_mm
	ux, uy, dist = _norm(px - fx, py - fy)
	if dist < 1e-3:
		# Overlap: break out toward arena center so we do not freeze.
		ux, uy, _ = _norm(width_mm * 0.5 - px, height_mm * 0.5 - py)
		dist = 0.0

	preferred = cfg.preferred_gap_mm
	gap_err = preferred - dist
	out.gap_error_mm = gap_err
	# Close → push away; far → ease back toward ferret (keeps the hunt alive).
	if gap_err > 0:
		out.dist_threat = _clamp01(gap_err / max(preferred - cfg.min_gap_mm, 1.0))
		radial = ux * gap_err * cfg.away_gain
		radial_y = uy * gap_err * cfg.away_gain
		tag = "nudge_away"
	else:
		out.dist_threat = 0.0
		pull = min(-gap_err, cfg.max_pull_mm)
		radial = -ux * pull * cfg.toward_gain
		radial_y = -uy * pull * cfg.toward_gain
		tag = "reel_in"

	# Lateral slip when pressed: avoid head-on stall, stay playful.
	tx, ty = -uy, ux
	closing = max(0.0, scene.closing_speed_mm_s)
	lateral = closing * cfg.lateral_gain
	# Bias slip toward center so we do not choose the wall side of a tangent.
	cx, cy = width_mm * 0.5 - px, height_mm * 0.5 - py
	if tx * cx + ty * cy < 0:
		tx, ty = -tx, -ty

	wx, wy, wall = _wall_push(px, py, width_mm, height_mm, cfg)
	out.wall_push = wall
	out.approach_threat = _clamp01(closing / 800.0)
	out.cone_threat = wall

	ax = radial + tx * lateral + wx
	ay = radial_y + ty * lateral + wy
	if wall > 0.35:
		tag = "edge_dodge"
	elif dist < cfg.min_gap_mm:
		tag = "press"
	return ax, ay, tag


def _wall_push(
	x: float,
	y: float,
	width_mm: float,
	height_mm: float,
	cfg: ChasePolicyConfig,
) -> tuple[float, float, float]:
	"""Repel from edges/corners toward open space. Strength grows inside margin."""
	m = cfg.wall_margin_mm
	left = max(0.0, m - x) / m
	right = max(0.0, m - (width_mm - x)) / m
	top = max(0.0, m - y) / m
	bottom = max(0.0, m - (height_mm - y)) / m
	# Squared so corners (two walls) kick harder than a single edge.
	# Near left → +x; near top → +y (origin is top-left in arena mm).
	px = (left * left - right * right) * cfg.wall_gain
	py = (top * top - bottom * bottom) * cfg.wall_gain
	# Extra center pull when deep in a corner.
	corner = max(left, right) * max(top, bottom)
	if corner > 0:
		cx, cy, _ = _norm(width_mm * 0.5 - x, height_mm * 0.5 - y)
		px += cx * corner * cfg.corner_gain
		py += cy * corner * cfg.corner_gain
	strength = max(left, right, top, bottom)
	return px, py, strength
