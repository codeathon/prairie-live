"""PrairieLink COM backend (Windows).

The COM object is local; Connect(host, password) reaches PrairieView on this
PC or another Windows box on the LAN. GetImage_2 only exists here, not on
the TCP script port.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time

import numpy as np


def _dispatch():
	import win32com.client

	for progid in ("PrairieLink64.Application", "PrairieLink.Application"):
		try:
			return win32com.client.Dispatch(progid)
		except Exception:
			continue
	raise RuntimeError(
		"PrairieLink COM not registered. Install PrairieView/PrairieLink "
		"on this Windows PC, or run: python -m prairie_live relay"
	)


class PrairieCom:
	def __init__(self, host: str, password: str = "0000"):
		self.host = host
		self.password = password
		self._pl = None
		self.last_error = ""
		self._last_wh = None
		self._last_reconnect = 0.0
		# Grab thread and command thread share one COM object.
		self._io = threading.Lock()

	def connect(self) -> None:
		try:
			import pythoncom

			pythoncom.CoInitialize()
		except Exception:
			pass
		self._pl = _dispatch()
		ok = self._pl.Connect(self.host, self.password)
		try:
			connected = bool(self._pl.Connected())
		except Exception:
			connected = bool(ok)
		if not connected:
			raise RuntimeError(
				f"PrairieLink Connect({self.host!r}) failed. "
				"Use the scope IPv4 from the analysis PC, or 127.0.0.1 on the scope PC."
			)
		# PV 5.8 aborts the TCP session on this command; only try it for T-series.

	def start_tseries(self) -> dict:
		# Older PV needed this so -TSeries would not block the COM client.
		try:
			self.send("-DoNotWaitForScans")
		except Exception:
			pass
		self.send("-TSeries")
		return {"ok": True, "cmd": "tseries"}

	def disconnect(self) -> None:
		with self._io:
			if self._pl is None:
				return
			try:
				self._pl.Disconnect()
			finally:
				self._pl = None

	def send(self, cmd: str) -> None:
		with self._io:
			self._require().SendScriptCommands(cmd)

	def get_state(
		self, key: str, index: str | None = None, subindex: str | None = None
	) -> dict:
		# COM GetState returns the value; SendScriptCommands("-gts") does not.
		if not key:
			raise ValueError("GetState requires a key")
		with self._io:
			pl = self._require()
			if index is None:
				raw = pl.GetState(key)
			elif subindex is None:
				raw = pl.GetState(key, index)
			else:
				raw = pl.GetState(key, index, subindex)
		out = {"ok": True, "cmd": "get_state", "key": key, "value": str(raw)}
		if index is not None:
			out["index"] = index
		if subindex is not None:
			out["subindex"] = subindex
		return out

	def get_motor_position(self, axis: str, device: str | None = None) -> dict:
		ax = axis.upper()
		if ax not in ("X", "Y", "Z"):
			raise ValueError(f"axis must be X, Y, or Z, not {axis!r}")
		with self._io:
			pl = self._require()
			raw = pl.GetMotorPosition(ax, device) if device else pl.GetMotorPosition(ax)
		out = {
			"ok": True,
			"cmd": "get_motor_position",
			"axis": ax,
			"value": float(raw),
		}
		if device:
			out["device"] = device
		return out

	def abort(self) -> dict:
		self.send("-Abort")
		return {"ok": True, "cmd": "abort"}

	def load_mark_points_xml(self, xml: str, path: str | None = None) -> dict:
		"""
		Write series XML on this PC and -LoadMarkPoints it.

		Used by the relay so the analysis PC can push XML without SMB shares.
		COM SendScriptCommands uses spaces (TCP port 1236 uses SOH).

		Always uses a fresh path (or overwrites path after unlink) so PrairieView
		does not pop "file exists / replace?" dialogs.
		"""
		if not xml or not str(xml).strip():
			raise ValueError("load_mark_points_xml requires xml content")
		path = _unique_mp_path(path)
		parent = os.path.dirname(path)
		if parent:
			os.makedirs(parent, exist_ok=True)
		# Unlink first so Windows/PV never see a same-name collision dialog.
		try:
			os.unlink(path)
		except FileNotFoundError:
			pass
		with open(path, "w", encoding="utf-8") as f:
			f.write(xml)
		# Quote path so spaces in Temp dirs do not break the command line.
		self.send(f'-LoadMarkPoints "{path}"')
		return {"ok": True, "cmd": "load_mark_points", "path": path}

	def mark_points(self) -> dict:
		"""Run the current Mark Point series (-MarkPoints)."""
		self.send("-MarkPoints")
		return {"ok": True, "cmd": "mark_points"}

	def start_live(self) -> dict:
		# Official script help: only -LiveScan (-lv); -Live is not a command.
		self.send("-LiveScan")
		return {"ok": True, "cmd": "live"}

	def pixels_per_line(self) -> int:
		return int(self._require().PixelsPerLine())

	def lines_per_frame(self) -> int:
		return int(self._require().LinesPerFrame())

	def get_frame(self, channel: int = 1) -> np.ndarray | None:
		# Do not call PixelsPerLine first: PV 5.8 remote aborts that COM method.
		pl = self._pl
		if pl is None:
			return None
		try:
			with self._io:
				raw = self._grab_raw(pl, channel)
		except Exception as e:
			self.last_error = str(e)
			self._reconnect_if_dropped(e)
			return None
		frame = _as_frame_auto(raw, self._last_wh)
		if frame is not None:
			self._last_wh = frame.shape
			self.last_error = ""
		return frame

	def _grab_raw(self, pl, channel: int):
		try:
			return pl.GetImage(channel)
		except Exception:
			# GetImage_2 wants (channel, pixelsPerLine, linesPerFrame).
			h, w = self._last_wh if self._last_wh else (512, 512)
			return pl.GetImage_2(channel, w, h)

	def _reconnect_if_dropped(self, err: Exception) -> None:
		msg = str(err).lower()
		if "abort" not in msg and "transport" not in msg:
			return
		now = time.monotonic()
		if now - self._last_reconnect < 2.0:
			return
		self._last_reconnect = now
		try:
			self.disconnect()
		except Exception:
			pass
		try:
			self.connect()
		except Exception:
			pass

	def _require(self):
		if self._pl is None:
			raise RuntimeError("not connected")
		return self._pl


def _unique_mp_path(path: str | None) -> str:
	"""Prefer a new tempfile each load; fixed path → still rewrite after unlink."""
	if path:
		return path
	fd, p = tempfile.mkstemp(prefix="prairie_live_mp_", suffix=".xml")
	os.close(fd)
	return p


def _as_frame_auto(raw, last_wh) -> np.ndarray | None:
	if raw is None:
		return None
	arr = np.array(raw)
	if arr.size == 0:
		return None
	arr = arr.astype(np.uint16, copy=False).reshape(-1)
	n = int(arr.size)
	if last_wh is not None:
		h, w = last_wh
		if w * h == n:
			return arr.reshape(h, w)
	side = int(round(n ** 0.5))
	if side * side == n:
		return arr.reshape(side, side)
	return None


def _as_frame(raw, w: int, h: int) -> np.ndarray | None:
	return _as_frame_auto(raw, (h, w))
