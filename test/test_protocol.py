from prairie_live.com_backend import _as_frame
from prairie_live.protocol import pack_frame, pack_json, recv_message
import numpy as np
import socket
import threading


def test_as_frame_reshapes_row_major():
	w, h = 4, 3
	raw = np.arange(w * h, dtype=np.uint16)
	out = _as_frame(raw, w, h)
	assert out.shape == (h, w)
	assert out[0, 1] == 1


def test_as_frame_rejects_short_buffer():
	assert _as_frame(np.arange(3, dtype=np.uint16), 4, 3) is None


def test_pack_roundtrip():
	frame = np.arange(12, dtype=np.uint16).reshape(3, 4)
	blob = pack_frame(frame, extra=2)

	class _Mem:
		def __init__(self, data):
			self._data = data

		def recv(self, n):
			chunk, self._data = self._data[:n], self._data[n:]
			return chunk

	kind, payload = recv_message(_Mem(blob))
	assert kind == "frame"
	assert payload["channel"] == 2
	np.testing.assert_array_equal(payload["image"], frame)


def test_pack_json_roundtrip():
	blob = pack_json({"cmd": "ping"})

	class _Mem:
		def __init__(self, data):
			self._data = data

		def recv(self, n):
			chunk, self._data = self._data[:n], self._data[n:]
			return chunk

	kind, payload = recv_message(_Mem(blob))
	assert kind == "json"
	assert payload["cmd"] == "ping"
