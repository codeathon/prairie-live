"""Scene packet matching pylon-track TrackingFrame (mm, host_time_ns)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TrialPhase(str, Enum):
	warmup = "warmup"
	running = "running"
	ended = "ended"


@dataclass
class TrackState:
	x_mm: float = 0.0
	y_mm: float = 0.0
	speed_mm_s: float = 0.0
	direction_deg: float = 0.0
	valid: bool = False


@dataclass
class TrackingQuality:
	ferret_confidence: float = 0.0
	prey_confidence: float = 0.0
	reject_reason: str | None = None


@dataclass
class TrackingFrame:
	frame_index: int = 0
	camera_ts_ticks: int = 0
	host_time_ns: int = 0
	ferret: TrackState = field(default_factory=TrackState)
	prey: TrackState = field(default_factory=TrackState)
	quality: TrackingQuality = field(default_factory=TrackingQuality)
	distance_mm: float = -1.0
	bearing_deg: float = 0.0
	closing_speed_mm_s: float = 0.0
	trial_phase: TrialPhase = TrialPhase.warmup

	def both_valid(self) -> bool:
		return self.ferret.valid and self.prey.valid
