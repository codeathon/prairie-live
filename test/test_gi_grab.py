import ctypes

import numpy as np

from prairie_live.gi_grab import GiGrabber, PixelBuffer, parse_wh_reply


class FakePV:
	"""Stands in for PV: on -gi, write w*h uint32 pixels at the given address."""

	def __init__(self, w=64, h=32, write=True, reply="ACK\r\nDONE"):
		self.w, self.h, self.write, self.reply = w, h, write, reply
		self.sent = []

	def query(self, cmd, key):
		return {"pixelsPerLine": str(self.w), "linesPerFrame": str(self.h)}[key]

	def send_command(self, *parts):
		self.sent.append(parts)
		if parts[0] == "-gi" and self.write:
			src = np.arange(self.w * self.h, dtype=np.uint32) + 7
			ctypes.memmove(int(parts[3]), src.ctypes.data, src.nbytes)
		return self.reply

	def close(self):
		pass


def test_grab_reads_pixels_pv_wrote_at_our_address():
	pv = FakePV()
	g = GiGrabber(pv, pid=1234, low=False)
	h, w = g.fov_wh()
	assert (h, w) == (32, 64)
	res = g.grab(2, w, h)
	assert pv.sent[-1][:3] == ("-gi", "2", "1234")
	assert int(pv.sent[-1][3]) == res.addr
	assert res.frame.shape == (32, 64)
	assert res.frame.dtype == np.uint16
	assert int(res.frame[0, 0]) == 7
	assert res.written == 64 * 32
	assert g.last_error == ""
	g.close()


def test_grab_reports_pv_reply_when_nothing_written():
	pv = FakePV(write=False, reply="ACK\r\nUnexpected parameter 2199023255552\r\nDONE")
	g = GiGrabber(pv, pid=1, low=False)
	res = g.grab(1, 64, 32)
	assert res.frame is None
	assert res.written == 0
	assert "Unexpected parameter" in res.summary()
	assert "wrote 0 px" in g.last_error


def test_pixel_buffer_sentinel_and_low_flag():
	buf = PixelBuffer(16, low=False)
	buf.fill_sentinel()
	assert buf.count_written() == 0
	buf.pixels[3] = 0
	assert buf.count_written() == 1
	assert buf.is_low == (buf.addr + 64 <= 0x80000000)
	buf.close()


def test_parse_wh_reply_skips_ack_and_tiny():
	assert parse_wh_reply("ACK\r\n512\r\nDONE") == 512
	assert parse_wh_reply("1") is None
