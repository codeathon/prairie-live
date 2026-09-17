"""2D cone-of-impact chase. Why: same threat math as pylon-track, XY flee vector."""

from __future__ import annotations

import math
from dataclasses import dataclass

from prey_gantry.config import ChasePolicyConfig
from prey_gantry.tracking_frame import TrackingFrame, TrialPhase


def _clamp01(v: float) -> float:
	return max(0.0, min(1.0, v))


def _angle_diff_deg(a: float, b: float) -> float:
	d = abs(a - b) % 360.0
	if d > 180.0:
		d = 360.0 - d
	return d


def _clampf(v: float, lo: float, hi: float) -> float:
	return max(lo, min(hi, v))


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


def _away_unit(frame: TrackingFrame) -> tuple[float, float]:
	dx = frame.prey.x_mm - frame.ferret.x_mm
	dy = frame.prey.y_mm - frame.ferret.y_mm
	n = math.hypot(dx, dy)
	if n < 1e-3:
		rad = math.radians(frame.ferret.direction_deg + 180.0)
		return math.cos(rad), -math.sin(rad)
	return dx / n, dy / n


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
	dist = scene.distance_mm
	out.dist_threat, out.cone_threat, out.approach_threat = _threat_parts(scene, cfg)
	out.threat = _clamp01(out.dist_threat * out.cone_threat * out.approach_threat)

	speed_span = cfg.max_chain_speed_mps - cfg.min_chain_speed_mps
	speed_mps = cfg.min_chain_speed_mps + out.threat * speed_span
	ux, uy = _away_unit(scene)
	out.flee_direction_deg = math.degrees(math.atan2(-uy, ux))
	out.target_vx_mm_s = ux * speed_mps * 1000.0
	out.target_vy_mm_s = uy * speed_mps * 1000.0

	if out.threat > cfg.flee_threat_threshold:
		return _attach_flee(out, scene, cfg, ux, uy, speed_mps, width_mm, height_mm)
	out.reason = "chase" if out.threat > 0.05 else "creep"
	return out


def _threat_parts(
	scene: TrackingFrame, cfg: ChasePolicyConfig
) -> tuple[float, float, float]:
	dist = scene.distance_mm
	dist_threat = 1.0
	if dist > cfg.threat_distance_mm:
		span = max(1.0, cfg.creep_distance_mm - cfg.threat_distance_mm)
		dist_threat = 1.0 - _clamp01((dist - cfg.threat_distance_mm) / span)
	heading_delta = _angle_diff_deg(scene.ferret.direction_deg, scene.bearing_deg)
	cone_threat = 1.0
	if heading_delta > cfg.cone_half_angle_deg:
		cone_threat = 1.0 - _clamp01(
			(heading_delta - cfg.cone_half_angle_deg) / 90.0
		)
	approach = 1.0 if scene.closing_speed_mm_s > 0.0 else 0.2
	return dist_threat, cone_threat, approach


def _attach_flee(
	out: ChaseDecision,
	scene: TrackingFrame,
	cfg: ChasePolicyConfig,
	ux: float,
	uy: float,
	speed_mps: float,
	width_mm: float,
	height_mm: float,
) -> ChaseDecision:
	closing = max(0.0, scene.closing_speed_mm_s)
	flee_mag = cfg.flee_gap_gain * max(0.0, scene.distance_mm) + cfg.flee_speed_gain * closing
	flee_mag = _clampf(flee_mag, cfg.min_flee_mm, cfg.max_flee_mm)
	tx = scene.prey.x_mm + ux * flee_mag
	ty = scene.prey.y_mm + uy * flee_mag
	margin = 40.0
	out.flee_x_mm = _clampf(tx, margin, width_mm - margin)
	out.flee_y_mm = _clampf(ty, margin, height_mm - margin)
	out.flee_mm = math.hypot(out.flee_x_mm - scene.prey.x_mm, out.flee_y_mm - scene.prey.y_mm)
	out.flee_speed_mm_s = speed_mps * 1000.0
	if out.flee_mm < 20.0:
		out.reason = "flee_infeasible_creep"
		return out
	out.use_planned_flee = True
	out.reason = "flee_plan"
	return out
