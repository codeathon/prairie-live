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
import time

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
		self.reconnects = 0
		self._opened_at = 0.0
		self._started = False

	def connect(self) -> None:
		self._open()
		self._started = True
		self.wh = self.gi.fov_wh(self.wh)

	def _open(self) -> None:
		self._opened_at = time.monotonic()
		self.link = PrairieLink(
			host=self.host, port=self.port, password=self.password, timeout=10.0
		)
		self.gi = GiGrabber(self.link)

	def _reopen(self, parts: tuple, err: Exception) -> None:
		"""PV dropped the session: log which command was in flight, log in again."""
		self.reconnects += 1
		print(
			f"PV closed the script connection during {' '.join(parts)!r} "
			f"({type(err).__name__}: {err}); reconnect #{self.reconnects}",
			flush=True,
		)
		if self.gi is not None:
			self.gi.close()
		self.link = self.gi = None
		wait = 1.0 - (time.monotonic() - self._opened_at)
		if wait > 0:
			time.sleep(wait)
		try:
			self._open()
		except OSError as e:
			print(f"reconnect to PV failed: {e}", flush=True)

	def _call(self, parts: tuple, fn):
		"""Run fn under the lock; on a dropped socket reconnect but never resend."""
		with self.lock:
			if self.link is None:
				if not self._started:
					raise RuntimeError("not connected to PrairieView script port")
				if time.monotonic() - self._opened_at < 1.0:
					raise RuntimeError("reconnecting to PrairieView")
				self._open()
			try:
				return fn()
			except (ConnectionError, OSError) as e:
				if isinstance(e, TimeoutError):
					raise
				self._reopen(parts, e)
				raise RuntimeError(
					f"PrairieView dropped the connection during {parts[0]} ({e}); "
					"reconnected, command not resent"
				) from e

	def disconnect(self) -> None:
		if self.gi is not None:
			self.gi.close()
			self.gi = None
		if self.link is not None:
			self.link.close()
			self.link = None

	def send(self, *parts: str, timeout: float = 60.0) -> str:
		"""One script command; waits for PV's DONE (TimeoutError otherwise)."""
		def run():
			link = self.link
			old = link.timeout
			link.timeout = timeout
			try:
				return link.send_command(*parts)
			finally:
				link.timeout = old

		return self._call(parts, run)

	def query(self, *parts: str) -> str:
		return self._call(parts, lambda: self.link.query(*parts))

	def grab(self, channel: int, w: int | None = None, h: int | None = None) -> GiResult:
		hh, ww = self.wh
		res = self._call(
			("-gi", str(channel)),
			lambda: self.gi.grab(int(channel), int(w or ww), int(h or hh)),
		)
		self.last_error = res.summary() if res.frame is None else ""
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

	def mark_points(self, *, no_wait: bool = False) -> dict:
		# With Trig In 1, -MarkPoints blocks DONE until the trigger; we pulse
		# DTR only after this returns — -NoWait breaks that deadlock.
		if no_wait:
			raw = self.send("-NoWait", "-MarkPoints")
		else:
			raw = self.send("-MarkPoints")
		return {"ok": True, "cmd": "mark_points", "pv": raw, "no_wait": bool(no_wait)}

	def mark_all_points(
		self,
		parts: list[str],
		*,
		wait_ms: float = 0.0,
		no_wait: bool | None = None,
	) -> dict:
		"""parts from build_mark_all_points_parts (starts with -MarkAllPoints).

		When the hologram waits on a countable trigger (e.g. Trig In 1), PV does
		not send DONE until that edge — but our DTR pulse runs *after* this call.
		Default: prepend -NoWait whenever a trigger name is in ``parts``.
		"""
		if not parts:
			raise ValueError("mark_all_points requires non-empty parts list")
		tokens = [str(p) for p in parts]
		if no_wait is None:
			no_wait = _script_parts_wait_on_trigger(tokens)
		if wait_ms and float(wait_ms) > 0:
			# PV -Wait requires an integer millisecond count (rejects 22.100).
			self.send("-Wait", str(int(round(float(wait_ms)))))
		if no_wait and tokens[0] not in ("-NoWait", "-nw"):
			tokens = ["-NoWait", *tokens]
		raw = self.send(*tokens)
		return {
			"ok": True,
			"cmd": "mark_all_points",
			"parts": list(parts),
			"pv": raw,
			"no_wait": bool(no_wait),
		}


def _script_parts_wait_on_trigger(parts: list[str]) -> bool:
	"""True when -slm / -mp argv includes a countable trigger source name."""
	for p in parts:
		s = str(p).strip().lower()
		if s in ("none", "", "false", "true"):
			continue
		if s.startswith("trig") or s.startswith("pfi"):
			return True
	return False
