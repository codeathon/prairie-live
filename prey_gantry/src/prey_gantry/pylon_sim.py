"""Basler pylon–shaped grab pipeline with pylon-track camera timings.

Why: InstantCamera.OnImageGrabbed is not instantaneous — exposure, USB3
transfer, then MOG2/associator. LatestImageOnly drops frames if we fall behind.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from prey_gantry.config import CameraTiming
from prey_gantry.tracking_frame import (
	TrackingFrame,
	TrackingQuality,
	TrackState,
	TrialPhase,
)
from prey_gantry.chase_policy import fill_tracking_derived


@dataclass
class _PendingGrab:
	ready_s: float
	sample_s: float
	frame_index: int
	ferret_x: float
	ferret_y: float
	ferret_speed: float
	ferret_dir: float
	prey_x: float
	prey_y: float
	prey_speed: float
	prey_dir: float


class SimulatedPylonCamera:
	"""CBaslerUniversalInstantCamera stand-in: 200 fps Mono8, LatestImageOnly."""

	def __init__(self, timing: CameraTiming) -> None:
		self.timing = timing
		self.ExposureTime = timing.exposure_us
		self.AcquisitionFrameRate = timing.frame_rate_fps
		self.PixelFormat = "Mono8"
		self.Width = timing.width_px
		self.Height = timing.height_px
		self.GrabStrategy = "LatestImageOnly"
		self._next_t0 = 0.0
		self._index = 0
		self._pending: deque[_PendingGrab] = deque()
		self.last_grab_to_frame_ms = 0.0
		self.dropped = 0
		self.delivered = 0

	def start_grabbing(self) -> None:
		self._next_t0 = 0.0
		self._index = 0
		self._pending.clear()

	def poll(
		self,
		t_s: float,
		ferret: TrackState,
		prey: TrackState,
		trial: TrialPhase,
	) -> TrackingFrame | None:
		self._maybe_expose(t_s, ferret, prey)
		return self._maybe_deliver(t_s, trial)

	def _maybe_expose(self, t_s: float, ferret: TrackState, prey: TrackState) -> None:
		period = self.timing.frame_period_s
		while self._next_t0 <= t_s:
			t0 = self._next_t0
			self._next_t0 += period
			# Why: sample at mid-exposure — global shutter still integrates over 3 ms.
			self._pending.append(
				_PendingGrab(
					ready_s=t0 + self.timing.grab_to_track_s,
					sample_s=t0 + self.timing.exposure_s * 0.5,
					frame_index=self._index,
					ferret_x=ferret.x_mm,
					ferret_y=ferret.y_mm,
					ferret_speed=ferret.speed_mm_s,
					ferret_dir=ferret.direction_deg,
					prey_x=prey.x_mm,
					prey_y=prey.y_mm,
					prey_speed=prey.speed_mm_s,
					prey_dir=prey.direction_deg,
				)
			)
			self._index += 1
			# LatestImageOnly: keep one in-flight burst, drop the oldest extra.
			while len(self._pending) > 4:
				self._pending.popleft()
				self.dropped += 1

	def _maybe_deliver(self, t_s: float, trial: TrialPhase) -> TrackingFrame | None:
		latest: _PendingGrab | None = None
		while self._pending and self._pending[0].ready_s <= t_s:
			latest = self._pending.popleft()
			if self._pending and self._pending[0].ready_s <= t_s:
				self.dropped += 1
		if latest is None:
			return None
		self.delivered += 1
		self.last_grab_to_frame_ms = (latest.ready_s - (latest.sample_s - self.timing.exposure_s * 0.5)) * 1e3
		frame = TrackingFrame(
			frame_index=latest.frame_index,
			camera_ts_ticks=int(latest.sample_s * 1e9),
			host_time_ns=int(latest.ready_s * 1e9),
			ferret=TrackState(
				latest.ferret_x,
				latest.ferret_y,
				latest.ferret_speed,
				latest.ferret_dir,
				True,
			),
			prey=TrackState(
				latest.prey_x,
				latest.prey_y,
				latest.prey_speed,
				latest.prey_dir,
				True,
			),
			quality=TrackingQuality(1.0, 1.0),
			trial_phase=trial,
		)
		fill_tracking_derived(frame)
		return frame
