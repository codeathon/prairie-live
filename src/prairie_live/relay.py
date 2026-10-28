"""Scope-PC relay: PV frames on --port, JSON commands on --port+1.

Talks to PrairieView only over its script port (TCP 1236): frames via -gi
into our own buffer, Mark Points / state / Live / T-series as script
commands on the same logged-in session. PrairieLink COM is not used.
Ctrl+C (or Ctrl+Break on Windows) stops cleanly by closing listen sockets
so accept() cannot hang forever.
"""

from __future__ import annotations

import argparse
import json
import signal
import socket
import sys
import threading
import time

from prairie_live.protocol import pack_frame
from prairie_live.pv_script import PvScript

DEFAULT_PORT = 25100


def grab_heartbeat_line(
	frame,
	last_error: str,
	channel: int | None = None,
	sent: str = "",
) -> str:
	"""Scope-console line so a dark analysis FOV can be diagnosed without the plot."""
	ch = f" ch={int(channel)}" if channel is not None else ""
	if frame is None:
		line = f"grab heartbeat:{ch} NO FRAME  {last_error or 'no -gi frame'}"
	else:
		h, w = frame.shape[:2]
		line = (
			f"grab heartbeat:{ch} {int(w)}x{int(h)} "
			f"min={int(frame.min())} max={int(frame.max())} "
			f"mean={float(frame.mean()):.1f}"
		)
	if sent:
		line = f"{line}  {sent}"
	return line


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
		self.pv = PvScript(pv_host, password, image_wh=image_wh)
		self._grab_enabled = bool(grab)
		self._latest = None
		self._lock = threading.Lock()
		self._stop = threading.Event()
		# Set while -slm/-mp holds pv.lock so -gi cannot starve Abort/Stop.
		self._stim_hold = threading.Event()
		self._logged_first_grab = False
		self._hb_t = 0.0
		self._send_n = 0
		self._send_wh = None
		self._send_max = -1
		self._n_fired = 0

	def note_send(self, w: int, h: int, peak: int) -> None:
		self._send_n += 1
		self._send_wh = (int(w), int(h))
		self._send_max = int(peak)

	def sent_note(self) -> str:
		if not self._send_n or self._send_wh is None:
			return "send=none (view not on THIS relay — kill extra python)"
		w, h = self._send_wh
		return f"send {w}x{h} n={self._send_n} max={self._send_max}"

	def start(self) -> None:
		try:
			self.pv.connect()
		except OSError as e:
			raise RuntimeError(
				f"cannot reach PrairieView script port on {self.pv.host}:{self.pv.port} "
				f"({e}). PrairieView must be open with scripting enabled."
			) from e
		h, w = self.pv.wh
		print(f"PV script port {self.pv.host}:{self.pv.port}  FOV {int(w)}x{int(h)}")
		try:
			self.pv.disable_mark_points_metadata()
			print("MarkPointsMetadata=False (no ad-hoc E:\\Markpoints spills)")
		except Exception as e:
			print(f"MarkPointsMetadata False failed ({e})")
		if not self._grab_enabled:
			print("grab OFF (default); pass --grab for live frames")
			return
		try:
			self.pv.start_live()
			print("sent -LiveScan")
		except Exception as e:
			print(f"-LiveScan failed ({e}); click Live in PV")
		try:
			print(f"  ch{self.channel}: {self.pv.grab(self.channel).summary()}")
		except Exception as e:
			print(f"  ch{self.channel}: -gi probe failed: {e}")
		threading.Thread(target=self._grab_loop, daemon=True, name="pv-grab").start()
		print(f"grab ON (direct -gi, pid={self.pv.gi.pid})")

	def stop(self) -> None:
		self._stop.set()
		self.pv.disconnect()

	def _grab_loop(self) -> None:
		while not self._stop.is_set():
			# Why: -gi and -slm share one lock; grab during an armed stim makes
			# Stop/Abort wait behind every frame until the hang times out.
			if self._stim_hold.is_set():
				time.sleep(0.05)
				continue
			self._grab_one()
			# The PV lock is not fair; this gap lets Mark Points commands in.
			time.sleep(0.01)

	def _grab_one(self) -> None:
		try:
			frame = self.pv.grab(self.channel).frame
		except Exception as e:
			self.pv.last_error = f"-gi {type(e).__name__}: {e}"
			frame = None
			time.sleep(0.2)
		if frame is not None:
			with self._lock:
				self._latest = frame
			if not self._logged_first_grab:
				self._logged_first_grab = True
				print(
					f"first grab {int(frame.shape[1])}x{int(frame.shape[0])} "
					f"min={int(frame.min())} max={int(frame.max())} "
					f"mean={float(frame.mean()):.1f}"
				)
		else:
			time.sleep(0.05)
		now = time.monotonic()
		if now - self._hb_t >= 2.0:
			self._hb_t = now
			print(
				grab_heartbeat_line(
					frame, self.pv.last_error, self.channel, sent=self.sent_note()
				),
				flush=True,
			)

	def latest_frame(self):
		with self._lock:
			return self._latest

	def handle_command(self, payload: dict) -> dict:
		cmd = payload.get("cmd")
		try:
			return self._dispatch(cmd, payload)
		except Exception as e:
			return {"ok": False, "cmd": cmd, "error": str(e)}

	def _dispatch(self, cmd, payload: dict) -> dict:
		pv = self.pv
		if cmd == "ping":
			# The GUI's relay light goes green only when PV's session is up too.
			if pv.link is None:
				raise RuntimeError("PrairieView script port not connected")
			return {"ok": True, "cmd": "ping"}
		if cmd == "tseries":
			return pv.start_tseries()
		if cmd == "abort":
			return pv.abort()
		if cmd == "force_interrupt":
			# Dedicated path for a second ctrl client while -slm is mid-recv.
			hit = pv.force_interrupt(str(payload.get("why") or "ctrl force_interrupt"))
			return {"ok": True, "cmd": "force_interrupt", "interrupted": bool(hit)}
		if cmd == "abort_clear_stim":
			# GUI Stop: kill armed Mark Points / -slm, replace series, restore Live.
			from prairie_live.pv_cleanup import abort_and_clear_stim

			return {"ok": True, "cmd": "abort_clear_stim", **abort_and_clear_stim(pv)}
		if cmd == "live":
			# PV 6.1 needs explicit on|off; off must precede -slm/-mp.
			state = str(payload.get("state") or "on").strip().lower()
			if state in ("off", "0", "false"):
				return pv.stop_live()
			return pv.start_live()
		if cmd == "get_state":
			return pv.get_state(
				payload.get("key", ""), payload.get("index"), payload.get("subindex")
			)
		if cmd == "get_motor_position":
			return pv.get_motor_position(payload.get("axis", ""), payload.get("device"))
		if cmd == "load_mark_points":
			xml = payload.get("xml", "")
			out = pv.load_mark_points(xml, payload.get("path"))
			print(f"load_mark_points xml_bytes={len(xml)} DONE path={out['path']}")
			return out
		if cmd == "mark_points":
			self._n_fired += 1
			t0 = time.monotonic()
			no_wait = bool(payload["no_wait"]) if "no_wait" in payload else False
			timeout = float(payload.get("timeout") or 180.0)
			# Pause -gi so Abort from Stop is not queued behind grab frames.
			self._stim_hold.set()
			try:
				out = pv.mark_points(no_wait=no_wait, timeout=timeout)
			finally:
				self._stim_hold.clear()
			dt = (time.monotonic() - t0) * 1000.0
			print(f"mark_points #{self._n_fired} DONE in {dt:.0f} ms no_wait={out.get('no_wait')}")
			return out
		if cmd == "mark_all_points":
			parts = payload.get("parts") or []
			if not isinstance(parts, list) or not parts:
				raise ValueError("mark_all_points requires non-empty parts list")
			kw: dict = {
				"wait_ms": float(payload.get("wait_ms") or 0.0),
				"timeout": float(payload.get("timeout") or 180.0),
				"no_wait": bool(payload["no_wait"]) if "no_wait" in payload else False,
			}
			self._stim_hold.set()
			try:
				out = pv.mark_all_points(parts, **kw)
			finally:
				self._stim_hold.clear()
			print(
				f"mark_all_points DONE no_wait={out.get('no_wait')} "
				f"parts0={parts[0] if parts else None}"
			)
			return out
		return {"ok": False, "error": f"unknown cmd {cmd}"}


