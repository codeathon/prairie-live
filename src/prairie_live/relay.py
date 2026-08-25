"""Optional relay when the viewer PC does not have PrairieLink installed.

Run on the scope PC. Frames on --port, JSON commands on --port+1.
"""

from __future__ import annotations

import argparse
import json
import socket
import threading
import time

from prairie_live.com_backend import PrairieCom
from prairie_live.protocol import pack_frame

DEFAULT_PORT = 25100


class Relay:
	def __init__(self, pv_host: str, password: str, channel: int):
		self.channel = channel
		self.com = PrairieCom(pv_host, password)
		self._latest = None
		self._lock = threading.Lock()
		self._stop = threading.Event()

	def start(self) -> None:
		self.com.connect()
		threading.Thread(target=self._grab_loop, daemon=True).start()

	def stop(self) -> None:
		self._stop.set()
		self.com.disconnect()

	def _grab_loop(self) -> None:
		# win32com must be CoInitialized on every thread that calls COM.
		try:
			import pythoncom

			pythoncom.CoInitialize()
		except Exception:
			pass
		while not self._stop.is_set():
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
		try:
			if cmd == "tseries":
				self.com.start_tseries()
			elif cmd == "abort":
				self.com.abort()
			elif cmd == "live":
				self.com.start_live()
			elif cmd == "ping":
				pass
			elif cmd == "get_state":
				return self.com.get_state(
					payload.get("key", ""),
					payload.get("index"),
					payload.get("subindex"),
				)
			elif cmd == "get_motor_position":
				return self.com.get_motor_position(
					payload.get("axis", ""),
					payload.get("device"),
				)
			elif cmd == "load_mark_points":
				# Analysis pushes XML; scope writes a local file PV can open.
				return self.com.load_mark_points_xml(
					payload.get("xml", ""),
					payload.get("path"),
				)
			elif cmd == "mark_points":
				return self.com.mark_points()
			else:
				return {"ok": False, "error": f"unknown cmd {cmd}"}
			return {"ok": True, "cmd": cmd}
		except Exception as e:
			return {"ok": False, "cmd": cmd, "error": str(e)}


def _serve_frames(conn: socket.socket, relay: Relay, fps: float) -> None:
	period = 1.0 / max(fps, 1.0)
	try:
		while True:
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
	f = conn.makefile("rwb")
	try:
		for line in f:
			payload = json.loads(line.decode("utf-8"))
			reply = relay.handle_command(payload)
			f.write((json.dumps(reply) + "\n").encode("utf-8"))
			f.flush()
	except (ConnectionError, OSError, ValueError):
		pass
	finally:
		conn.close()


def _accept_loop(sock: socket.socket, handler, *args) -> None:
	while True:
		conn, addr = sock.accept()
		print(f"{handler.__name__} {addr[0]}:{addr[1]}")
		threading.Thread(target=handler, args=(conn,) + args, daemon=True).start()


def listen(relay: Relay, bind: str, port: int, fps: float) -> None:
	frame_srv = _bind(bind, port)
	ctrl_srv = _bind(bind, port + 1)
	print(f"frames {bind}:{port}  ctrl {bind}:{port + 1}  PV={relay.com.host}")
	threading.Thread(
		target=_accept_loop, args=(frame_srv, _serve_frames, relay, fps), daemon=True
	).start()
	try:
		_accept_loop(ctrl_srv, _serve_ctrl, relay)
	finally:
		frame_srv.close()
		ctrl_srv.close()


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
		print("stopping")
	finally:
		relay.stop()


if __name__ == "__main__":
	main()
