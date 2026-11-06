"""Background live FOV from the scope relay (starts -LiveScan when up).

Supports raw grayscale or %ΔF/F against a 60-frame baseline (auto + button).
"""

from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np

from prairie_live.lab_defaults import DEFAULT_RELAY
from prairie_live.relay_status import parse_relay_addr, probe_relay
from prairie_live.trial_images import (
	FrameBuffer,
	dff_rgb,
	disk_mean,
	png_bytes_rgb,
	to_u8,
	to_u8_clim,
)
from prairie_live.viewer import orient_fov

BASELINE_N = 60
# Spatial mean-bin for the GUI PNG only (full-res kept for geometry / ROI).
DISPLAY_BIN = 4

# Temporal display smooth (~12 Hz relay): frame counts + optional post-bin blur σ.
SMOOTH_LEVELS: dict[str, dict[str, float | int]] = {
	"off": {"n": 1, "blur": 0.0},
	"low": {"n": 4, "blur": 0.0},
	"med": {"n": 8, "blur": 0.5},
	"high": {"n": 18, "blur": 1.0},
}
DEFAULT_SMOOTH = "med"

# Candidate -gts keys for PV image-window Z-scale (min/max). First hit wins.
_PV_CLIM_KEY_PAIRS: tuple[tuple[str, str], ...] = (
	("displayMin", "displayMax"),
	("min", "max"),
	("channelMin", "channelMax"),
	("imageMin", "imageMax"),
	("minDataValue", "maxDataValue"),
)


def bin_mean(img: np.ndarray, factor: int = DISPLAY_BIN) -> np.ndarray:
	"""Mean-pool ``factor``×``factor`` blocks (crop to a multiple of ``factor``)."""
	f = max(int(factor), 1)
	if f == 1:
		return np.asarray(img, dtype=np.float64)
	arr = np.asarray(img, dtype=np.float64)
	h, w = arr.shape[:2]
	h2, w2 = h // f, w // f
	if h2 < 1 or w2 < 1:
		return arr
	cropped = arr[: h2 * f, : w2 * f]
	return cropped.reshape(h2, f, w2, f).mean(axis=(1, 3))


def gaussian_blur(img: np.ndarray, sigma: float) -> np.ndarray:
	"""Separable Gaussian (numpy only); no-op when ``sigma`` ≤ 0."""
	sig = float(sigma)
	if sig <= 0:
		return np.asarray(img, dtype=np.float64)
	arr = np.asarray(img, dtype=np.float64)
	radius = max(1, int(round(2.0 * sig)))
	x = np.arange(-radius, radius + 1, dtype=np.float64)
	kern = np.exp(-(x * x) / (2.0 * sig * sig))
	kern /= kern.sum()
	pad = np.pad(arr, radius, mode="reflect")
	# Rows then columns (valid length restores original shape).
	rows = np.vstack(
		[np.convolve(pad[i], kern, mode="valid") for i in range(pad.shape[0])]
	)
	cols = np.column_stack(
		[np.convolve(rows[:, j], kern, mode="valid") for j in range(rows.shape[1])]
	)
	return cols


def _link_dead(exc: BaseException) -> bool:
	"""True when the analysis↔relay control socket is gone (reconnect LiveFeed).

	A soft ``ValueError`` from ``fetch_scan_geometry`` that only says PV dropped
	(relay already reconnected) is *not* link-dead — caller re-issues LiveScan.
	"""
	if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
		return True
	if isinstance(exc, ConnectionError) and not isinstance(exc, TimeoutError):
		return True
	msg = f"{type(exc).__name__}: {exc}".lower()
	return any(
		s in msg
		for s in (
			"10054",
			"forcibly closed",
			"control socket closed",
			"broken pipe",
			"connection reset by peer",
		)
	)


