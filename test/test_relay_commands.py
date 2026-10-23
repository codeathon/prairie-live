import numpy as np
import pytest

from prairie_live.gi_grab import GiResult
from prairie_live.pv_script import PvScript
from prairie_live.relay import Relay, grab_heartbeat_line


class FakeLink:
	"""PV script port stand-in: records every command on the one session."""

	def __init__(self, replies=None):
		self.timeout = 10.0
		self.sent: list[tuple] = []
		self.replies = replies or {}

	def send_command(self, *parts):
		self.sent.append((parts, self.timeout))
		return "ACK\nDONE"

	def query(self, *parts):
		self.sent.append((parts, self.timeout))
		return self.replies.get(parts, "")

	def close(self):
		pass


def _relay(replies=None):
	r = Relay("127.0.0.1", "0000", 1)
	r.pv.link = FakeLink(replies)
	return r


def _cmds(r):
	return [parts for parts, _ in r.pv.link.sent]


def test_ping_sends_nothing_to_pv():
	r = _relay()
	assert r.handle_command({"cmd": "ping"}) == {"ok": True, "cmd": "ping"}
	assert _cmds(r) == []


def test_ping_fails_when_pv_session_down():
	r = Relay("127.0.0.1", "0000", 1)
	out = r.handle_command({"cmd": "ping"})
	assert out["ok"] is False and "PrairieView" in out["error"]


def test_live_tseries_abort_are_script_commands():
	r = _relay()
	for cmd in ("live", "tseries", "abort"):
		assert r.handle_command({"cmd": cmd})["ok"] is True
	assert _cmds(r) == [("-LiveScan",), ("-TSeries",), ("-Abort",)]


def test_get_state_uses_gts():
	r = _relay({("-gts", "currentScanAmplitude", "XAxis"): "7.6"})
	out = r.handle_command(
		{"cmd": "get_state", "key": "currentScanAmplitude", "index": "XAxis"}
	)
	assert out["ok"] is True and out["value"] == "7.6" and out["index"] == "XAxis"
	assert _cmds(r) == [("-gts", "currentScanAmplitude", "XAxis")]


def test_get_state_needs_key():
	out = _relay().handle_command({"cmd": "get_state", "key": ""})
	assert out["ok"] is False and "key" in out["error"]


def test_get_motor_position_parses_number():
	r = _relay({("-GetMotorPosition", "Z"): "3595.85"})
	out = r.handle_command({"cmd": "get_motor_position", "axis": "z"})
	assert out == {"ok": True, "cmd": "get_motor_position", "axis": "Z", "value": 3595.85}


def test_get_motor_position_bad_axis_is_error():
	out = _relay().handle_command({"cmd": "get_motor_position", "axis": "Q"})
	assert out["ok"] is False and "axis" in out["error"]


def test_load_and_fire_mark_points_on_one_session(tmp_path):
	r = _relay()
	path = str(tmp_path / "trial.xml")
	out = r.handle_command({"cmd": "load_mark_points", "xml": "<x/>", "path": path})
	assert out["ok"] is True and out["path"] == path
	assert (tmp_path / "trial.xml").read_text() == "<x/>"
	assert r.handle_command({"cmd": "mark_points"})["ok"] is True
	assert r.pv.link.sent == [(("-LoadMarkPoints", path), 60.0), (("-MarkPoints",), 60.0)]
	assert r.pv.link.timeout == 10.0


def test_load_mark_points_rejects_empty_xml():
	out = _relay().handle_command({"cmd": "load_mark_points", "xml": " "})
	assert out["ok"] is False


def test_mark_all_points_waits_whole_ms_then_fires():
	r = _relay()
	parts = [
		"-MarkAllPoints",
		"1",
		"False",
		"0.1",
		"0.2",
		"10",
		"Monaco",
		"0.75",
		"Trig In 1",
		"1",
	]
	out = r.handle_command({"cmd": "mark_all_points", "parts": parts, "wait_ms": 22.1})
	assert out["ok"] is True
	assert _cmds(r) == [("-Wait", "22"), tuple(parts)]


