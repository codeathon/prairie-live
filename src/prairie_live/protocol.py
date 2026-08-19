"""Length-prefixed binary frames so a Mac viewer can pull images off the
Windows scope PC. PrairieLink COM cannot run on macOS; the relay translates."""

from __future__ import annotations

import json
import struct
from typing import Any

import numpy as np

# 'PLV1' marks a frame packet. Commands are JSON lines on the same socket
# would interleave badly with binary, so commands use a second header.
MAGIC_FRAME = b"PLF1"
MAGIC_JSON = b"PLJ1"
_HDR = struct.Struct(">4sIII")  # magic, width, height, extra


def pack_frame(frame: np.ndarray, extra: int = 1) -> bytes:
	"""extra is the PMT channel number (1-based)."""
	if frame.dtype != np.uint16:
		frame = np.ascontiguousarray(frame, dtype=np.uint16)
	else:
		frame = np.ascontiguousarray(frame)
	h, w = frame.shape
	return _HDR.pack(MAGIC_FRAME, w, h, extra) + frame.tobytes()


def pack_json(payload: dict[str, Any]) -> bytes:
	body = json.dumps(payload).encode("utf-8")
	return _HDR.pack(MAGIC_JSON, len(body), 0, 0) + body


def _recv_exact(sock, n: int) -> bytes:
	buf = bytearray()
	while len(buf) < n:
		chunk = sock.recv(n - len(buf))
		if not chunk:
			raise ConnectionError("socket closed")
		buf.extend(chunk)
	return bytes(buf)


def recv_message(sock) -> tuple[str, Any]:
	hdr = _recv_exact(sock, _HDR.size)
	magic, a, b, extra = _HDR.unpack(hdr)
	if magic == MAGIC_FRAME:
		w, h, channel = a, b, extra
		raw = _recv_exact(sock, w * h * 2)
		frame = np.frombuffer(raw, dtype=np.uint16).reshape(h, w).copy()
		return "frame", {"image": frame, "channel": channel}
	if magic == MAGIC_JSON:
		body = _recv_exact(sock, a)
		return "json", json.loads(body.decode("utf-8"))
	raise ValueError(f"bad magic {magic!r}")
