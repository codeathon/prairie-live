"""Embedded PsychoPy grating stimulus for mp-sync (legacy lab script parity).

mp-sync owns COM3 (RTS visual epoch + DTR photostim). This module only drives
the display; TTL timing is coordinated from sync_loop._run_stim_epoch.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class VisualParams:
	"""One trial's grating identity (logged on the JSONL row)."""

	visual_ori_deg: float
	visual_contrast_pct: float
	visual_stim_index: int


@dataclass(frozen=True)
class VisualConfig:
	enabled: bool = False
	screen: int = 1
	refresh_hz: float = 120.0
	isi_s: float = 2.0
	lead_ms: float = 100.0
	dtr_frames: int = 6
	grating_frames: int = 119
	orientations: tuple[float, ...] = (0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0)
	contrasts: tuple[float, ...] = (0.0, 16.0, 100.0)
	spatial_freq: float = 0.10
	temporal_freq: float = 4.0
	stim_size_deg: float = 300.0
	texture: str = "sqr"
	randomize: bool = True

	@property
	def frame_s(self) -> float:
		return 1.0 / max(self.refresh_hz, 1.0)

	@property
	def lead_frame(self) -> int:
		# Legacy stimOnTime=12 @ 120 Hz ≈ 100 ms.
		return max(int(round(self.lead_ms / 1000.0 / self.frame_s)), 0)

	@property
	def dtr_width_s(self) -> float:
		return max(self.dtr_frames * self.frame_s, 0.001)

	@property
	def epoch_s(self) -> float:
		return self.grating_frames * self.frame_s


def visual_config_from_dict(cfg: dict[str, Any]) -> VisualConfig:
	oris = cfg.get("visual_orientations")
	if oris is None:
		oris = list(VisualConfig.orientations)
	contrasts = cfg.get("visual_contrasts")
	if contrasts is None:
		contrasts = list(VisualConfig.contrasts)
	return VisualConfig(
		enabled=bool(cfg.get("visual_enabled", False)),
		screen=int(cfg.get("visual_screen", 1)),
		refresh_hz=float(cfg.get("visual_refresh_hz", 120.0)),
		isi_s=float(cfg.get("visual_isi_s", 2.0)),
		lead_ms=float(cfg.get("visual_lead_ms", 100.0)),
		dtr_frames=int(cfg.get("visual_dtr_frames", 6)),
		grating_frames=int(cfg.get("visual_grating_frames", 119)),
		orientations=tuple(float(x) for x in oris),
		contrasts=tuple(float(x) for x in contrasts),
		spatial_freq=float(cfg.get("visual_spatial_freq", 0.10)),
		temporal_freq=float(cfg.get("visual_temporal_freq", 4.0)),
		stim_size_deg=float(cfg.get("visual_stim_size_deg", 300.0)),
		texture=str(cfg.get("visual_texture", "sqr")),
		randomize=bool(cfg.get("visual_random", True)),
	)


class VisualStim(Protocol):
	@property
	def enabled(self) -> bool: ...

	def close(self) -> None: ...

	def pick_trial(self, rng: random.Random) -> VisualParams: ...

	def run_isi(self) -> None: ...

	def run_grating_epoch(
		self,
		params: VisualParams,
		*,
		on_rts_on: Callable[[], None],
		on_frame: Callable[[int], None],
		on_rts_off: Callable[[], None],
		poll: Callable[[], None] | None = None,
	) -> None: ...


class NullVisualStim:
	"""No display; sleep-based timing for tests and visual_enabled=false."""

	def __init__(self, cfg: VisualConfig):
		self._cfg = cfg

	@property
	def enabled(self) -> bool:
		return False

	def close(self) -> None:
		pass

	def pick_trial(self, rng: random.Random) -> VisualParams:
		return _pick_params(self._cfg, rng)

	def run_isi(self) -> None:
		if self._cfg.isi_s > 0:
			time.sleep(self._cfg.isi_s)

	def run_grating_epoch(
		self,
		params: VisualParams,
		*,
		on_rts_on: Callable[[], None],
		on_frame: Callable[[int], None],
		on_rts_off: Callable[[], None],
		poll: Callable[[], None] | None = None,
	) -> None:
		on_rts_on()
		dt = self._cfg.frame_s
		t0 = time.monotonic()
		for frame in range(self._cfg.grating_frames):
			on_frame(frame)
			if poll is not None:
				poll()
			target = t0 + (frame + 1) * dt
			delay = target - time.monotonic()
			if delay > 0:
				time.sleep(delay)
		on_rts_off()


