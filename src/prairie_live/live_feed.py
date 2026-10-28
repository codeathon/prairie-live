"""Background live FOV from the scope relay (starts -LiveScan when up)."""

from __future__ import annotations

import threading
import time

import numpy as np

from prairie_live.lab_defaults import DEFAULT_RELAY
from prairie_live.relay_status import parse_relay_addr, probe_relay
from prairie_live.trial_images import png_bytes_rgb, to_u8
from prairie_live.viewer import orient_fov


class LiveFeed:
	def __init__(self, addr: str = DEFAULT_RELAY) -> None:
		self.addr = addr
		self.png: bytes | None = None
		self.ok = False
		self.detail = "connecting…"
		self.live_started = False
		self.geometry: dict | None = None
		self.geometry_error = ""
		self.frame_wh: tuple[int, int] | None = None
		self._stop = threading.Event()
		# Set while mp-sync fires: never -LiveScan on (would undo stim Live-off).
		self._hold_stim = threading.Event()
		self._lock = threading.Lock()
		self._thread: threading.Thread | None = None

	def start(self) -> None:
		if self._thread and self._thread.is_alive():
			return
		self._stop.clear()
		self._thread = threading.Thread(target=self._loop, daemon=True)
		self._thread.start()

	def stop(self) -> None:
		self._stop.set()

	def hold_for_stim(self, held: bool) -> None:
		"""During a Run, do not call -LiveScan on (races serial -slm Trig In 1)."""
		if held:
			self._hold_stim.set()
			with self._lock:
				self.detail = "stim hold — not starting LiveScan"
		else:
			self._hold_stim.clear()

	def set_addr(self, addr: str) -> None:
		with self._lock:
			if addr and addr != self.addr:
				self.addr = addr
				self.ok = False
				self.live_started = False
				self.detail = "reconnecting…"

	def snapshot(self) -> dict:
		with self._lock:
			return {
				"ok": self.ok,
				"detail": self.detail,
				"has_frame": self.png is not None,
				"live_started": self.live_started,
				"stim_hold": self._hold_stim.is_set(),
				"addr": self.addr,
			}

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

	def _loop(self) -> None:
		while not self._stop.is_set():
			# During a Run, idle — do not reconnect into -LiveScan on.
			if self._hold_stim.is_set():
				with self._lock:
					self.ok = False
					self.detail = "stim hold — live FOV paused"
				self._stop.wait(0.5)
				continue
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
			# mp-sync turns Live off for -slm; starting it here races Trig In 1.
			if self._hold_stim.is_set():
				with self._lock:
					self.detail = "stim hold — LiveScan left off"
			else:
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
			# Zoom/pan change the scan range, so the Mark Points overlay re-reads it.
			next_geom: float | None = 0.0
			while not self._stop.is_set():
				# Hold flipped mid-stream (Run started): drop this session so we
				# do not keep a client that might reconnect into -LiveScan on.
				if self._hold_stim.is_set() and started:
					with self._lock:
						self.detail = "stim hold — releasing live client"
						self.live_started = False
					break
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
				img = np.asarray(frame)
				if img.ndim > 2:
					img = img[..., 0]
				oriented = orient_fov(img.astype(np.float64))
				u8 = to_u8(oriented)
				png = png_bytes_rgb(np.stack([u8, u8, u8], axis=-1))
				with self._lock:
					self.png = png
					self.frame_wh = (int(oriented.shape[1]), int(oriented.shape[0]))
					self.ok = True
					if started and not self._hold_stim.is_set():
						self.detail = "live FOV"
				self._stop.wait(0.08)
		finally:
			client.disconnect()
