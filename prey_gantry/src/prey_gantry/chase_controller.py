"""50 Hz chase loop. Why: same period as pylon-track ChaseController (20 ms)."""

from __future__ import annotations

import math
import time

from prey_gantry.chase_policy import ChaseDecision, compute_chase_decision
from prey_gantry.config import ChasePolicyConfig
from prey_gantry.hunt_event import HuntArmState, evaluate_hunt_event, note_hunt_flee_complete
from prey_gantry.tracking_frame import TrackingFrame
from prey_gantry.zaber_sim import SimulatedGantry


class ChaseController:
	def __init__(
		self,
		gantry: SimulatedGantry,
		cfg: ChasePolicyConfig,
		width_mm: float,
		height_mm: float,
		period_ms: int,
		stale_ms: float,
	) -> None:
		self._gantry = gantry
		self._cfg = cfg
		self._w = width_mm
		self._h = height_mm
		self._period_s = period_ms * 1e-3
		self._stale_s = stale_ms * 1e-3
		self._next_s = 0.0
		self._latest: TrackingFrame | None = None
		self._arm = HuntArmState()
		self._fleeing = False
		self.last_decision = ChaseDecision()
		self.last_decision_ms = 0.0
		self.stale_stops = 0

	def submit_frame(self, frame: TrackingFrame) -> None:
		self._latest = frame

	def poll(self, t_s: float) -> None:
		if t_s < self._next_s:
			return
		self._next_s = t_s + self._period_s
		frame = self._latest
		if frame is None:
			return
		if t_s - frame.host_time_ns * 1e-9 > self._stale_s:
			self._gantry.stop()
			self.stale_stops += 1
			self.last_decision = ChaseDecision(
				reason="stale_frame", decision_time_ns=frame.host_time_ns
			)
			return
		# Why: pylon-track does not preempt an active flee (MotionPlanner busy).
		if self._fleeing and self._gantry.is_busy():
			return
		if self._fleeing and not self._gantry.is_busy():
			note_hunt_flee_complete(self._arm, frame.host_time_ns)
			self._fleeing = False
		self._tick_decision(frame)

	def _tick_decision(self, frame: TrackingFrame) -> None:
		t0 = time.perf_counter()
		decision = compute_chase_decision(frame, self._cfg, self._w, self._h)
		self.last_decision_ms = (time.perf_counter() - t0) * 1e3
		self.last_decision = decision
		if not decision.enable_motion:
			if self._gantry.is_busy() or math.hypot(*self._gantry.get_velocity()) > 1.0:
				self._gantry.stop()
			return
		if evaluate_hunt_event(
			decision.use_planned_flee,
			frame.host_time_ns,
			self._cfg.hunt_event_min_interval_ms,
			self._arm,
		) and decision.use_planned_flee:
			self._gantry.move_absolute(
				decision.flee_x_mm,
				decision.flee_y_mm,
				wait_until_idle=False,
				velocity=decision.flee_speed_mm_s,
				acceleration=self._cfg.flee_accel_mps2 * 1000.0,
			)
			self._fleeing = True
			return
		if self._gantry.is_busy():
			return
		self._gantry.move_velocity(decision.target_vx_mm_s, decision.target_vy_mm_s)
