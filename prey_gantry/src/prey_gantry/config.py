"""Load sim.json. Why: keep pylon-track numbers in one file, not scattered literals."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


def _default_config_path() -> Path:
	here = Path(__file__).resolve()
	return here.parents[2] / "config" / "sim.json"


@dataclass(frozen=True)
class CameraTiming:
	model: str
	width_px: int
	height_px: int
	gsd_mm_per_px: float
	exposure_us: float
	frame_rate_fps: float
	usb_transfer_ms: float
	tracking_pipeline_ms: float
	mount_height_mm: float
	lens_mm: float

	@property
	def width_mm(self) -> float:
		return self.width_px * self.gsd_mm_per_px

	@property
	def height_mm(self) -> float:
		return self.height_px * self.gsd_mm_per_px

	@property
	def frame_period_s(self) -> float:
		return 1.0 / self.frame_rate_fps

	@property
	def exposure_s(self) -> float:
		return self.exposure_us * 1e-6

	@property
	def grab_to_host_s(self) -> float:
		# Why: global-shutter exposure then USB3 transfer of Mono8 1920x1200.
		return self.exposure_s + self.usb_transfer_ms * 1e-3

	@property
	def grab_to_track_s(self) -> float:
		return self.grab_to_host_s + self.tracking_pipeline_ms * 1e-3


@dataclass(frozen=True)
class ChasePolicyConfig:
	"""Soft keep-away gains. Speeds stay low so the chase stays playable."""

	preferred_gap_mm: float
	min_gap_mm: float
	max_pull_mm: float
	away_gain: float
	toward_gain: float
	lateral_gain: float
	wall_margin_mm: float
	wall_gain: float
	corner_gain: float
	velocity_gain_s: float
	max_engage_speed_mm_s: float
	# Kept for HUD rings / older fields; not used for discrete flees anymore.
	cone_half_angle_deg: float
	threat_distance_mm: float
	creep_distance_mm: float


@dataclass(frozen=True)
class ZaberConfig:
	command_rtt_ms: float
	max_speed_mm_s: float
	max_accel_mm_s2: float
	home_x_mm: float
	home_y_mm: float
	comm: str


@dataclass(frozen=True)
class SimConfig:
	camera: CameraTiming
	chase: ChasePolicyConfig
	zaber: ZaberConfig
	control_period_ms: int
	stale_frame_ms: float
	trial_timeout_s: float


def load_sim_config(path: Path | None = None) -> SimConfig:
	cfg_path = path or _default_config_path()
	raw = json.loads(cfg_path.read_text(encoding="utf-8"))
	cam = raw["camera"]
	ch = raw["chase_policy"]
	zb = raw["zaber"]
	return SimConfig(
		camera=CameraTiming(
			model=cam["model"],
			width_px=int(cam["width_px"]),
			height_px=int(cam["height_px"]),
			gsd_mm_per_px=float(cam["gsd_mm_per_px"]),
			exposure_us=float(cam["exposure_us"]),
			frame_rate_fps=float(cam["frame_rate_fps"]),
			usb_transfer_ms=float(cam["usb_transfer_ms"]),
			tracking_pipeline_ms=float(cam["tracking_pipeline_ms"]),
			mount_height_mm=float(cam["mount_height_mm"]),
			lens_mm=float(cam["lens_mm"]),
		),
		chase=ChasePolicyConfig(
			preferred_gap_mm=float(ch["preferred_gap_mm"]),
			min_gap_mm=float(ch["min_gap_mm"]),
			max_pull_mm=float(ch["max_pull_mm"]),
			away_gain=float(ch["away_gain"]),
			toward_gain=float(ch["toward_gain"]),
			lateral_gain=float(ch["lateral_gain"]),
			wall_margin_mm=float(ch["wall_margin_mm"]),
			wall_gain=float(ch["wall_gain"]),
			corner_gain=float(ch["corner_gain"]),
			velocity_gain_s=float(ch["velocity_gain_s"]),
			max_engage_speed_mm_s=float(ch["max_engage_speed_mm_s"]),
			cone_half_angle_deg=float(ch.get("cone_half_angle_deg", 45.0)),
			threat_distance_mm=float(ch.get("threat_distance_mm", ch["preferred_gap_mm"])),
			creep_distance_mm=float(ch.get("creep_distance_mm", ch["preferred_gap_mm"] * 2)),
		),
		zaber=ZaberConfig(
			command_rtt_ms=float(zb["command_rtt_ms"]),
			max_speed_mm_s=float(zb["max_speed_mm_s"]),
			max_accel_mm_s2=float(zb["max_accel_mm_s2"]),
			home_x_mm=float(zb["home_x_mm"]),
			home_y_mm=float(zb["home_y_mm"]),
			comm=str(zb["comm"]),
		),
		control_period_ms=int(raw["control"]["period_ms"]),
		stale_frame_ms=float(raw["control"]["stale_frame_ms"]),
		trial_timeout_s=float(raw["trial"]["timeout_s"]),
	)
