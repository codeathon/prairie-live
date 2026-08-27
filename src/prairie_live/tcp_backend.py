"""TCP script commands (T-series, abort). Does not carry live images.

PrairieLink's TCP port (default 1236) is a command channel. Use COM or the
relay for frames. Wire format from PV script help: password first, SOH
between tokens, terminate with CR+LF; expect ACK … DONE.
Prefer prairie_live.prairie_link.PrairieLink for new call sites.
"""

from __future__ import annotations

import socket

DEFAULT_PORT = 1236
SOH = "\x01"
_CRLF = "\r\n"


class PrairieTcp:
	def __init__(self, host: str, password: str = "0000", port: int = DEFAULT_PORT):
		self.host = host
		self.password = password
		self.port = port
		self._sock: socket.socket | None = None

	def connect(self) -> None:
		sock = socket.create_connection((self.host, self.port), timeout=5)
		sock.settimeout(5)
		self._sock = sock
		self._authenticate()

	def disconnect(self) -> None:
		if self._sock is None:
			return
		try:
			self._sock.close()
		finally:
			self._sock = None

	def send(self, cmd: str) -> str:
		# Single-token cmds are fine as-is; multi-arg strings should use SOH.
		sock = self._require()
		text = cmd.rstrip("\r\n")
		sock.sendall((text + _CRLF).encode("ascii", errors="replace"))
		return self._recv_text()

	def start_tseries(self) -> dict:
		self.send("-TSeries")
		return {"ok": True, "cmd": "tseries"}

	def abort(self) -> dict:
		self.send("-Abort")
		return {"ok": True, "cmd": "abort"}

	def start_live(self) -> dict:
		# Official help lists only -LiveScan (-lv), not -Live.
		self.send("-LiveScan")
		return {"ok": True, "cmd": "live"}

	def _authenticate(self) -> None:
		# Password must be first; CR+LF terminator (not bare LF).
		self._require().sendall((self.password + _CRLF).encode("ascii"))
		try:
			self._recv_text()
		except Exception:
			# Some PV builds do not echo; command sends still work.
			pass

	def _recv_text(self) -> str:
		sock = self._require()
		buf = b""
		while True:
			chunk = sock.recv(4096)
			if not chunk:
				break
			buf += chunk
			if b"DONE" in buf or b"\n" in buf:
				break
		return buf.decode("utf-8", errors="replace").strip()

	def _require(self) -> socket.socket:
		if self._sock is None:
			raise RuntimeError("not connected")
		return self._sock
