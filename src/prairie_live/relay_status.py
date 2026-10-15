"""Probe prairie_live.relay + PrairieView without opening a frame stream."""

from __future__ import annotations

import json
import socket


def parse_relay_addr(addr: str) -> tuple[str, int]:
	text = (addr or "").strip()
	if not text:
		raise ValueError("relay address is empty")
	host, _, port = text.partition(":")
	if not host:
		raise ValueError(f"bad relay address: {addr!r}")
	return host, int(port or 25100)


def probe_relay(addr: str, *, timeout: float = 1.5) -> dict:
	"""
	Green when the control port answers ping (relay up and PV script COM live).

	ping on the relay calls _script() first, so a failed ping with a live
	TCP socket usually means PrairieView / PrairieLink is down.
	"""
	try:
		host, port = parse_relay_addr(addr)
	except ValueError as exc:
		return {"up": False, "pv": False, "detail": str(exc), "addr": addr}
	ctrl = port + 1
	try:
		sock = socket.create_connection((host, ctrl), timeout=timeout)
	except OSError as exc:
		return {
			"up": False,
			"pv": False,
			"detail": f"relay not reachable on {host}:{ctrl} ({exc})",
			"addr": f"{host}:{port}",
		}
	try:
		sock.settimeout(timeout)
		sock.sendall(b'{"cmd":"ping"}\n')
		line = sock.makefile("rb").readline()
		if not line:
			return {
				"up": True,
				"pv": False,
				"detail": "relay port open, no ping reply",
				"addr": f"{host}:{port}",
			}
		reply = json.loads(line.decode("utf-8"))
		if reply.get("ok"):
			return {
				"up": True,
				"pv": True,
				"detail": "relay up · PrairieView COM live",
				"addr": f"{host}:{port}",
			}
		err = reply.get("error") or "ping failed"
		return {
			"up": True,
			"pv": False,
			"detail": f"relay up · PrairieView not ready ({err})",
			"addr": f"{host}:{port}",
		}
	except Exception as exc:
		return {
			"up": True,
			"pv": False,
			"detail": f"relay up · ping failed ({exc})",
			"addr": f"{host}:{port}",
		}
	finally:
		try:
			sock.close()
		except OSError:
			pass
