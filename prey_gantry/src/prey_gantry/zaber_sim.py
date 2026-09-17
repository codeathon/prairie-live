"""Zaber Motion Library–shaped XY API with USB-CDC RTT and trapezoid motion.

Why: the real zaber_motion Axis.move_absolute / move_velocity signatures are
what the chase loop will call on hardware; this sim executes them with
pylon-track-scale speeds and X-MCC-like command delay.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field


class Units:
	"""Subset of zaber_motion.Units used by the hunt loop."""

	LENGTH_MILLIMETRES = "Length:millimetres"
	VELOCITY_MILLIMETRES_PER_SECOND = "Velocity:millimetres per second"
	ACCELERATION_MILLIMETRES_PER_SECOND_SQUARED = (
		"Acceleration:millimetres per second squared"
	)


@dataclass
class ApiCall:
	t_s: float
	name: str
	detail: str
	rtt_ms: float


@dataclass
class _Pending:
	ready_s: float
	kind: str
	x: float = 0.0
	y: float = 0.0
	vx: float = 0.0
	vy: float = 0.0
	speed: float = 0.0
	accel: float = 0.0


class SimulatedGantry:
	"""Two-axis gantry: lockstep+cross collapsed to Cartesian mm."""

	def __init__(
		self,
		x_mm: float,
		y_mm: float,
		max_speed_mm_s: float,
		max_accel_mm_s2: float,
		rtt_ms: float,
		width_mm: float,
		height_mm: float,
	) -> None:
		self.x_mm = x_mm
		self.y_mm = y_mm
		self.vx_mm_s = 0.0
		self.vy_mm_s = 0.0
		self.max_speed = max_speed_mm_s
		self.max_accel = max_accel_mm_s2
		self.rtt_s = rtt_ms * 1e-3
		self.width_mm = width_mm
		self.height_mm = height_mm
		self._mode = "idle"
		self._tgt_x = x_mm
		self._tgt_y = y_mm
		self._cmd_vx = 0.0
		self._cmd_vy = 0.0
		self._move_speed = max_speed_mm_s
		self._move_accel = max_accel_mm_s2
		self._pending: deque[_Pending] = deque()
		self.calls: deque[ApiCall] = deque(maxlen=12)
		self.last_rtt_ms = rtt_ms
		self.t_s = 0.0

	def step(self, dt_s: float, t_s: float) -> None:
		self.t_s = t_s
		self._apply_due(t_s)
		if self._mode == "position":
			self._step_position(dt_s)
		elif self._mode == "velocity":
			self._step_velocity(dt_s)
		else:
			self._step_velocity(dt_s)
		self._clamp()

	def is_busy(self) -> bool:
		if self._pending:
			return True
		speed = math.hypot(self.vx_mm_s, self.vy_mm_s)
		if self._mode == "position":
			remain = math.hypot(self._tgt_x - self.x_mm, self._tgt_y - self.y_mm)
			return remain > 0.5 or speed > 1.0
		return speed > 1.0

	def get_position(self) -> tuple[float, float]:
		return self.x_mm, self.y_mm

	def get_velocity(self) -> tuple[float, float]:
		return self.vx_mm_s, self.vy_mm_s

	def move_absolute(
		self,
		x_mm: float,
		y_mm: float,
		*,
		wait_until_idle: bool = True,
		velocity: float = 0.0,
		acceleration: float = 0.0,
	) -> None:
		# Why: wait_until_idle=False is the live hunt path (NI preempts prior move).
		speed = velocity if velocity > 0 else self.max_speed
		accel = acceleration if acceleration > 0 else self.max_accel
		self._log("move_absolute", f"p=({x_mm:.1f},{y_mm:.1f}) v={speed:.0f} wait={wait_until_idle}")
		self._pending.append(
			_Pending(self.t_s + self.rtt_s, "abs", x=x_mm, y=y_mm, speed=speed, accel=accel)
		)
		if wait_until_idle:
			self._drain_blocking()

	def move_velocity(self, vx_mm_s: float, vy_mm_s: float) -> None:
		self._log("move_velocity", f"v=({vx_mm_s:.0f},{vy_mm_s:.0f}) mm/s")
		self._pending.append(_Pending(self.t_s + self.rtt_s, "vel", vx=vx_mm_s, vy=vy_mm_s))

	def stop(self) -> None:
		self._log("stop", "decelerate to 0")
		self._pending.append(_Pending(self.t_s + self.rtt_s, "stop"))

	def _log(self, name: str, detail: str) -> None:
		self.calls.appendleft(ApiCall(self.t_s, name, detail, self.rtt_s * 1e3))
		self.last_rtt_ms = self.rtt_s * 1e3

	def _apply_due(self, t_s: float) -> None:
		while self._pending and self._pending[0].ready_s <= t_s:
			cmd = self._pending.popleft()
			if cmd.kind == "abs":
				self._mode = "position"
				self._tgt_x, self._tgt_y = cmd.x, cmd.y
				self._move_speed = min(cmd.speed, self.max_speed)
				self._move_accel = min(cmd.accel, self.max_accel)
			elif cmd.kind == "vel":
				self._mode = "velocity"
				self._cmd_vx, self._cmd_vy = cmd.vx, cmd.vy
			else:
				self._mode = "velocity"
				self._cmd_vx = 0.0
				self._cmd_vy = 0.0

	def _step_position(self, dt_s: float) -> None:
		dx = self._tgt_x - self.x_mm
		dy = self._tgt_y - self.y_mm
		dist = math.hypot(dx, dy)
		if dist < 0.5:
			self.x_mm, self.y_mm = self._tgt_x, self._tgt_y
			self.vx_mm_s = self.vy_mm_s = 0.0
			self._mode = "idle"
			return
		ux, uy = dx / dist, dy / dist
		speed_now = math.hypot(self.vx_mm_s, self.vy_mm_s)
		# Why: stop distance v^2/2a so we do not overshoot the flee point.
		stop_d = (speed_now * speed_now) / (2.0 * max(self._move_accel, 1.0))
		if dist <= stop_d:
			target_speed = 0.0
		else:
			target_speed = self._move_speed
		speed_now = _approach_speed(speed_now, target_speed, self._move_accel, dt_s)
		self.vx_mm_s = ux * speed_now
		self.vy_mm_s = uy * speed_now
		self.x_mm += self.vx_mm_s * dt_s
		self.y_mm += self.vy_mm_s * dt_s
		if dist < speed_now * dt_s:
			self.x_mm, self.y_mm = self._tgt_x, self._tgt_y
			self.vx_mm_s = self.vy_mm_s = 0.0
			self._mode = "idle"

	def _step_velocity(self, dt_s: float) -> None:
		self.vx_mm_s = _approach_speed(self.vx_mm_s, self._cmd_vx, self.max_accel, dt_s)
		self.vy_mm_s = _approach_speed(self.vy_mm_s, self._cmd_vy, self.max_accel, dt_s)
		cap = self.max_speed
		sp = math.hypot(self.vx_mm_s, self.vy_mm_s)
		if sp > cap and sp > 1e-6:
			s = cap / sp
			self.vx_mm_s *= s
			self.vy_mm_s *= s
		self.x_mm += self.vx_mm_s * dt_s
		self.y_mm += self.vy_mm_s * dt_s

	def _clamp(self) -> None:
		# Why: only kill the blocked axis so creep can slide along a wall.
		if self.x_mm < 0.0:
			self.x_mm = 0.0
			self.vx_mm_s = 0.0
			self._cmd_vx = 0.0
		elif self.x_mm > self.width_mm:
			self.x_mm = self.width_mm
			self.vx_mm_s = 0.0
			self._cmd_vx = 0.0
		if self.y_mm < 0.0:
			self.y_mm = 0.0
			self.vy_mm_s = 0.0
			self._cmd_vy = 0.0
		elif self.y_mm > self.height_mm:
			self.y_mm = self.height_mm
			self.vy_mm_s = 0.0
			self._cmd_vy = 0.0
		if self._mode == "position":
			self._tgt_x = min(max(self._tgt_x, 0.0), self.width_mm)
			self._tgt_y = min(max(self._tgt_y, 0.0), self.height_mm)

	def _drain_blocking(self) -> None:
		# Sim does not block the engine thread; chase uses wait_until_idle=False.
		return


def _approach_speed(current: float, target: float, accel: float, dt: float) -> float:
	dv = target - current
	step = accel * dt
	if abs(dv) <= step:
		return target
	return current + math.copysign(step, dv)
