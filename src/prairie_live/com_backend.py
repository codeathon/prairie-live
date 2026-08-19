"""PrairieLink COM backend (Windows).

The COM object is local; Connect(host, password) reaches PrairieView on this
PC or another Windows box on the LAN. GetImage_2 only exists here, not on
the TCP script port.
"""

from __future__ import annotations

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

	def connect(self) -> None:
		try:
			import pythoncom

			pythoncom.CoInitialize()
		except Exception:
			pass
		self._pl = _dispatch()
		self._pl.Connect(self.host, self.password)
		# Without this, -TSeries blocks GetImage_2 until the stack finishes.
		self.send("-DoNotWaitForScans")

	def disconnect(self) -> None:
		if self._pl is None:
			return
		try:
			self._pl.Disconnect()
		finally:
			self._pl = None

	def send(self, cmd: str) -> None:
		self._require().SendScriptCommands(cmd)

	def start_tseries(self) -> dict:
		self.send("-TSeries")
		return {"ok": True, "cmd": "tseries"}

	def abort(self) -> dict:
		self.send("-Abort")
		return {"ok": True, "cmd": "abort"}

	def start_live(self) -> dict:
		# Command name differs across PrairieView versions.
		for cmd in ("-LiveScan", "-Live"):
			try:
				self.send(cmd)
				return {"ok": True, "cmd": "live"}
			except Exception:
				continue
		return {"ok": False, "error": "no live-scan command"}

	def pixels_per_line(self) -> int:
		return int(self._require().PixelsPerLine())

	def lines_per_frame(self) -> int:
		return int(self._require().LinesPerFrame())

	def get_frame(self, channel: int = 1) -> np.ndarray | None:
		pl = self._require()
		w = int(pl.PixelsPerLine())
		h = int(pl.LinesPerFrame())
		if w <= 0 or h <= 0:
			return None
		try:
			raw = pl.GetImage_2(channel, w, h)
		except Exception:
			return None
		return _as_frame(raw, w, h)

	def _require(self):
		if self._pl is None:
			raise RuntimeError("not connected")
		return self._pl


def _as_frame(raw, w: int, h: int) -> np.ndarray | None:
	if raw is None:
		return None
	arr = np.array(raw)
	if arr.size == 0:
		return None
	arr = arr.astype(np.uint16, copy=False).reshape(-1)
	if arr.size < w * h:
		return None
	# PrairieView hands back row-major samples; MATLAB often transposes.
	return arr[: w * h].reshape(h, w)