def _peer(conn: socket.socket) -> str:
	try:
		host, port = conn.getpeername()[:2]
		return f"{host}:{port}"
	except OSError:
		return "?"


def _push_one_frame(conn, relay, peer: str, n_sent: int, n_tiny: int) -> tuple[int, int]:
	frame = relay.latest_frame()
	if frame is None:
		return n_sent, n_tiny
	h, w = frame.shape[:2]
	# Never put 1×1 on the wire — analysis skip= grows forever on those packets.
	if min(h, w) < 32:
		if n_tiny == 0:
			print(f"frame NOT sent {peer} tiny {int(w)}x{int(h)}", flush=True)
		return n_sent, n_tiny + 1
	conn.sendall(pack_frame(frame, extra=relay.channel))
	peak = int(frame.max())
	relay.note_send(w, h, peak)
	if n_sent == 0:
		print(f"frame send {peer} {int(w)}x{int(h)} max={peak}", flush=True)
	return n_sent + 1, n_tiny


def _serve_frames(conn: socket.socket, relay: Relay, fps: float) -> None:
	period = 1.0 / max(fps, 1.0)
	try:
		conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
	except OSError:
		pass
	peer = _peer(conn)
	print(f"frame client {peer}", flush=True)
	n_sent = n_tiny = 0
	try:
		while not relay._stop.is_set():
			n_sent, n_tiny = _push_one_frame(conn, relay, peer, n_sent, n_tiny)
			time.sleep(period)
	except (ConnectionError, OSError) as e:
		print(f"frame client {peer} closed: {e}", flush=True)
	finally:
		conn.close()


