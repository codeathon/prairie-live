"""PrairieLink TCP script client (SOH framing, port 1236).

Password first, then commands with SOH between tokens and CRLF terminator.
Responses are ACK … [data] … DONE. Use this for -LoadMarkPoints / -MarkPoints;
live images still need COM or the relay.
"""

from __future__ import annotations

import socket

from prairie_live.tcp_backend import DEFAULT_PORT

SOH = "\x01"


class PrairieLink:
	"""TCP client for Prairie View's script port (scope PC: 127.0.0.1:1236)."""

	def __init__(
		self,
		host: str = "127.0.0.1",
		port: int = DEFAULT_PORT,
		password: str = "0000",
		timeout: float = 10.0,
	):
		self.host = host
		self.port = port
		self.password = password
		self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.sock.settimeout(timeout)
		self.sock.connect((host, port))
		# Password must be first on the wire or PV drops the socket.
		self._send_raw(self.password)
		try:
			self._readline()
		except OSError:
			pass

	def _readline(self) -> str:
		buf = bytearray()
		while True:
			ch = self.sock.recv(1)
			if not ch:
				break
			buf += ch
			if ch == b"\n":
				break
		return buf.decode("ascii", errors="replace").strip()

	def _send_raw(self, text: str) -> None:
		self.sock.sendall((text + "\r\n").encode("ascii"))

	def send_command(self, *parts: str) -> str:
		"""Send one script command; parts joined with SOH (not spaces)."""
		if not parts:
			raise ValueError("empty command")
		self._send_raw(SOH.join(parts))
		lines = []
		while True:
			line = self._readline()
			lines.append(line)
			if line == "DONE":
				break
		return "\n".join(lines)

	def query(self, *parts: str) -> str:
		"""Send a command; return data lines between ACK and DONE."""
		raw = self.send_command(*parts)
		data_lines = [
			ln
			for ln in raw.splitlines()
			if ln.strip() and ln.strip() not in ("ACK", "DONE")
		]
		return "\n".join(data_lines).strip()

	def close(self) -> None:
		try:
			self.send_command("-Exit")
		except OSError:
			pass
		self.sock.close()
