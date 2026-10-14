"""PrairieCom 6.1 wrappers: timeout + grab, no live PrairieLink."""

import numpy as np

from prairie_live.com_backend import (
	PrairieCom,
	_as_frame_auto,
	apply_script_timeout,
	detect_grab_api,
	grab_raw,
)


class _TimeoutMs:
	def __init__(self):
		self.calls = []

	def SetTimeoutAndRetries(self, ms):
		self.calls.append((ms,))


class _TimeoutMsRetries:
	def __init__(self):
		self.calls = []

	def SetTimeoutAndRetries(self, ms, retries):
		self.calls.append((ms, retries))


class _ReadImagePL:
	def __init__(self, frame):
		self.frame = frame
		self.reads = []
		self.gi2 = []

	def ReadImage(self, channel):
		self.reads.append(channel)
		return self.frame

	def GetImage_2(self, channel, w, h):
		self.gi2.append((channel, w, h))
		return np.ones(w * h, dtype=np.uint16)


class _ReadImagesPL:
	def __init__(self, bundle):
		self.bundle = bundle
		self.gi2 = []

	def ReadImages(self):
		return self.bundle

	def GetImage_2(self, channel, w, h):
		self.gi2.append((channel, w, h))
		return np.ones(w * h, dtype=np.uint16)


class _BrokenReadImagePL:
	def GetImage_2(self, channel, w, h):
		return np.arange(w * h, dtype=np.uint16)

	def ReadImage(self, channel):
		raise RuntimeError("ReadImage not ready")


class _OldPL:
	def GetImage_2(self, channel, w, h):
		return np.arange(w * h, dtype=np.uint16)


def test_timeout_ms_only():
	# Why: some 6.1 builds take a single millisecond argument.
	pl = _TimeoutMs()
	assert apply_script_timeout(pl) == "SetTimeoutAndRetries(120000,)"
	assert pl.calls == [(120000,)]


def test_timeout_ms_and_retries():
	# Why: other builds require (ms, retries); TypeError on the 1-arg call.
	pl = _TimeoutMsRetries()
	assert apply_script_timeout(pl) == "SetTimeoutAndRetries(120000, 3)"
	assert pl.calls == [(120000, 3)]


def test_timeout_missing_on_58():
	assert apply_script_timeout(_OldPL()) is None


def test_detect_prefers_readimage():
	assert detect_grab_api(_ReadImagePL(np.ones(4))) == "ReadImage"
	assert detect_grab_api(_ReadImagesPL([np.ones(4)])) == "ReadImages"
	assert detect_grab_api(_OldPL()) == "GetImage_2"


def test_grab_uses_readimage():
	frame = np.arange(4, dtype=np.uint16)
	pl = _ReadImagePL(frame)
	failed: set[str] = set()
	out = grab_raw(pl, 1, (2, 2), failed)
	assert np.array_equal(out, frame)
	assert pl.reads == [1]
	assert pl.gi2 == []


def test_grab_uses_readimages_list():
	ch1 = np.arange(4, dtype=np.uint16)
	pl = _ReadImagesPL([np.zeros(4, dtype=np.uint16), ch1])
	out = grab_raw(pl, 2, (2, 2), set())
	assert np.array_equal(out, ch1)


def test_grab_falls_back_when_readimage_raises():
	# Why: a present-but-broken 6.1 method must not block GetImage_2 forever.
	pl = _BrokenReadImagePL()
	failed: set[str] = set()
	out = grab_raw(pl, 1, (2, 2), failed)
	assert out.size == 4
	assert "ReadImage" in failed
	out2 = grab_raw(pl, 1, (2, 2), failed)
	assert out2.size == 4


def test_grab_58_getimage2():
	pl = _OldPL()
	out = grab_raw(pl, 1, (2, 2), set())
	assert out.size == 4


def test_prairiecom_get_frame_readimage():
	com = PrairieCom("127.0.0.1", "0000")
	frame = np.arange(4, dtype=np.uint16).reshape(2, 2)
	com._pl = _ReadImagePL(frame.reshape(-1))
	com._last_wh = (2, 2)
	got = com.get_frame(1)
	assert got is not None
	assert got.shape == (2, 2)


def test_readimage_none_cells_fall_back_to_getimage2():
	# Why: 6.1 ReadImage returned VARIANT Nones and killed the grab thread.
	pl = _ReadImagePL(np.array([None, None, None, None], dtype=object))
	failed: set[str] = set()
	out = grab_raw(pl, 1, (2, 2), failed)
	assert out.size == 4
	assert "ReadImage" in failed
	assert pl.gi2 == [(1, 2, 2)]


def test_readimage_tuple_with_trailing_none():
	frame = np.arange(4, dtype=np.uint16)
	pl = _ReadImagePL((frame, None))
	failed: set[str] = set()
	out = grab_raw(pl, 1, (2, 2), failed)
	assert np.array_equal(out[0] if isinstance(out, tuple) else out, frame)
	assert pl.gi2 == []


def test_as_frame_auto_none_cells_does_not_raise():
	assert _as_frame_auto(np.array([None, 1], dtype=object), (1, 2)) is None


def test_get_frame_survives_none_variant():
	com = PrairieCom("127.0.0.1", "0000")
	com._pl = _ReadImagePL(np.array([None] * 4, dtype=object))
	com._last_wh = (2, 2)
	got = com.get_frame(1)
	# Falls back to GetImage_2 ones(2*2).
	assert got is not None
	assert got.shape == (2, 2)


def test_readimage_scalar_does_not_lock_fov_to_1x1():
	# Why: 6.1 Alpha ReadImage returned one number; title showed 1x1 dark FOV.
	pl = _ReadImagePL(np.array(16, dtype=np.uint16))
	failed: set[str] = set()
	out = grab_raw(pl, 1, (512, 512), failed)
	assert out.size == 512 * 512
	assert "ReadImage" in failed
	assert pl.gi2 == [(1, 512, 512)]


def test_as_frame_auto_rejects_scalar_against_512():
	assert _as_frame_auto(np.array([16], dtype=np.uint16), (512, 512)) is None
	assert _as_frame_auto(np.arange(4, dtype=np.uint16), (2, 2)).shape == (2, 2)


def test_readimage_constant_falls_back_to_getimage2():
	# Why: 6.1 ReadImage can return a 512² of zeros; that looked like a live FOV.
	pl = _ReadImagePL(np.zeros(512 * 512, dtype=np.uint16))
	failed: set[str] = set()
	out = grab_raw(pl, 1, (512, 512), failed)
	assert out.size == 512 * 512
	assert np.all(np.asarray(out) == 1)
	# empty, not bad — keep retrying ReadImage on later frames.
	assert "ReadImage" not in failed
	assert pl.gi2 == [(1, 512, 512)]


def test_stale_readimage_switches_to_getimage2(monkeypatch):
	# Why: GUI Live can refresh PV while ReadImage stays on the last T-series.
	times = [100.0]
	monkeypatch.setattr("prairie_live.com_backend.time.monotonic", lambda: times[0])
	raw = np.arange(32 * 32, dtype=np.uint16)
	pl = _ReadImagePL(raw)
	com = PrairieCom("127.0.0.1", "0000")
	com._pl = pl
	com._last_wh = (32, 32)
	assert com.get_frame(1) is not None
	assert pl.gi2 == []
	times[0] = 103.0
	com.get_frame(1)
	assert "ReadImage" in com._grab_failed
	third = com.get_frame(1)
	assert pl.gi2 == [(1, 32, 32)]
	assert third is not None
	assert third.shape == (32, 32)
