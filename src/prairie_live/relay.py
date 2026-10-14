"""Optional relay when the viewer PC does not have PrairieLink installed.

Run on the scope PC. Frames on --port, JSON commands on --port+1.
Ctrl+C (or Ctrl+Break on Windows) stops cleanly by closing listen sockets
so accept() cannot hang forever.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import socket
import sys
import tempfile
import threading
import time

from prairie_live.com_backend import PrairieCom
from prairie_live.prairie_link import SOH, PrairieLink
from prairie_live.protocol import pack_frame
from prairie_live.tcp_backend import DEFAULT_PORT as PV_SCRIPT_PORT

DEFAULT_PORT = 25100
# After pausing grab, wait for an in-flight GetImage to release the COM lock.
_GRAB_DRAIN_S = 0.35
# COM script calls must not block the relay ctrl socket forever.
_COM_SCRIPT_TIMEOUT_S = 45.0


def grab_heartbeat_line(frame, last_error: str, channel: int | None = None) -> str:
	"""Scope-console line so a dark analysis FOV can be diagnosed without the plot."""
	ch = f" ch={int(channel)}" if channel is not None else ""
	if frame is None:
		return f"grab heartbeat:{ch} NO FRAME  {last_error or 'no COM frame'}"
	h, w = frame.shape[:2]
	return (
		f"grab heartbeat:{ch} {int(w)}x{int(h)} "
		f"min={int(frame.min())} max={int(frame.max())} "
		f"mean={float(frame.mean()):.1f}"
	)


class Relay:
	def __init__(
		self,
		pv_host: str,
		password: str,
		channel: int,
		*,
		image_wh=(512, 512),
		grab: bool = False,
	):
		self.channel = channel
		self.password = password
		self.pv_host = pv_host
		self.com = PrairieCom(pv_host, password)
		# (height, width) — matches numpy frame.shape / GetImage_2 args.
		h, w = image_wh
		self.com._last_wh = (int(h), int(w))
		# Same COM as grab — a second Connect races password vs -gi on port 1236
		# ("unexpected parameter 0000-gi"). Scripts pause grab via _pause_grab.
		self.script_com: PrairieCom | None = None
		# Default off: GetImage_2 maps to script -gi with process/buffer addresses
		# and PV then reports "Unexpected parameter <huge number>".
		self._grab_enabled = bool(grab)
		self._latest = None
		self._lock = threading.Lock()
		self._stop = threading.Event()
		self._pause_grab = threading.Event()
		self._script_lock = threading.Lock()
		# One-shot so the console shows whether grab is zeros vs a real FOV.
		self._logged_first_grab = False
		self._logged_gi2 = False
		self._hb_t = 0.0
		# Jobs run on the grab thread — win32com STA cannot GetImage from another thread.
		self._com_jobs: queue.Queue = queue.Queue()
		self._com_ready = threading.Event()

	def start(self) -> None:
		if self._grab_enabled:
			# Why: Connect+GetImage must share one thread. Main-thread Connect then
			# grab-thread GetImage returned None (analysis: "no frame from relay").
			threading.Thread(
				target=self._grab_loop, daemon=True, name="pv-grab"
			).start()
			if not self._com_ready.wait(30.0):
				raise RuntimeError("grab thread did not Connect in 30s")
			if self.script_com is None:
				raise RuntimeError(
					"grab Connect failed. PrairieView must be open on this PC."
				)
		else:
			self.com.connect()
			self.script_com = self.com
			print("grab OFF (default); pass --grab only when you need live frames")

	def _probe_and_pick_channel(self) -> None:
		# Why: Live can be on PMT2/3 or 256/1024² while we asked GetImage_2 for 512 ch1.
		try:
			probe = self.com.probe_live_grab(self.channel)
		except Exception as e:
			print(f"grab probe failed: {e}")
			return
		print(f"grab probe FOV {int(probe['wh'][1])}x{int(probe['wh'][0])}")
		for row in probe["rows"]:
			print(f"  {row}")
		if int(probe["channel"]) != int(self.channel):
			print(
				f"switching grab channel {self.channel} -> {probe['channel']} "
				"(Live counts were on a different PMT)"
			)
			self.channel = int(probe["channel"])
		scores = probe.get("scores") or {}
		if max(scores.values(), default=-1.0) <= 0:
			print(
				"all channels max=0 after Live — PrairieLink is not the PV window. "
				"Take one Snapshot in PV, leave Live running, watch max=."
			)

	def _arm_live_grab(self) -> None:
		# Why: the PV Live Scan *button* refreshes the PV window; PrairieLink's
		# GetImage/ReadImage buffer often stays on the last T-series until
		# -LiveScan is sent on this COM connection. Markpoints are not required.
		try:
			self.com.start_live()
			print("sent -LiveScan; FOV grab should move with no markpoints")
		except Exception as e:
			print(f"-LiveScan failed ({e}); press l in view or click Live in PV")

	def stop(self) -> None:
		self._stop.set()
		self._pause_grab.set()
		if not self._grab_enabled and self.com is not None:
			try:
				self.com.disconnect()
			except Exception:
				pass
			self.script_com = None

	def _init_grab_com(self) -> None:
		try:
			import pythoncom

			pythoncom.CoInitialize()
		except Exception:
			pass
		self.com.connect()
		self.script_com = self.com
		self._arm_live_grab()
		self._probe_and_pick_channel()
		kind = getattr(self.com, "grab_api", None) or "GetImage_2"
		print(f"grab ON ({kind}) COM-thread; use without --grab if PV shows -gi errors")
		to = getattr(self.com, "timeout_api", None)
		if to:
			print(f"COM timeout: {to}")

	def _grab_loop(self) -> None:
		try:
			self._init_grab_com()
		except Exception as e:
			print(f"grab Connect failed: {e}")
			self.script_com = None
		finally:
			self._com_ready.set()
		if self.script_com is None:
			return
		try:
			while not self._stop.is_set():
				self._drain_com_jobs()
				if self._pause_grab.is_set():
					time.sleep(0.01)
					continue
				self._grab_one()
		finally:
			try:
				self.com.disconnect()
			except Exception:
				pass
			self.script_com = None

	def _drain_com_jobs(self) -> None:
		while True:
			try:
				fn, box, err, done = self._com_jobs.get_nowait()
			except queue.Empty:
				return
			try:
				box.append(fn())
			except BaseException as e:
				err.append(e)
			done.set()

	def _grab_one(self) -> None:
		frame = self.com.get_frame(self.channel)
		if frame is not None:
			with self._lock:
				self._latest = frame
			if not self._logged_first_grab:
				# Why: analysis "dark screen" was often grab max=0, not the plot.
				self._logged_first_grab = True
				print(
					f"first grab {int(frame.shape[1])}x{int(frame.shape[0])} "
					f"min={int(frame.min())} max={int(frame.max())} "
					f"mean={float(frame.mean()):.1f}"
				)
			failed = getattr(self.com, "_grab_failed", ())
			if (not self._logged_gi2) and "ReadImage" in failed:
				self._logged_gi2 = True
				print("ReadImage frozen during Live; grabbing via GetImage_2")
		else:
			time.sleep(0.01)
		now = time.monotonic()
		if now - self._hb_t >= 2.0:
			self._hb_t = now
			err = getattr(self.com, "last_error", "") or ""
			print(grab_heartbeat_line(frame, err, self.channel), flush=True)

	def latest_frame(self):
		with self._lock:
			return self._latest

	def handle_command(self, payload: dict) -> dict:
		cmd = payload.get("cmd")
		self._pause_grab.set()
		time.sleep(_GRAB_DRAIN_S)
		try:
			return self._dispatch(cmd, payload)
		finally:
			if not self._stop.is_set():
				self._pause_grab.clear()

	def _script(self) -> PrairieCom:
		if self.script_com is None:
			raise RuntimeError("relay script COM not connected")
		return self.script_com

	def _run_script_com(self, fn, *, timeout: float = _COM_SCRIPT_TIMEOUT_S):
		"""Run a script COM call on the grab thread (same STA as GetImage)."""
		if not self._grab_enabled:
			# No grab thread — tests and --no-grab scripts call COM inline.
			return fn()
		done = threading.Event()
		box: list = []
		err: list[BaseException] = []
		self._com_jobs.put((fn, box, err, done))
		if not done.wait(timeout):
			raise TimeoutError(f"COM script timed out after {timeout:.0f}s")
		if err:
			raise err[0]
		return box[0]

	def _dispatch(self, cmd, payload: dict) -> dict:
		try:
			sc = self._script()
			if cmd == "tseries":
				self._run_script_com(sc.start_tseries)
			elif cmd == "abort":
				self._run_script_com(sc.abort)
			elif cmd == "live":
				self._run_script_com(sc.start_live)
			elif cmd == "ping":
				pass
			elif cmd == "get_state":
				return self._run_script_com(
					lambda: sc.get_state(
						payload.get("key", ""),
						payload.get("index"),
						payload.get("subindex"),
					)
				)
			elif cmd == "get_motor_position":
				return self._run_script_com(
					lambda: sc.get_motor_position(
						payload.get("axis", ""),
						payload.get("device"),
					)
				)
			elif cmd == "load_mark_points":
				return self._load_mark_points(
					payload.get("xml", ""),
					payload.get("path"),
				)
			elif cmd == "mark_points":
				self._run_script_com(sc.mark_points)
				return {"ok": True, "cmd": "mark_points"}
			elif cmd == "mark_all_points":
				parts = payload.get("parts") or []
				if not isinstance(parts, list) or not parts:
					raise ValueError("mark_all_points requires non-empty parts list")
				wait_ms = float(payload.get("wait_ms") or 0.0)
				return self._run_script_com(
					lambda: sc.mark_all_points(parts, wait_ms=wait_ms)
				)
			else:
				return {"ok": False, "error": f"unknown cmd {cmd}"}
			return {"ok": True, "cmd": cmd}
		except Exception as e:
			return {"ok": False, "cmd": cmd, "error": str(e)}

	def _load_mark_points(self, xml: str, path: str | None) -> dict:
		if not xml or not str(xml).strip():
			raise ValueError("load_mark_points requires xml content")
		sc = self._script()
		# Unique path by default — avoids PV "replace existing file?" dialogs.
		print(f"load_mark_points xml_bytes={len(xml)} path={path!r}")
		try:
			out = self._run_script_com(
				lambda: sc.load_mark_points_xml(xml, path),
				timeout=_COM_SCRIPT_TIMEOUT_S,
			)
			print(f"load_mark_points COM OK → {out.get('path')}")
			return out
		except Exception as com_err:
			print(f"load_mark_points COM failed ({com_err}); trying TCP :1236 …")
		from prairie_live.com_backend import _unique_mp_path

		disk = _unique_mp_path(path)
		parent = os.path.dirname(disk)
		if parent:
			os.makedirs(parent, exist_ok=True)
		try:
			os.unlink(disk)
		except FileNotFoundError:
			pass
		with open(disk, "w", encoding="utf-8") as f:
			f.write(xml)
		raw = self._script_command("-LoadMarkPoints", disk)
		print(f"load_mark_points TCP DONE path={disk} {raw[:200]!r}")
		return {"ok": True, "cmd": "load_mark_points", "path": disk, "pv": raw}

	def _script_command(self, *parts: str) -> str:
		"""Mark Points cmds via PrairieLink TCP when COM is unavailable."""
		cmd = SOH.join(parts)
		print(f"script TCP send: {cmd[:120]!r}")
		with self._script_lock:
			pl = PrairieLink(
				host=self.pv_host,
				port=PV_SCRIPT_PORT,
				password=self.password,
				timeout=60.0,
			)
			try:
				return pl.send_command(*parts)
			finally:
				pl.close()


def _serve_frames(conn: socket.socket, relay: Relay, fps: float) -> None:
	period = 1.0 / max(fps, 1.0)
	try:
		while not relay._stop.is_set():
			frame = relay.latest_frame()
			if frame is not None:
				conn.sendall(pack_frame(frame, extra=relay.channel))
			time.sleep(period)
	except (ConnectionError, OSError):
		pass
	finally:
		conn.close()


def _serve_ctrl(conn: socket.socket, relay: Relay) -> None:
	# This thread calls PrairieLink COM; it needs its own apartment.
	try:
		import pythoncom

		pythoncom.CoInitialize()
	except Exception:
		pass
	peer = "?"
	try:
		peer = f"{conn.getpeername()[0]}:{conn.getpeername()[1]}"
	except OSError:
		pass
	print(f"ctrl client connected {peer}")
	f = conn.makefile("rwb")
	try:
		for line in f:
			if relay._stop.is_set():
				break
			raw = line.decode("utf-8", errors="replace")
			try:
				payload = json.loads(raw)
			except json.JSONDecodeError as e:
				print(f"ctrl recv from {peer} (bad JSON): {raw[:500]!r} err={e}")
				reply = {"ok": False, "error": f"bad json: {e}"}
				f.write((json.dumps(reply) + "\n").encode("utf-8"))
				f.flush()
				continue
			print(f"ctrl recv from {peer}: {_fmt_payload(payload)}")
			reply = relay.handle_command(payload)
			print(f"ctrl reply to {peer}: {_fmt_reply(reply)}")
			f.write((json.dumps(reply) + "\n").encode("utf-8"))
			f.flush()
	except (ConnectionError, OSError, ValueError) as e:
		print(f"ctrl client {peer} closed: {e}")
	finally:
		conn.close()
		print(f"ctrl client disconnected {peer}")


def _fmt_payload(payload: dict) -> str:
	"""Loggable summary; truncate Mark Points XML so the console stays readable."""
	cmd = payload.get("cmd")
	if cmd == "load_mark_points":
		xml = payload.get("xml") or ""
		path = payload.get("path")
		preview = xml[:240].replace("\n", " ")
		more = "…" if len(xml) > 240 else ""
		return (
			f"cmd=load_mark_points path={path!r} xml_bytes={len(xml)} "
			f"xml_preview={preview!r}{more}"
		)
	# Small commands: show the full dict.
	try:
		s = json.dumps(payload, sort_keys=True)
	except TypeError:
		s = str(payload)
	if len(s) > 500:
		return s[:500] + "…"
	return s


def _fmt_reply(reply: dict) -> str:
	try:
		s = json.dumps(reply, sort_keys=True)
	except TypeError:
		s = str(reply)
	# PV ACK/DONE blobs can be noisy; keep a short cap.
	if len(s) > 400:
		return s[:400] + "…"
	return s


def _accept_loop(sock: socket.socket, stop: threading.Event, handler, *args) -> None:
	# Timed accept so Ctrl+C / stop can be noticed on Windows.
	sock.settimeout(1.0)
	while not stop.is_set():
		try:
			conn, addr = sock.accept()
		except TimeoutError:
			continue
		except OSError:
			break
		print(f"{handler.__name__} {addr[0]}:{addr[1]}")
		threading.Thread(target=handler, args=(conn,) + args, daemon=True).start()


def listen(relay: Relay, bind: str, port: int, fps: float) -> None:
	frame_srv = _bind(bind, port)
	ctrl_srv = _bind(bind, port + 1)
	servers = (frame_srv, ctrl_srv)

	def _shutdown(*_args) -> None:
		# First Ctrl+C: close listen socks (unblocks accept) and stop COM grab.
		if relay._stop.is_set():
			print("force exit")
			sys.exit(1)
		print("\nstopping (Ctrl+C) …")
		relay._stop.set()
		relay._pause_grab.set()
		for s in servers:
			try:
				s.close()
			except OSError:
				pass

	signal.signal(signal.SIGINT, _shutdown)
	# Windows console Ctrl+Break
	if hasattr(signal, "SIGBREAK"):
		signal.signal(signal.SIGBREAK, _shutdown)

	print(f"frames {bind}:{port}  ctrl {bind}:{port + 1}  PV={relay.com.host}")
	print("Ctrl+C to stop")
	threading.Thread(
		target=_accept_loop,
		args=(frame_srv, relay._stop, _serve_frames, relay, fps),
		daemon=True,
	).start()
	try:
		_accept_loop(ctrl_srv, relay._stop, _serve_ctrl, relay)
	finally:
		for s in servers:
			try:
				s.close()
			except OSError:
				pass


def _bind(bind: str, port: int) -> socket.socket:
	srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
	srv.bind((bind, port))
	srv.listen(4)
	return srv


def main(argv=None) -> None:
	p = argparse.ArgumentParser(description="PrairieLink image relay")
	p.add_argument("--pv-host", default="127.0.0.1", help="PrairieView IP")
	p.add_argument("--password", default="0000")
	p.add_argument("--bind", default="0.0.0.0")
	p.add_argument("--port", type=int, default=DEFAULT_PORT)
	p.add_argument("--channel", type=int, default=1)
	p.add_argument("--fps", type=float, default=20.0)
	# Avoid COM PixelsPerLine(); seed FOV size for GetImage_2 fallback.
	p.add_argument("--width", type=int, default=512, help="pixelsPerLine for grab fallback")
	p.add_argument("--height", type=int, default=512, help="linesPerFrame for grab fallback")
	p.add_argument(
		"--grab",
		action="store_true",
		help="Enable frame streaming (off by default; can trip PV -gi errors)",
	)
	p.add_argument(
		"--no-grab",
		action="store_true",
		help=argparse.SUPPRESS,  # backward alias; grab is already off by default
	)
	args = p.parse_args(argv)

	do_grab = bool(args.grab) and not bool(args.no_grab)
	relay = Relay(
		args.pv_host,
		args.password,
		args.channel,
		image_wh=(args.height, args.width),
		grab=do_grab,
	)
	relay.start()
	try:
		listen(relay, args.bind, args.port, args.fps)
	except KeyboardInterrupt:
		print("\nstopping (KeyboardInterrupt) …")
	finally:
		relay.stop()
		print("relay stopped")


if __name__ == "__main__":
	main()
