"""PrairieLink TCP script client (SOH framing, port 1236).

Password first, then commands with SOH between tokens and CRLF terminator.
Responses are ACK … [data] … DONE. Use this for -LoadMarkPoints / -MarkPoints;
live images still need COM or the relay.
"""

from __future__ import annotations

import socket
import time

from prairie_live.tcp_backend import DEFAULT_PORT

SOH = "\x01"


class PrairieLink:
	"""TCP client for Prairie View's script port (scope PC: 127.0.0.1:1236)."""

	def __init__(
		self,
		host: str = "127.0.0.1",
		port: int = DEFAULT_PORT,
		password: str = "0000",
		timeout: float = 120.0,
	):
		self.host = host
		self.port = port
		self.password = password
		self.timeout = timeout
		self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.sock.settimeout(timeout)
		self.sock.connect((host, port))
		# Password must be first on the wire or PV drops the socket.
		self._send_raw(self.password)
		try:
			self._recv_until_done(max_wait=min(timeout, 5.0))
		except OSError:
			pass

	def _send_raw(self, text: str) -> None:
		self.sock.sendall((text + "\r\n").encode("ascii"))

	def send_command(self, *parts: str) -> str:
		"""Send one script command; parts joined with SOH (not spaces)."""
		if not parts:
			raise ValueError("empty command")
		self._send_raw(SOH.join(parts))
		return self._recv_until_done(max_wait=self.timeout)

	def _recv_until_done(self, max_wait: float) -> str:
		"""Read until DONE appears or max_wait elapses (PV can be slow on -lmp)."""
		deadline = time.monotonic() + max_wait
		buf = b""
		while time.monotonic() < deadline:
			remaining = deadline - time.monotonic()
			self.sock.settimeout(max(remaining, 0.1))
			try:
				chunk = self.sock.recv(4096)
			except TimeoutError:
				if b"DONE" in buf:
					break
				continue
			if not chunk:
				break
			buf += chunk
			if b"DONE" in buf:
				break
		text = buf.decode("ascii", errors="replace").strip()
		if b"DONE" not in buf and time.monotonic() >= deadline:
			preview = text[:300] if text else "(no data)"
			raise TimeoutError(
				f"PrairieLink timed out after {max_wait:.0f}s waiting for DONE; "
				f"got: {preview!r}"
			)
		return text

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
		# Do not send -Exit here; it can hang while PV is busy and wedged the relay.
		try:
			self.sock.close()
		except OSError:
			pass
