"""PrairieView over its own script port (TCP 1236) — no PrairieLink COM.

One logged-in session carries everything: -gi frames into our buffer,
Mark Points, -gts state, Live/T-series/Abort. PV parses a second login on
the same port as a command ("Unexpected parameter <password>"), so callers
share this one object and its lock instead of opening more sockets.
"""

from __future__ import annotations

import os
import tempfile
import threading

import numpy as np

from prairie_live.gi_grab import GiGrabber, GiResult
from prairie_live.prairie_link import PrairieLink
from prairie_live.tcp_backend import DEFAULT_PORT

_AXES = ("X", "Y", "Z")


def unique_mp_path(path: str | None) -> str:
	"""Fresh temp file per load unless a path is given (no PV replace dialogs)."""
	if path:
		return path
	fd, p = tempfile.mkstemp(prefix="prairie_live_mp_", suffix=".xml")
	os.close(fd)
	return p


def _first_float(text: str) -> float | None:
	for tok in str(text).replace(",", " ").split():
		try:
			return float(tok)
		except ValueError:
			continue
	return None


class PvScript:
	def __init__(
		self,
		host: str = "127.0.0.1",
		password: str = "0000",
		*,
		port: int = DEFAULT_PORT,
		image_wh: tuple[int, int] = (512, 512),
	):
		self.host = host
		self.password = password
		self.port = port
		self.wh = (int(image_wh[0]), int(image_wh[1]))
		self.link: PrairieLink | None = None
		self.gi: GiGrabber | None = None
		self.lock = threading.Lock()
		self.last_error = ""

	def connect(self) -> None:
		self.link = PrairieLink(
			host=self.host, port=self.port, password=self.password, timeout=10.0
		)
		self.gi = GiGrabber(self.link)
		self.wh = self.gi.fov_wh(self.wh)

	def disconnect(self) -> None:
		if self.gi is not None:
			self.gi.close()
			self.gi = None
		if self.link is not None:
			self.link.close()
			self.link = None

	def _require(self) -> PrairieLink:
		if self.link is None:
			raise RuntimeError("not connected to PrairieView script port")
		return self.link

	def send(self, *parts: str, timeout: float = 60.0) -> str:
		"""One script command; waits for PV's DONE (TimeoutError otherwise)."""
		link = self._require()
		with self.lock:
			old = link.timeout
			link.timeout = timeout
			try:
				return link.send_command(*parts)
			finally:
				link.timeout = old

	def query(self, *parts: str) -> str:
		link = self._require()
		with self.lock:
			return link.query(*parts)

	def grab(self, channel: int, w: int | None = None, h: int | None = None) -> GiResult:
		if self.gi is None:
			raise RuntimeError("not connected to PrairieView script port")
		hh, ww = self.wh
		with self.lock:
			res = self.gi.grab(int(channel), int(w or ww), int(h or hh))
		self.last_error = self.gi.last_error
		return res

	def get_frame(self, channel: int = 1) -> np.ndarray | None:
		return self.grab(channel).frame

	def start_tseries(self) -> dict:
		self.send("-TSeries")
		return {"ok": True, "cmd": "tseries"}

	def abort(self) -> dict:
		self.send("-Abort")
		return {"ok": True, "cmd": "abort"}

	def start_live(self) -> dict:
		self.send("-LiveScan")
		return {"ok": True, "cmd": "live"}

	def get_state(
		self, key: str, index: str | None = None, subindex: str | None = None
	) -> dict:
		if not key:
			raise ValueError("GetState requires a key")
		parts = [str(key)] + [str(v) for v in (index, subindex) if v is not None]
		out = {"ok": True, "cmd": "get_state", "key": key, "value": self.query("-gts", *parts)}
		if index is not None:
			out["index"] = index
		if subindex is not None:
			out["subindex"] = subindex
		return out

	def get_motor_position(self, axis: str, device: str | None = None) -> dict:
		ax = str(axis or "").upper()
		if ax not in _AXES:
			raise ValueError(f"axis must be one of {_AXES}, got {axis!r}")
		parts = [ax] + ([str(device)] if device else [])
		raw = self.query("-GetMotorPosition", *parts)
		val = _first_float(raw)
		if val is None:
			raise RuntimeError(f"GetMotorPosition {ax}: no number in reply {raw!r}")
		out = {"ok": True, "cmd": "get_motor_position", "axis": ax, "value": val}
		if device:
			out["device"] = device
		return out

	def load_mark_points(self, xml: str, path: str | None = None) -> dict:
		if not xml or not str(xml).strip():
			raise ValueError("load_mark_points requires xml content")
		disk = unique_mp_path(path)
		parent = os.path.dirname(disk)
		if parent:
			os.makedirs(parent, exist_ok=True)
		try:
			os.unlink(disk)
		except FileNotFoundError:
			pass
		with open(disk, "w", encoding="utf-8") as f:
			f.write(xml)
		raw = self.send("-LoadMarkPoints", disk)
		return {"ok": True, "cmd": "load_mark_points", "path": disk, "pv": raw}

	def mark_points(self) -> dict:
		raw = self.send("-MarkPoints")
		return {"ok": True, "cmd": "mark_points", "pv": raw}

	def mark_all_points(self, parts: list[str], *, wait_ms: float = 0.0) -> dict:
		"""parts from build_mark_all_points_parts (starts with -MarkAllPoints)."""
		if not parts:
			raise ValueError("mark_all_points requires non-empty parts list")
		if wait_ms and float(wait_ms) > 0:
			# PV -Wait requires an integer millisecond count (rejects 22.100).
			self.send("-Wait", str(int(round(float(wait_ms)))))
		raw = self.send(*[str(p) for p in parts])
		return {"ok": True, "cmd": "mark_all_points", "parts": list(parts), "pv": raw}
