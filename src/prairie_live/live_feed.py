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
	to_u8_clim,
)
from prairie_live.viewer import orient_fov

BASELINE_N = 60
# Spatial mean-bin for the GUI PNG only (full-res kept for geometry / ROI).
DISPLAY_BIN = 4
# Fixed Raw display clim (ADU) — match PV Z-scale; avoids percentile noise pump.
DISPLAY_CLIM_LO = 0.0
DISPLAY_CLIM_HI = 100_000.0


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
				self.png = self._encode_locked(self._current)
			return self._snapshot_locked()

	def snapshot(self) -> dict:
		with self._lock:
			return self._snapshot_locked()

	def scan_geometry(self) -> tuple[dict | None, tuple[int, int] | None]:
		with self._lock:
			return self.geometry, self.frame_wh

	def scan_geometry_error(self) -> str:
		with self._lock:
			return self.geometry_error

	def _poll_geometry(self, client) -> float | None:
		"""Seconds until the next poll; None stops polling on this connection."""
		from prairie_live.scan_geometry import fetch_scan_geometry

		err = ""
		geom = None
		try:
			geom = fetch_scan_geometry(client)
		except Exception as exc:
			err = f"{type(exc).__name__}: {exc}"[:200]
		if err and "dropped the connection" in err:
			# Don't keep hammering PV; keep last good geometry so MP overlay stays up.
			err = f"{err} — scan range polling paused"
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
		if not err:
			return 2.0
		# Retry later even after a drop; permanent None wiped the overlay before.
		return 30.0 if "polling paused" in err else 15.0

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
			if self._collecting:
				self._buf.push(oriented)
				if len(self._buf) >= self._baseline_n:
					self._baseline = self._buf.mean()
					self._collecting = False
					self._mode = "dff"
			self.png = self._encode_locked(oriented)
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

	def _raw_u8(self, disp: np.ndarray) -> np.ndarray:
		return to_u8_clim(disp, DISPLAY_CLIM_LO, DISPLAY_CLIM_HI)

	def _encode_locked(self, oriented: np.ndarray) -> bytes:
		# Bin display only — keep full-res _current / frame_wh for overlays.
		disp = bin_mean(oriented, DISPLAY_BIN)
		if self._mode == "dff" and self._baseline is not None:
			base = bin_mean(self._baseline, DISPLAY_BIN)
			if base.shape != disp.shape:
				u8 = self._raw_u8(disp)
				return png_bytes_rgb(np.stack([u8, u8, u8], axis=-1))
			rgb = dff_rgb(base, disp)
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
			"clim_lo": DISPLAY_CLIM_LO,
			"clim_hi": DISPLAY_CLIM_HI,
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
			# Zoom/pan change the scan range, so the Mark Points overlay re-reads it.
			next_geom: float | None = 0.0
			while not self._stop.is_set():
				if next_geom is not None and time.monotonic() >= next_geom:
					wait = self._poll_geometry(client)
					next_geom = None if wait is None else time.monotonic() + wait
				with self._lock:
					if self.addr != addr:
						break
				frame = client.get_frame()
				if frame is None:
					self._stop.wait(0.08)
					continue
				self.ingest_frame(frame)
				with self._lock:
					if started and not self._collecting and self._mode == "raw":
						from prairie_live.scan_geometry import format_optics_line

						self.detail = "live · " + format_optics_line(
							self.geometry, self.frame_wh
						)
				self._stop.wait(0.08)
		finally:
			client.disconnect()
