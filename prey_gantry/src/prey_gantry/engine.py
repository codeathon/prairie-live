"""Real-time hunt world: mouse ferret, delayed pylon grab, Zaber XY prey."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from prey_gantry.chase_controller import ChaseController
from prey_gantry.chase_policy import fill_tracking_derived
from prey_gantry.config import SimConfig, load_sim_config
from prey_gantry.pylon_sim import SimulatedPylonCamera
from prey_gantry.tracking_frame import TrackState, TrialPhase
from prey_gantry.zaber_sim import SimulatedGantry


@dataclass
class Pointer:
	x_mm: float
	y_mm: float


class HuntSim:
	def __init__(self, cfg: SimConfig | None = None) -> None:
		self.cfg = cfg or load_sim_config()
		cam = self.cfg.camera
		zb = self.cfg.zaber
		self.t_s = 0.0
		self.trial = TrialPhase.warmup
		self.true_ferret = TrackState(cam.width_mm * 0.25, cam.height_mm * 0.5, 0, 0, True)
		self._prev_fx = self.true_ferret.x_mm
		self._prev_fy = self.true_ferret.y_mm
		self.gantry = SimulatedGantry(
			zb.home_x_mm,
			zb.home_y_mm,
			zb.max_speed_mm_s,
			zb.max_accel_mm_s2,
			zb.command_rtt_ms,
			cam.width_mm,
			cam.height_mm,
		)
		self.camera = SimulatedPylonCamera(cam)
		self.camera.start_grabbing()
		self.controller = ChaseController(
			self.gantry,
			self.cfg.chase,
			cam.width_mm,
			cam.height_mm,
			self.cfg.control_period_ms,
			self.cfg.stale_frame_ms,
		)
		self.last_frame_index = -1

	def set_pointer(self, x_mm: float, y_mm: float) -> None:
		cam = self.cfg.camera
		self.true_ferret.x_mm = min(max(x_mm, 0.0), cam.width_mm)
		self.true_ferret.y_mm = min(max(y_mm, 0.0), cam.height_mm)

	def set_trial(self, cmd: str) -> None:
		if cmd == "start":
			self.trial = TrialPhase.running
		elif cmd == "end":
			self.trial = TrialPhase.ended
			self.gantry.stop()
		elif cmd == "reset":
			self.trial = TrialPhase.warmup
			self.gantry.stop()
			self.gantry.x_mm = self.cfg.zaber.home_x_mm
			self.gantry.y_mm = self.cfg.zaber.home_y_mm
			self.gantry.vx_mm_s = self.gantry.vy_mm_s = 0.0

	def step(self, dt_s: float) -> None:
		self._update_ferret_kinematics(dt_s)
		self.t_s += dt_s
		self.gantry.step(dt_s, self.t_s)
		prey = self._prey_track()
		frame = self.camera.poll(self.t_s, self.true_ferret, prey, self.trial)
		if frame is not None:
			# Prey in the chase loop uses encoder (gantry), ferret is camera-delayed.
			frame.prey = prey
			frame.prey.valid = True
			fill_tracking_derived(frame)
			self.controller.submit_frame(frame)
			self.last_frame_index = frame.frame_index
		self.controller.poll(self.t_s)

	def snapshot(self) -> dict:
		d = self.controller.last_decision
		px, py = self.gantry.get_position()
		vx, vy = self.gantry.get_velocity()
		cam = self.cfg.camera
		frame = self.controller._latest
		seen = frame.ferret if frame else TrackState()
		return {
			"t_s": self.t_s,
			"trial": self.trial.value,
			"arena": {
				"width_mm": cam.width_mm,
				"height_mm": cam.height_mm,
				"width_px": cam.width_px,
				"height_px": cam.height_px,
				"gsd_mm_per_px": cam.gsd_mm_per_px,
			},
			"ferret_true": _track_dict(self.true_ferret),
			"ferret_camera": _track_dict(seen),
			"prey": _track_dict(self._prey_track()),
			"camera": {
				"model": cam.model,
				"fps": cam.frame_rate_fps,
				"exposure_us": cam.exposure_us,
				"usb_transfer_ms": cam.usb_transfer_ms,
				"tracking_pipeline_ms": cam.tracking_pipeline_ms,
				"grab_to_host_ms": cam.grab_to_host_s * 1e3,
				"grab_to_track_ms": cam.grab_to_track_s * 1e3,
				"last_grab_to_frame_ms": self.camera.last_grab_to_frame_ms,
				"delivered": self.camera.delivered,
				"dropped": self.camera.dropped,
				"strategy": self.camera.GrabStrategy,
				"pixel_format": self.camera.PixelFormat,
			},
			"zaber": {
				"comm": self.cfg.zaber.comm,
				"rtt_ms": self.gantry.last_rtt_ms,
				"busy": self.gantry.is_busy(),
				"x_mm": px,
				"y_mm": py,
				"vx_mm_s": vx,
				"vy_mm_s": vy,
				"speed_mm_s": math.hypot(vx, vy),
				"heading_deg": math.degrees(math.atan2(-vy, vx)) if math.hypot(vx, vy) > 1 else 0.0,
				"max_speed_mm_s": self.cfg.zaber.max_speed_mm_s,
				"max_accel_mm_s2": self.cfg.zaber.max_accel_mm_s2,
				"api_calls": [asdict(c) for c in list(self.gantry.calls)[:8]],
			},
			"decision": {
				"reason": d.reason,
				"threat": d.threat,
				"dist_threat": d.dist_threat,
				"wall_push": d.wall_push,
				"approach_threat": d.approach_threat,
				"gap_error_mm": d.gap_error_mm,
				"enable_motion": d.enable_motion,
				"use_planned_flee": False,
				"vx_mm_s": d.target_vx_mm_s,
				"vy_mm_s": d.target_vy_mm_s,
				"flee_direction_deg": d.flee_direction_deg,
				"compute_ms": self.controller.last_decision_ms,
				"stale_stops": self.controller.stale_stops,
			},
			"scene": {
				"distance_mm": frame.distance_mm if frame else -1,
				"bearing_deg": frame.bearing_deg if frame else 0,
				"closing_speed_mm_s": frame.closing_speed_mm_s if frame else 0,
				"frame_index": self.last_frame_index,
			},
			"control_hz": 1000.0 / self.cfg.control_period_ms,
			"policy": asdict(self.cfg.chase),
		}

	def _prey_track(self) -> TrackState:
		vx, vy = self.gantry.get_velocity()
		x, y = self.gantry.get_position()
		spd = math.hypot(vx, vy)
		heading = math.degrees(math.atan2(-vy, vx)) if spd > 1 else 0.0
		return TrackState(x, y, spd, heading, True)

	def _update_ferret_kinematics(self, dt_s: float) -> None:
		dx = self.true_ferret.x_mm - self._prev_fx
		dy = self.true_ferret.y_mm - self._prev_fy
		if dt_s > 1e-6:
			self.true_ferret.speed_mm_s = math.hypot(dx, dy) / dt_s
			if self.true_ferret.speed_mm_s > 5.0:
				self.true_ferret.direction_deg = math.degrees(math.atan2(-dy, dx))
		self._prev_fx = self.true_ferret.x_mm
		self._prev_fy = self.true_ferret.y_mm


def _track_dict(t: TrackState) -> dict:
	return {
		"x_mm": t.x_mm,
		"y_mm": t.y_mm,
		"speed_mm_s": t.speed_mm_s,
		"direction_deg": t.direction_deg,
		"valid": t.valid,
	}