def _screen_size_px(screen_idx: int) -> tuple[int, int]:
	"""Target display resolution for Monitor.setSizePix (required for deg units)."""
	try:
		import pyglet

		screens = pyglet.canvas.get_display().get_screens()
		if 0 <= screen_idx < len(screens):
			s = screens[screen_idx]
			return int(s.width), int(s.height)
	except Exception:
		pass
	return 1920, 1080


class GratingStimSession:
	"""Fullscreen PsychoPy grating on the animal monitor."""

	def __init__(self, cfg: VisualConfig):
		self._cfg = cfg
		from psychopy import monitors, visual

		w_px, h_px = _screen_size_px(cfg.screen)
		mon = monitors.Monitor("mp_sync")
		# PsychoPy needs pixel size before deg/pix conversion (mouse, TextBox2, etc.).
		mon.setSizePix((w_px, h_px))
		mon.setDistance(57.0)
		mon.setWidth(53.0)
		self._win = visual.Window(
			size=[w_px, h_px],
			monitor=mon,
			units="deg",
			screen=cfg.screen,
			fullscr=True,
			allowGUI=False,
			# Skip frame-rate splash; we time frames from config refresh_hz.
			checkTiming=False,
		)
		self._grating = visual.GratingStim(
			win=self._win,
			mask="circle",
			tex=cfg.texture,
			units="deg",
			pos=[0, 0],
			size=cfg.stim_size_deg,
			sf=cfg.spatial_freq,
			autoLog=False,
		)
		self._grating.setAutoDraw(True)
		# Phase advance per frame: temporal_freq cycles/s at refresh_hz.
		self._phase_step = cfg.temporal_freq / max(cfg.refresh_hz, 1.0)

	@property
	def enabled(self) -> bool:
		return True

	def close(self) -> None:
		self._win.close()

	def pick_trial(self, rng: random.Random) -> VisualParams:
		return _pick_params(self._cfg, rng)

	def run_isi(self) -> None:
		self._grating.setContrast(0)
		if self._cfg.isi_s <= 0:
			return
		t_end = time.monotonic() + self._cfg.isi_s
		while time.monotonic() < t_end:
			self._win.flip()

	def run_grating_epoch(
		self,
		params: VisualParams,
		*,
		on_rts_on: Callable[[], None],
		on_frame: Callable[[int], None],
		on_rts_off: Callable[[], None],
		poll: Callable[[], None] | None = None,
	) -> None:
		# Legacy: ori = lab_orientation - 90.
		self._grating.ori = params.visual_ori_deg - 90.0
		self._grating.setContrast(params.visual_contrast_pct / 100.0)
		on_rts_on()
		dt = self._cfg.frame_s
		t0 = time.monotonic()
		for frame in range(self._cfg.grating_frames):
			self._grating.setPhase(self._phase_step, "+")
			self._win.flip()
			on_frame(frame)
			if poll is not None:
				poll()
			target = t0 + (frame + 1) * dt
			delay = target - time.monotonic()
			if delay > 0:
				time.sleep(delay)
		self._grating.setContrast(0)
		self._win.flip()
		on_rts_off()


def _pick_params(cfg: VisualConfig, rng: random.Random) -> VisualParams:
	ori = float(rng.choice(cfg.orientations))
	con = float(rng.choice(cfg.contrasts))
	idx = int(
		cfg.contrasts.index(con) * len(cfg.orientations)
		+ list(cfg.orientations).index(ori)
	)
	return VisualParams(
		visual_ori_deg=ori,
		visual_contrast_pct=con,
		visual_stim_index=idx,
	)


def open_visual_stim(cfg: VisualConfig) -> VisualStim:
	if not cfg.enabled:
		return NullVisualStim(cfg)
	try:
		return GratingStimSession(cfg)
	except ImportError as exc:
		raise ImportError(
			"visual_enabled requires PsychoPy. "
			"pip install -r requirements-visual.txt"
		) from exc
