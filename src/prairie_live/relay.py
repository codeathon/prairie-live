"""Optional relay when the viewer PC does not have PrairieLink installed.

Run on the scope PC. Frames on --port, JSON commands on --port+1.
Ctrl+C (or Ctrl+Break on Windows) stops cleanly by closing listen sockets
so accept() cannot hang forever.
"""

from __future__ import annotations

import argparse
import json
import os
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


class Relay:
	def __init__(self, pv_host: str, password: str, channel: int):
		self.channel = channel
		self.password = password
		self.pv_host = pv_host
		self.com = PrairieCom(pv_host, password)
		# Separate COM session for scripts so GetImage never blocks -lmp/-mp.
		self.script_com: PrairieCom | None = None
		self._latest = None
		self._lock = threading.Lock()
		self._stop = threading.Event()
		self._pause_grab = threading.Event()
		self._script_lock = threading.Lock()

	def start(self) -> None:
		self.com.connect()
		self.script_com = PrairieCom(self.pv_host, self.password)
		self.script_com.connect()
		threading.Thread(target=self._grab_loop, daemon=True).start()

	def stop(self) -> None:
		self._stop.set()
		self._pause_grab.set()
		for c in (self.com, self.script_com):
			if c is None:
				continue
			try:
				c.disconnect()
			except Exception:
				pass

	def _grab_loop(self) -> None:
		# win32com must be CoInitialized on every thread that calls COM.
		try:
			import pythoncom

			pythoncom.CoInitialize()
		except Exception:
			pass
		while not self._stop.is_set():
			if self._pause_grab.is_set():
				time.sleep(0.01)
				continue
			frame = self.com.get_frame(self.channel)
			if frame is not None:
				with self._lock:
					self._latest = frame
			else:
				time.sleep(0.01)

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
		"""Run a script COM call with a wall-clock timeout."""
		box: list = []
		err: list[BaseException] = []

		def _worker() -> None:
			try:
				import pythoncom

				pythoncom.CoInitialize()
			except Exception:
				pass
			try:
				box.append(fn())
			except BaseException as e:
				err.append(e)

		t = threading.Thread(target=_worker, daemon=True)
		t.start()
		t.join(timeout)
		if t.is_alive():
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
	args = p.parse_args(argv)

	relay = Relay(args.pv_host, args.password, args.channel)
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
