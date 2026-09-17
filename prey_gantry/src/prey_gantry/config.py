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
	min_chain_speed_mps: float
	max_chain_speed_mps: float
	cone_half_angle_deg: float
	threat_distance_mm: float
	creep_distance_mm: float
	flee_threat_threshold: float
	min_flee_mm: float
	max_flee_mm: float
	flee_gap_gain: float
	flee_speed_gain: float
	flee_accel_mps2: float
	hunt_event_min_interval_ms: int


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
			min_chain_speed_mps=float(ch["min_chain_speed_mps"]),
			max_chain_speed_mps=float(ch["max_chain_speed_mps"]),
			cone_half_angle_deg=float(ch["cone_half_angle_deg"]),
			threat_distance_mm=float(ch["threat_distance_mm"]),
			creep_distance_mm=float(ch["creep_distance_mm"]),
			flee_threat_threshold=float(ch["flee_threat_threshold"]),
			min_flee_mm=float(ch["min_flee_mm"]),
			max_flee_mm=float(ch["max_flee_mm"]),
			flee_gap_gain=float(ch["flee_gap_gain"]),
			flee_speed_gain=float(ch["flee_speed_gain"]),
			flee_accel_mps2=float(ch["flee_accel_mps2"]),
			hunt_event_min_interval_ms=int(ch["hunt_event_min_interval_ms"]),
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