class LiveFeed:
	def __init__(self, addr: str = DEFAULT_RELAY, *, baseline_n: int = BASELINE_N) -> None:
		self.addr = addr
		self.png: bytes | None = None
		self.ok = False
		self.detail = "connecting…"
		self.live_started = False
		self.geometry: dict | None = None
		self.geometry_error = ""
		self.frame_wh: tuple[int, int] | None = None
		self._stop = threading.Event()
		self._lock = threading.Lock()
		self._thread: threading.Thread | None = None
		self._baseline_n = max(int(baseline_n), 1)
		self._buf = FrameBuffer(self._baseline_n)
		self._collecting = True
		self._baseline: np.ndarray | None = None
		self._current: np.ndarray | None = None
		self._mode = "raw"  # "raw" | "dff"
		self._smooth = DEFAULT_SMOOTH
		self._disp_buf = FrameBuffer(int(SMOOTH_LEVELS[DEFAULT_SMOOTH]["n"]))
		# Display Z-scale: None = auto percentile / auto ΔF/F lim.
		self._clim_lo: float | None = None
		self._clim_hi: float | None = None
		self._dff_lim: float | None = None
		self._scale_source = "auto"  # auto | manual | pv
		self._pv_scale_note = ""
		self._grab_channel = 1
		# Live stream's RelayClient (control + frames). Used by pull_pv_scale_now
		# so the API does not open a competing frame socket.
		self._active_client = None

	def start(self) -> None:
		if self._thread and self._thread.is_alive():
			return
		self._stop.clear()
		self._thread = threading.Thread(target=self._loop, daemon=True)
		self._thread.start()

	def stop(self) -> None:
		self._stop.set()

	def set_addr(self, addr: str) -> None:
		with self._lock:
			if addr and addr != self.addr:
				self.addr = addr
				self.ok = False
				self.live_started = False
				self.detail = "reconnecting…"
				self._reset_baseline_locked(collect=True)

	def request_baseline(self) -> dict[str, Any]:
		"""Clear F0 and re-collect the next ``baseline_n`` frames."""
		with self._lock:
			self._reset_baseline_locked(collect=True)
			return self._snapshot_locked()

	def set_mode(self, mode: str) -> dict[str, Any]:
		m = str(mode or "").strip().lower()
		if m not in ("raw", "dff"):
			raise ValueError("mode must be 'raw' or 'dff'")
		with self._lock:
			if m == "dff" and self._baseline is None:
				raise ValueError("baseline not ready")
			self._mode = m
			if self._current is not None:
				self.png = self._encode_locked()
			return self._snapshot_locked()

	def set_smooth(self, level: str) -> dict[str, Any]:
		"""Temporal display smooth: off | low | med | high."""
		key = str(level or "").strip().lower()
		if key not in SMOOTH_LEVELS:
			raise ValueError("smooth must be off|low|med|high")
		with self._lock:
			self._smooth = key
			n = int(SMOOTH_LEVELS[key]["n"])
			self._disp_buf = FrameBuffer(n)
			if self._current is not None:
				self._disp_buf.push(self._current)
				self.png = self._encode_locked()
			return self._snapshot_locked()

	def set_scale(
		self,
		*,
		lo: float | None = None,
		hi: float | None = None,
		dff_lim: float | None = None,
		auto: bool = False,
		source: str | None = None,
	) -> dict[str, Any]:
		"""Set Raw clim (ADU) and/or fixed |ΔF/F| lim. ``auto=True`` clears fixed scale."""
		with self._lock:
			if auto:
				self._clim_lo = None
				self._clim_hi = None
				self._dff_lim = None
				self._scale_source = "auto"
				self._pv_scale_note = ""
			else:
				if lo is not None:
					self._clim_lo = float(lo)
				if hi is not None:
					self._clim_hi = float(hi)
				if dff_lim is not None:
					self._dff_lim = float(dff_lim) if float(dff_lim) > 0 else None
				if source:
					self._scale_source = str(source)
				elif self._clim_hi is not None or self._dff_lim is not None:
					self._scale_source = "manual"
			if self._current is not None:
				self.png = self._encode_locked()
			return self._snapshot_locked()

	def set_grab_channel(self, channel: int) -> None:
		with self._lock:
			self._grab_channel = max(int(channel), 1)

	def snapshot(self) -> dict:
		with self._lock:
			return self._snapshot_locked()

	def scan_geometry(self) -> tuple[dict | None, tuple[int, int] | None]:
		with self._lock:
			return self.geometry, self.frame_wh

	def scan_geometry_error(self) -> str:
		with self._lock:
			return self.geometry_error

	def _poll_geometry(self, client) -> float:
		"""Seconds until the next poll.

		Raises ``ConnectionError`` when the relay control link is dead so the
		live thread can disconnect and open a fresh session (+ ``-LiveScan``).
		A PV-side bounce that the relay already recovered from re-issues
		``-LiveScan`` here (Live often stops when PV drops the script socket).
		"""
		from prairie_live.scan_geometry import fetch_scan_geometry

		err = ""
		geom = None
		try:
			geom = fetch_scan_geometry(client)
		except Exception as exc:
			err = f"{type(exc).__name__}: {exc}"[:200]
			if _link_dead(exc):
				with self._lock:
					self.geometry_error = err
				raise ConnectionError(err) from exc
		pv_bounced = bool(err) and "dropped the connection" in err.lower()
		if pv_bounced:
			# Relay reconnected to PV but LiveScan is usually off after a drop.
			try:
				client.start_live()
			except Exception as live_exc:
				err = f"{err}; LiveScan re-issue failed ({live_exc})"[:200]
			else:
				err = f"{err} — scan range paused; LiveScan re-issued"
		with self._lock:
			if geom is not None:
				self.geometry = geom
				self.geometry_error = ""
			else:
				# Keep prior amplitude/center — clearing it hides galvo Mark Points.
				self.geometry_error = err
				had = self.geometry is not None
				if had and err:
					self.geometry_error = f"{err} (using last scan range)"
		# Refresh image Z-scale from PV when user asked for PV scale.
		with self._lock:
			want_pv = self._scale_source == "pv"
			ch = self._grab_channel
		if want_pv and not err:
			self._pull_pv_scale(client, ch)
		if not err:
			return 2.0
		# Back off after a bounce; soft parse errors retry sooner.
		return 30.0 if pv_bounced else 15.0

	def _pull_pv_scale(self, client, channel: int) -> bool:
		"""Try candidate -gts keys for channel display min/max. Returns True on hit."""
		from prairie_live.scan_geometry import _try_state

		ch = str(int(channel))
		tried: list[str] = []
		for kmin, kmax in _PV_CLIM_KEY_PAIRS:
			# Prefer keyed-by-channel, then bare key.
			for index in (ch, None):
				hi = _try_state(client, kmax, index)
				label = f"{kmax}" + (f"[{index}]" if index else "")
				tried.append(label)
				if hi is None or hi <= 0:
					continue
				lo = _try_state(client, kmin, index)
				if lo is None:
					lo = 0.0
				with self._lock:
					self._clim_lo = float(lo)
					self._clim_hi = float(hi)
					self._scale_source = "pv"
					self._pv_scale_note = f"PV {label}={hi:g}"
					if self._current is not None:
						self.png = self._encode_locked()
				return True
		with self._lock:
			self._pv_scale_note = (
				"no PV display min/max via -gts ("
				+ ", ".join(tried[:6])
				+ "…); use Scale slider"
			)
		return False

	def pull_pv_scale_now(self, client=None) -> dict[str, Any]:
		"""One-shot pull (API). Sets source=pv and probes keys.

		Uses ``client`` when given; otherwise the live stream's active client.
		If neither is available, marks source=pv so the next geometry poll retries.
		"""
		with self._lock:
			self._scale_source = "pv"
			ch = self._grab_channel
			c = client if client is not None else self._active_client
		if c is None:
			with self._lock:
				self._pv_scale_note = "PV scale pending (waiting for live link)"
				snap = self._snapshot_locked()
			snap["pv_scale_ok"] = False
			return snap
		ok = self._pull_pv_scale(c, ch)
		with self._lock:
			snap = self._snapshot_locked()
		snap["pv_scale_ok"] = ok
		return snap

	def png_bytes(self) -> bytes | None:
		with self._lock:
			return self.png

	def ingest_frame(self, frame: np.ndarray) -> None:
		"""Orient + encode one frame (used by the live thread and unit tests)."""
		img = np.asarray(frame)
		if img.ndim > 2:
			img = img[..., 0]
		oriented = orient_fov(img.astype(np.float64))
		with self._lock:
			self._current = oriented
			self.frame_wh = (int(oriented.shape[1]), int(oriented.shape[0]))
			self._disp_buf.push(oriented)
			if self._collecting:
				self._buf.push(oriented)
				if len(self._buf) >= self._baseline_n:
					self._baseline = self._buf.mean()
					self._collecting = False
					self._mode = "dff"
			self.png = self._encode_locked()
			self.ok = True

	def roi_dff_pct(
		self,
		points: list[dict[str, Any]],
		*,
		radius: int = 3,
	) -> dict[str, float]:
		"""Per-point disk %ΔF/F vs baseline; keys are point ids."""
		with self._lock:
			cur = None if self._current is None else self._current.copy()
			base = None if self._baseline is None else self._baseline.copy()
		if cur is None or base is None:
			return {}
		out: dict[str, float] = {}
		for i, pt in enumerate(points):
			x = pt.get("fx", pt.get("x"))
			y = pt.get("fy", pt.get("y"))
			if x is None or y is None:
				continue
			try:
				xf, yf = float(x), float(y)
			except (TypeError, ValueError):
				continue
			b = disk_mean(base, xf, yf, int(radius))
			a = disk_mean(cur, xf, yf, int(radius))
			if b is None or a is None or abs(b) < 1e-9:
				continue
			out[str(pt.get("id", i))] = float((a - b) / b * 100.0)
		return out

	def _reset_baseline_locked(self, *, collect: bool) -> None:
		self._buf = FrameBuffer(self._baseline_n)
		self._baseline = None
		self._collecting = bool(collect)
		self._mode = "raw"
		n = int(SMOOTH_LEVELS.get(self._smooth, SMOOTH_LEVELS[DEFAULT_SMOOTH])["n"])
		self._disp_buf = FrameBuffer(n)

	def _raw_u8(self, disp: np.ndarray) -> np.ndarray:
		if self._clim_hi is not None:
			lo = 0.0 if self._clim_lo is None else float(self._clim_lo)
			return to_u8_clim(disp, lo, float(self._clim_hi))
		return to_u8(disp)

	def _encode_locked(self) -> bytes:
		# Rolling mean → 4×4 bin → optional blur. Full-res _current stays for ROI.
		src = self._disp_buf.mean()
		if src is None:
			src = self._current
		if src is None:
			return b""
		disp = bin_mean(src, DISPLAY_BIN)
		blur = float(SMOOTH_LEVELS.get(self._smooth, SMOOTH_LEVELS["off"])["blur"])
		if blur > 0:
			disp = gaussian_blur(disp, blur)
		if self._mode == "dff" and self._baseline is not None:
			base = bin_mean(self._baseline, DISPLAY_BIN)
			if blur > 0:
				base = gaussian_blur(base, blur)
			if base.shape != disp.shape:
				u8 = self._raw_u8(disp)
				return png_bytes_rgb(np.stack([u8, u8, u8], axis=-1))
			rgb = dff_rgb(base, disp, lim=self._dff_lim)
			return png_bytes_rgb(rgb)
		u8 = self._raw_u8(disp)
		return png_bytes_rgb(np.stack([u8, u8, u8], axis=-1))

	def _snapshot_locked(self) -> dict:
		optics = {}
		if self.geometry:
			from prairie_live.scan_geometry import optics_from_geometry

			optics = optics_from_geometry(self.geometry)
		if self.frame_wh:
			optics["image_width_px"] = int(self.frame_wh[0])
			optics["image_height_px"] = int(self.frame_wh[1])
		n_buf = len(self._buf)
		detail = self.detail
		if self._collecting and self.ok:
			detail = f"baseline {n_buf}/{self._baseline_n}…"
		elif self._mode == "dff" and self._baseline is not None and self.ok:
			from prairie_live.scan_geometry import format_optics_line

			detail = "ΔF/F · " + format_optics_line(self.geometry, self.frame_wh)
		cfg = SMOOTH_LEVELS.get(self._smooth, SMOOTH_LEVELS[DEFAULT_SMOOTH])
		return {
			"ok": self.ok,
			"detail": detail,
			"has_frame": self.png is not None,
			"live_started": self.live_started,
			"addr": self.addr,
			"optics": optics,
			"mode": self._mode,
			"baseline_ready": self._baseline is not None,
			"baseline_frames": n_buf if self._collecting else self._baseline_n,
			"baseline_n": self._baseline_n,
			"collecting_baseline": self._collecting,
			"smooth": self._smooth,
			"smooth_frames": int(cfg["n"]),
			"smooth_levels": list(SMOOTH_LEVELS.keys()),
			"scale_source": self._scale_source,
			"clim_lo": self._clim_lo,
			"clim_hi": self._clim_hi,
			"dff_lim": self._dff_lim,
			"pv_scale_note": self._pv_scale_note,
			"grab_channel": self._grab_channel,
		}

	def _loop(self) -> None:
		while not self._stop.is_set():
			with self._lock:
				addr = self.addr
			st = probe_relay(addr, timeout=1.2)
			if not (st.get("up") and st.get("pv")):
				with self._lock:
					self.ok = False
					self.detail = st.get("detail") or "relay / PV not ready"
				self._stop.wait(2.0)
				continue
			try:
				self._stream(addr)
			except Exception as exc:
				with self._lock:
					self.ok = False
					self.detail = f"live dropped ({exc})"
				self._stop.wait(2.0)

	def _stream(self, addr: str) -> None:
		from prairie_live.relay_client import RelayClient

		host, port = parse_relay_addr(addr)
		client = RelayClient(host, port)
		client.connect()
		started = False
		try:
			with self._lock:
				self._active_client = client
			try:
				client.start_live()
				started = True
			except Exception as exc:
				with self._lock:
					self.detail = f"connected; LiveScan failed ({exc})"
			else:
				with self._lock:
					self.live_started = True
					self.detail = "LiveScan on"
					# Fresh connection → collect a new baseline once.
					self._reset_baseline_locked(collect=True)
			# Defer -gts until Live is producing frames. A geometry poll that
			# runs first can reset PV/relay and leave the FOV blank forever
			# while this loop swallows the error and waits on a dead stream.
			next_geom: float | None = None
			while not self._stop.is_set():
				if next_geom is not None and time.monotonic() >= next_geom:
					try:
						wait = self._poll_geometry(client)
					except ConnectionError as exc:
						with self._lock:
							self.ok = False
							self.detail = f"live reconnecting ({exc})"[:180]
							self.png = None
						raise
					next_geom = time.monotonic() + wait
				with self._lock:
					if self.addr != addr:
						break
				frame = client.get_frame()
				if frame is None:
					self._stop.wait(0.08)
					continue
				self.ingest_frame(frame)
				if next_geom is None:
					# First frame in — ask for scan range shortly after.
					next_geom = time.monotonic() + 1.0
				with self._lock:
					if started and not self._collecting and self._mode == "raw":
						from prairie_live.scan_geometry import format_optics_line

						self.detail = "live · " + format_optics_line(
							self.geometry, self.frame_wh
						)
				self._stop.wait(0.08)
		finally:
			with self._lock:
				if self._active_client is client:
					self._active_client = None
			client.disconnect()
