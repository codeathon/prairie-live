from prairie_live.protocol import pack_frame, pack_json, recv_message
import numpy as np
import socket
import threading


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


def test_recv_survives_timeout_mid_payload():
	# Why: analysis stayed on 1×1 "not an FOV" after a 1s timeout mid-512² packet.
	frame = np.arange(12, dtype=np.uint16).reshape(3, 4)
	blob = pack_frame(frame, extra=1)
	calls = {"n": 0}

	class _Flaky:
		def __init__(self, data):
			self._data = data

		def recv(self, n):
			calls["n"] += 1
			if calls["n"] == 2:
				raise TimeoutError
			chunk, self._data = self._data[:n], self._data[n:]
			return chunk

	kind, payload = recv_message(_Flaky(blob))
	assert kind == "frame"
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
