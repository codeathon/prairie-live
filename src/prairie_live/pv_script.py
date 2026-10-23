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

	def _drop_link_locked(self, why: str) -> None:
		"""Close the script socket while holding ``lock`` (no reconnect yet).

		Why: a stuck -MarkPoints / E:\\Markpoints dialog never returns DONE.
		Leaving the socket open holds ``lock`` until timeout, then the *next*
		-LoadMarkPoints/-MarkPoints still rides a wedged PV session — those
		\"leftover\" commands keep firing even after the operator restarts PV.
		Dropping the socket lets Abort land on a fresh login.
		"""
		print(f"dropping PV script session ({why})", flush=True)
		if self.gi is not None:
			try:
				self.gi.close()
			except Exception:
				pass
			self.gi = None
		if self.link is not None:
			try:
				self.link.close()
			except Exception:
				pass
			self.link = None

	def _reopen(self, parts: tuple, err: Exception) -> None:
		"""PV dropped the session: log which command was in flight, log in again."""
		self.reconnects += 1
		print(
			f"PV closed the script connection during {' '.join(parts)!r} "
			f"({type(err).__name__}: {err}); reconnect #{self.reconnects}",
			flush=True,
		)
		self._drop_link_locked("connection lost")
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
			except TimeoutError:
				# Must drop under the same lock — otherwise Abort queues behind
				# the still-open recv and leftover groups keep arming PV.
				self._drop_link_locked(f"timeout on {' '.join(parts)!r}")
				raise
			except (ConnectionError, OSError) as e:
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

	def recover_after_stuck_stim(self) -> None:
		"""Fresh login + Abort after a Mark Points timeout / dialog hang."""
		with self.lock:
			if self.link is None and self._started:
				try:
					self._open()
				except OSError as e:
					print(f"recover_after_stuck_stim: reconnect failed: {e}", flush=True)
					return
		try:
			self.ensure_idle_for_stim()
		except Exception as exc:
			print(f"recover_after_stuck_stim Abort/Live: {exc}", flush=True)
		try:
			self.disable_mark_points_metadata()
		except Exception as exc:
			print(f"recover_after_stuck_stim metadata: {exc}", flush=True)

	def send(self, *parts: str, timeout: float = 60.0) -> str:
		"""One script command; waits for PV's DONE (TimeoutError otherwise)."""
		def run():
			link = self.link
			if link is None:
				raise RuntimeError("not connected to PrairieView script port")
			old = link.timeout
			link.timeout = timeout
			try:
				return link.send_command(*parts)
			finally:
				# link may already be dropped on TimeoutError
				if self.link is link:
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
		# -Silent: Abort mid-Mark-Points otherwise pops E:\\Markpoints
		# "directory already exists — delete and continue?"
		self.send("-Silent", "-Abort")
		return {"ok": True, "cmd": "abort"}

	def start_live(self) -> dict:
		self.send("-LiveScan")
		return {"ok": True, "cmd": "live"}

	def disable_mark_points_metadata(self) -> dict:
		"""Turn off ad-hoc Mark Points disk saves (persists in PV until changed)."""
		raw = self.send("-MarkPointsMetadata", "False")
		return {"ok": True, "cmd": "mark_points_metadata", "enabled": False, "pv": raw}

	def ensure_idle_for_stim(self) -> None:
		"""Last-resort clear when the next arm fails (do not call every trial).

		Aborting after every -NoWait trial finalizes PV's Mark Points save into
		E:\\Markpoints\\… and shows a delete-folder dialog. Prefer waiting for
		the prior stim to finish; only Abort from Stop or a single retry.
		"""
		try:
			self.send("-Silent", "-Abort")
		except Exception as exc:
			print(f"ensure_idle_for_stim -Abort: {exc}")
		try:
			self.send("-LiveScan")
		except Exception as exc:
			print(f"ensure_idle_for_stim -LiveScan: {exc}")

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
		# -Silent: PV otherwise pops E:\Markpoints "already exists / file
		# already present" and blocks the script port (stuck on first group).
		try:
			self.disable_mark_points_metadata()
		except Exception:
			pass
		try:
			raw = self.send("-Silent", "-LoadMarkPoints", disk)
		except TimeoutError as exc:
			# Dialog / wedged PV — do not queue another -lmp on the same session.
			self.recover_after_stuck_stim()
			raise RuntimeError(
				"LoadMarkPoints timed out (often E:\\Markpoints "
				"'File already present'). Session dropped + Abort; "
				"stop the Run before retrying."
			) from exc
		if _pv_file_dialog(raw) or _pv_task_busy(raw):
			# One Abort + retry for a transient busy; never leave a second
			# -lmp queued if the dialog is still up (that is the leftover hang).
			self.ensure_idle_for_stim()
			try:
				self.disable_mark_points_metadata()
			except Exception:
				pass
			disk = unique_mp_path(None)  # new path — avoid replace dialog
			parent = os.path.dirname(disk)
			if parent:
				os.makedirs(parent, exist_ok=True)
			with open(disk, "w", encoding="utf-8") as f:
				f.write(xml)
			raw = self.send("-Silent", "-LoadMarkPoints", disk)
			if _pv_file_dialog(raw) or _pv_task_busy(raw):
				self.recover_after_stuck_stim()
				raise RuntimeError(
					f"LoadMarkPoints still blocked after Abort: {raw[:240]!r}. "
					"Dismiss any Markpoints dialog, then Stop and re-Run."
				)
		return {"ok": True, "cmd": "load_mark_points", "path": disk, "pv": raw}

	def mark_points(self, *, no_wait: bool = False, timeout: float = 180.0) -> dict:
		"""Run current Mark Points series.

		Default blocks until DONE. For Trig In 1, the caller must pulse DTR
		*while* this waits (see ``stim_fire.run_while_pulsing_dtr``). Do not use
		-NoWait for photostim — DONE returns before PV is armed for the trigger,
		so later DTR never burns.

		On timeout / file-dialog reply: drop the script session, Abort, and
		raise — do **not** fire a second -MarkPoints (that left leftover arms
		running after a PV restart).
		"""
		try:
			if no_wait:
				raw = self.send("-Silent", "-NoWait", "-MarkPoints", timeout=timeout)
			else:
				raw = self.send("-Silent", "-MarkPoints", timeout=timeout)
		except TimeoutError as exc:
			self.recover_after_stuck_stim()
			raise RuntimeError(
				"MarkPoints timed out (Trig In 1 miss or E:\\Markpoints dialog). "
				"Script session dropped + Abort; stop the Run — do not keep "
				"sending groups after restarting PV."
			) from exc
		if _pv_task_busy(raw) or _pv_file_dialog(raw):
			self.recover_after_stuck_stim()
			raise RuntimeError(
				f"MarkPoints blocked ({raw[:240]!r}). Cleared with Abort; "
				"stop the Run and dismiss any Markpoints folder dialog."
			)
		return {"ok": True, "cmd": "mark_points", "pv": raw, "no_wait": bool(no_wait)}

	def mark_all_points(
		self,
		parts: list[str],
		*,
		wait_ms: float = 0.0,
		no_wait: bool | None = None,
		timeout: float = 180.0,
	) -> dict:
		"""parts from build_mark_all_points_parts (starts with -MarkAllPoints).

		Default ``no_wait=False``: block until DONE. Pulse DTR concurrently when
		using Trig In 1 (``stim_fire.run_while_pulsing_dtr``).
		"""
		if not parts:
			raise ValueError("mark_all_points requires non-empty parts list")
		tokens = [str(p) for p in parts]
		if no_wait is None:
			no_wait = False
		if wait_ms and float(wait_ms) > 0:
			# PV -Wait requires an integer millisecond count (rejects 22.100).
			self.send("-Wait", str(int(round(float(wait_ms)))))
		send_toks = list(tokens)
		if no_wait and send_toks[0] not in ("-NoWait", "-nw"):
			send_toks = ["-NoWait", *send_toks]
		if send_toks[0] not in ("-Silent", "-s"):
			send_toks = ["-Silent", *send_toks]
		try:
			raw = self.send(*send_toks, timeout=timeout)
		except TimeoutError as exc:
			self.recover_after_stuck_stim()
			raise RuntimeError(
				"MarkAllPoints timed out; session dropped + Abort. Stop the Run."
			) from exc
		if _pv_task_busy(raw) or _pv_file_dialog(raw):
			self.recover_after_stuck_stim()
			raise RuntimeError(
				f"MarkAllPoints blocked ({raw[:240]!r}); Abort sent — stop the Run."
			)
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


def _pv_task_busy(raw: str) -> bool:
	"""PV replied that Mark Points / another acquisition is still running."""
	t = str(raw or "").lower()
	return "already in progress" in t or "another task" in t


def _pv_file_dialog(raw: str) -> bool:
	"""PV is blocked on an E:\\Markpoints replace / already-present dialog."""
	t = str(raw or "").lower()
	return (
		"already present" in t
		or "already exists" in t
		or "directory already" in t
		or "file already" in t
		or "delete and continue" in t
	)