def test_grab_one_stores_gi_frame():
	class FakeGi:
		last_error = ""
		pid = 1

		def grab(self, ch, w, h):
			return GiResult(np.full((h, w), 9, np.uint16), "ACK DONE", w * h, 0x1000000, True)

	r = _relay()
	r.pv.gi = FakeGi()
	r.pv.wh = (64, 128)
	r._grab_one()
	assert r.latest_frame().shape == (64, 128)
	assert int(r.latest_frame().max()) == 9


def test_pv_script_requires_connection():
	with pytest.raises(RuntimeError):
		PvScript().send("-LiveScan")


def test_unknown_command_is_rejected():
	r = _relay()
	out = r.handle_command({"cmd": "shutdown"})
	assert out["ok"] is False
	assert "unknown" in out["error"]


def test_relay_client_take_frame_skips_tiny():
	from prairie_live.relay_client import RelayClient

	c = RelayClient("127.0.0.1")
	c._take_frame(np.zeros((1, 1), dtype=np.uint16))
	assert c.get_frame() is None
	assert "skip=1" in c.last_error
	assert "1x1" in c.last_error
	assert "max=0" in c.last_error
	c._take_frame(np.ones((32, 40), dtype=np.uint16) * 9)
	got = c.get_frame()
	assert got is not None
	assert got.shape == (32, 40)
	assert int(got.max()) == 9


def test_relay_client_assembles_line_scan():
	from prairie_live.relay_client import RelayClient

	c = RelayClient("127.0.0.1")
	c._take_frame(np.arange(32, dtype=np.uint16).reshape(1, 32))
	got = c.get_frame()
	assert got is not None
	assert got.shape == (32, 32)
	assert c.frames_ok == 1
	assert c.frames_skip == 0


def test_grab_heartbeat_line_none_and_frame():
	assert "NO FRAME" in grab_heartbeat_line(None, "boom")
	assert "boom" in grab_heartbeat_line(None, "boom")
	frame = np.arange(4, dtype=np.uint16).reshape(2, 2)
	line = grab_heartbeat_line(frame, "", channel=2)
	assert "2x2" in line
	assert "max=3" in line
	assert "ch=2" in line
	with_send = grab_heartbeat_line(frame, "", sent="send 512x512 n=1 max=9")
	assert "send 512x512" in with_send
	assert "max=9" in with_send


def test_sent_note_idle_and_after_send():
	r = _relay()
	assert "send=none" in r.sent_note()
	r.note_send(512, 512, 40)
	assert "send 512x512" in r.sent_note()
	assert "max=40" in r.sent_note()


def test_bind_second_listen_fails():
	from prairie_live.relay import _bind

	a = _bind("127.0.0.1", 0)
	port = a.getsockname()[1]
	try:
		try:
			_bind("127.0.0.1", port)
		except SystemExit as e:
			assert "already in use" in str(e)
		else:
			raise AssertionError("expected SystemExit on second bind")
	finally:
		a.close()




class DroppingLink(FakeLink):
	def query(self, *parts):
		self.sent.append((parts, self.timeout))
		raise ConnectionResetError("[WinError 10054] forcibly closed by the remote host")


def test_dropped_session_reconnects_and_names_the_command(monkeypatch, capsys):
	r = Relay("127.0.0.1", "0000", 1)
	opened = []

	def fake_open():
		r.pv._opened_at = -10.0
		r.pv.link = FakeLink()
		r.pv.gi = None
		opened.append(1)

	monkeypatch.setattr(r.pv, "_open", fake_open)
	r.pv._started = True
	r.pv.link = DroppingLink()
	out = r.handle_command({"cmd": "get_state", "key": "currentScanAmplitude", "index": "XAxis"})
	assert out["ok"] is False and "not resent" in out["error"]
	assert opened == [1]
	assert "during '-gts currentScanAmplitude XAxis'" in capsys.readouterr().out
	assert r.handle_command({"cmd": "mark_points"})["ok"] is True
	assert _cmds(r) == [("-MarkPoints",)]


def test_prairie_link_raises_when_pv_closes_socket():
	import socket

	from prairie_live.prairie_link import PrairieLink

	a, b = socket.socketpair()
	link = PrairieLink.__new__(PrairieLink)
	link.sock, link.timeout = a, 2.0
	b.sendall(b"ACK\r\n")
	b.shutdown(socket.SHUT_WR)
	with pytest.raises(ConnectionResetError):
		link.send_command("-gts", "x")
	b.close()
