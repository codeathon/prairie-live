"""Remote viewer talking to the Windows relay (any OS)."""

from __future__ import annotations

import json
import socket
import threading

import numpy as np

from prairie_live.protocol import recv_message
from prairie_live.relay import DEFAULT_PORT
from prairie_live.scan_assemble import ScanAssembler


class RelayClient:
	def __init__(self, host: str, port: int = DEFAULT_PORT, channel: int = 1):
		self.host = host
		self.port = port
		self.channel = channel
		self._frame_sock: socket.socket | None = None
		self._ctrl_sock: socket.socket | None = None
		self._ctrl_file = None
		self._latest: np.ndarray | None = None
		self._lock = threading.Lock()
		self._stop = threading.Event()
		self.last_error = ""
		self.last_rx = "?"
		self.last_px = -1
		self.frames_ok = 0
		self.frames_skip = 0
		self._scan = ScanAssembler()

	def connect(self) -> None:
		self._frame_sock = self._open_frame_sock()
		self._ctrl_sock = socket.create_connection((self.host, self.port + 1), timeout=5)
		# create_connection leaves a short timeout on the sock; Mark Points
		# -lmp can take longer than 5s under load.
		self._ctrl_sock.settimeout(120.0)
		self._ctrl_file = self._ctrl_sock.makefile("rwb")
		self._stop.clear()
		threading.Thread(target=self._recv_loop, daemon=True).start()

	def disconnect(self) -> None:
		self._stop.set()
		for s in (self._frame_sock, self._ctrl_sock):
			if s is not None:
				try:
					s.close()
				except OSError:
					pass
		self._frame_sock = None
		self._ctrl_sock = None
		self._ctrl_file = None

	def get_frame(self, channel: int = 1) -> np.ndarray | None:
		with self._lock:
			if self._latest is not None:
				return self._latest.copy()
		self.last_error = (
			f"last={self.last_rx} max={self.last_px} skip={self.frames_skip} "
			f"got={self.frames_ok}"
		)
		return None

	def start_tseries(self) -> dict:
		return self._command({"cmd": "tseries"})

	def abort(self) -> dict:
		return self._command({"cmd": "abort"})

	def abort_clear_stim(self) -> dict:
		"""Abort in-flight stim, load empty Mark Points series, restart LiveScan."""
		return self._command({"cmd": "abort_clear_stim"})

	def start_live(self) -> dict:
		return self._command({"cmd": "live", "state": "on"})

	def stop_live(self) -> dict:
		"""-LiveScan off (no Abort). Call before -slm/-mp on PV 6.1."""
		return self._command({"cmd": "live", "state": "off"})

	def ping(self) -> dict:
		return self._command({"cmd": "ping"})

	def get_state(
		self, key: str, index: str | None = None, subindex: str | None = None
	) -> dict:
		payload = {"cmd": "get_state", "key": key}
		if index is not None:
			payload["index"] = index
		if subindex is not None:
			payload["subindex"] = subindex
		return self._command(payload)

	def get_motor_position(self, axis: str, device: str | None = None) -> dict:
		payload = {"cmd": "get_motor_position", "axis": axis}
		if device is not None:
			payload["device"] = device
		return self._command(payload)

	def load_mark_points(self, xml: str, path: str | None = None) -> dict:
		"""Push Mark Points series XML; relay writes it locally and -lmp."""
		payload = {"cmd": "load_mark_points", "xml": xml}
		if path is not None:
			payload["path"] = path
		return self._command(payload)

	def mark_points(
		self,
		*,
		no_wait: bool | None = None,
		timeout: float | None = None,
	) -> dict:
		"""Run current Mark Points series on the scope (-MarkPoints)."""
		payload: dict = {"cmd": "mark_points"}
		if no_wait is not None:
			payload["no_wait"] = bool(no_wait)
		if timeout is not None:
			payload["timeout"] = float(timeout)
		return self._command(payload)

	def mark_all_points(
		self,
		parts: list[str],
		*,
		wait_ms: float = 0.0,
		no_wait: bool | None = None,
		timeout: float | None = None,
	) -> dict:
		"""Simultaneous SLM fire (-MarkAllPoints / -slm) on the scope."""
		payload: dict = {"cmd": "mark_all_points", "parts": list(parts)}
		if wait_ms and float(wait_ms) > 0:
			payload["wait_ms"] = float(wait_ms)
		if no_wait is not None:
			payload["no_wait"] = bool(no_wait)
		if timeout is not None:
			payload["timeout"] = float(timeout)
		return self._command(payload)

	def _command(self, payload: dict) -> dict:
		if self._ctrl_file is None:
			raise RuntimeError("not connected")
		self._ctrl_file.write((json.dumps(payload) + "\n").encode("utf-8"))
		self._ctrl_file.flush()
		line = self._ctrl_file.readline()
		if not line:
			raise ConnectionError("control socket closed")
		return json.loads(line.decode("utf-8"))

	def _open_frame_sock(self):
		sock = socket.create_connection((self.host, self.port), timeout=5)
		try:
			sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
		except OSError:
			pass
		sock.settimeout(5.0)
		return sock

	def _recv_loop(self) -> None:
		# Why: on some Python builds socket.timeout is OSError, so the old
		# handler treated an idle 1s wait as "socket closed" and never stored
		# a frame (HUD: no frame from relay) while the scope heartbeat was live.
		idle = (TimeoutError, getattr(socket, "timeout", TimeoutError))
		while not self._stop.is_set():
			sock = self._frame_sock
			if sock is None:
				try:
					sock = self._open_frame_sock()
					self._frame_sock = sock
				except OSError as e:
					print(f"view frame reconnect failed: {e}", flush=True)
					self._stop.wait(1.0)
					continue
			try:
				kind, payload = recv_message(sock)
			except idle:
				continue
			except (ConnectionError, OSError, ValueError) as e:
				print(f"view frame socket closed: {e}; reconnecting", flush=True)
				try:
					sock.close()
				except OSError:
					pass
				self._frame_sock = None
				continue
			if kind != "frame":
				continue
			self._take_frame(payload["image"])

	def _take_frame(self, img) -> None:
		h, w = int(img.shape[0]), int(img.shape[1])
		self.last_rx = f"{w}x{h}"
		self.last_px = int(img.flat[0]) if img.size else -1
		out = self._scan.push(img)
		if out is None:
			self.frames_skip += 1
			if self.frames_skip % 20 == 1:
				print(
					f"view skip {self.last_rx} max={self.last_px} n={self.frames_skip} "
					f"— kill extra python on scope, one relay --grab",
					flush=True,
				)
				self._drop_frame_sock()
			return
		self.frames_ok += 1
		if self.frames_ok == 1:
			print(
				f"view got {self.last_rx} -> {int(out.shape[1])}x{int(out.shape[0])} "
				f"max={int(out.max())}",
				flush=True,
			)
		with self._lock:
			self._latest = out

	def _drop_frame_sock(self) -> None:
		sock = self._frame_sock
		self._frame_sock = None
		if sock is not None:
			try:
				sock.close()
			except OSError:
				pass
