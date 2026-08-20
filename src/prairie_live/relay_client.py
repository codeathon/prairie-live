"""Remote viewer talking to the Windows relay (any OS)."""

from __future__ import annotations

import json
import socket
import threading

import numpy as np

from prairie_live.protocol import recv_message
from prairie_live.relay import DEFAULT_PORT


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

	def connect(self) -> None:
		self._frame_sock = socket.create_connection((self.host, self.port), timeout=5)
		self._ctrl_sock = socket.create_connection((self.host, self.port + 1), timeout=5)
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
			return None if self._latest is None else self._latest.copy()

	def start_tseries(self) -> dict:
		return self._command({"cmd": "tseries"})

	def abort(self) -> dict:
		return self._command({"cmd": "abort"})

	def start_live(self) -> dict:
		return self._command({"cmd": "live"})

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

	def _command(self, payload: dict) -> dict:
		if self._ctrl_file is None:
			raise RuntimeError("not connected")
		self._ctrl_file.write((json.dumps(payload) + "\n").encode("utf-8"))
		self._ctrl_file.flush()
		line = self._ctrl_file.readline()
		if not line:
			raise ConnectionError("control socket closed")
		return json.loads(line.decode("utf-8"))

	def _recv_loop(self) -> None:
		sock = self._frame_sock
		if sock is None:
			return
		sock.settimeout(1.0)
		while not self._stop.is_set():
			try:
				kind, payload = recv_message(sock)
			except TimeoutError:
				continue
			except (ConnectionError, OSError, ValueError):
				break
			if kind != "frame":
				continue
			with self._lock:
				self._latest = payload["image"]