def _serve_ctrl(conn: socket.socket, relay: Relay) -> None:
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
		# First Ctrl+C: close listen socks (unblocks accept) and stop the grab.
		if relay._stop.is_set():
			print("force exit")
			sys.exit(1)
		print("\nstopping (Ctrl+C) …")
		relay._stop.set()
		for s in servers:
			try:
				s.close()
			except OSError:
				pass

	signal.signal(signal.SIGINT, _shutdown)
	# Windows console Ctrl+Break
	if hasattr(signal, "SIGBREAK"):
		signal.signal(signal.SIGBREAK, _shutdown)

	print(f"frames {bind}:{port}  ctrl {bind}:{port + 1}  PV={relay.pv.host}")
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
	# Why: Windows SO_REUSEADDR lets two relays listen on 25100. View then
	# stays on the old 1×1 sender while the new console shows 512×512.
	srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
		srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
	else:
		srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
	try:
		srv.bind((bind, port))
	except OSError as e:
		raise SystemExit(
			f"port {port} already in use. Task Manager: kill every python.exe "
			f"on the scope PC, then start ONE `relay --grab`. ({e})"
		) from e
	srv.listen(4)
	return srv


def main(argv=None) -> None:
	p = argparse.ArgumentParser(description="PrairieView frame + command relay (scope PC)")
	p.add_argument("--pv-host", default="127.0.0.1", help="PrairieView IP")
	p.add_argument("--password", default="0000")
	p.add_argument("--bind", default="0.0.0.0")
	p.add_argument("--port", type=int, default=DEFAULT_PORT)
	p.add_argument("--channel", type=int, default=1)
	p.add_argument("--fps", type=float, default=20.0)
	# Used only if PV does not answer -gts pixelsPerLine / linesPerFrame.
	p.add_argument("--width", type=int, default=512, help="FOV width fallback")
	p.add_argument("--height", type=int, default=512, help="FOV height fallback")
	p.add_argument("--grab", action="store_true", help="stream live frames (-gi)")
	p.add_argument("--no-grab", action="store_true", help=argparse.SUPPRESS)
	# Old launch lines pass --grab-api gi; -gi is now the only grab path.
	p.add_argument("--grab-api", choices=("gi",), default="gi", help=argparse.SUPPRESS)
	args = p.parse_args(argv)

	relay = Relay(
		args.pv_host,
		args.password,
		args.channel,
		image_wh=(args.height, args.width),
		grab=bool(args.grab) and not bool(args.no_grab),
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
