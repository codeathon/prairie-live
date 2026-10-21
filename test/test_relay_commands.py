import threading

import numpy as np

from prairie_live.com_backend import PrairieCom
from prairie_live.relay import Relay, grab_heartbeat_line


class FakeCom:
	"""Stand-in for PrairieCom so relay dispatch can be tested without COM."""

	def __init__(self):
		self.calls = []

	def start_tseries(self):
		self.calls.append("tseries")

	def abort(self):
		self.calls.append("abort")

	def start_live(self):
		self.calls.append("live")

	def get_state(self, key, index=None, subindex=None):
		self.calls.append(("get_state", key, index, subindex))
		return {
			"ok": True,
			"cmd": "get_state",
			"key": key,
			"value": f"{key}/{index}/{subindex}",
		}

	def get_motor_position(self, axis, device=None):
		self.calls.append(("get_motor_position", axis, device))
		if axis.upper() not in ("X", "Y", "Z"):
			raise ValueError(f"bad axis {axis!r}")
		return {
			"ok": True,
			"cmd": "get_motor_position",
			"axis": axis.upper(),
			"value": 12.5,
		}

	def load_mark_points_xml(self, xml, path=None):
		self.calls.append(("load_mark_points_xml", xml[:20], path))
		if not xml.strip():
			raise ValueError("empty xml")
		return {"ok": True, "cmd": "load_mark_points", "path": path or "tmp.xml"}

	def mark_points(self):
		self.calls.append("mark_points")
		return {"ok": True, "cmd": "mark_points"}

	def mark_all_points(self, parts, *, wait_ms=0.0):
		self.calls.append(("mark_all_points", list(parts), wait_ms))
		return {"ok": True, "cmd": "mark_all_points", "parts": list(parts)}


def _relay():
	r = Relay("127.0.0.1", "0000", 1)
	fake = FakeCom()
	r.com = fake
	r.script_com = fake
	return r


def test_ping_does_not_touch_com():
	r = _relay()
	assert r.handle_command({"cmd": "ping"}) == {"ok": True, "cmd": "ping"}
	assert r.com.calls == []


def test_get_state_passes_key_and_index():
	r = _relay()
	out = r.handle_command(
		{"cmd": "get_state", "key": "micronsPerPixel", "index": "XAxis"}
	)
	assert out["ok"] is True
	assert out["value"] == "micronsPerPixel/XAxis/None"
	assert r.com.calls == [("get_state", "micronsPerPixel", "XAxis", None)]


def test_get_motor_position_returns_float():
	r = _relay()
	out = r.handle_command({"cmd": "get_motor_position", "axis": "X"})
	assert out["ok"] is True
	assert out["axis"] == "X"
	assert out["value"] == 12.5


def test_get_motor_position_bad_axis_is_error():
	r = _relay()
	out = r.handle_command({"cmd": "get_motor_position", "axis": "Q"})
	assert out["ok"] is False
	assert "axis" in out["error"]


def test_load_mark_points_and_mark_points(tmp_path):
	r = _relay()
	xml = "<PVMarkPointSeriesElements/>"
	path = str(tmp_path / "trial.xml")
	out = r.handle_command({"cmd": "load_mark_points", "xml": xml, "path": path})
	assert out["ok"] is True
	assert out["cmd"] == "load_mark_points"
	assert out["path"] == path
	assert r.com.calls[0][0] == "load_mark_points_xml"
	out = r.handle_command({"cmd": "mark_points"})
	assert out == {"ok": True, "cmd": "mark_points", "via": "com"}
	assert r.com.calls[1] == "mark_points"


def test_mark_points_prefers_tcp_script(tmp_path):
	r = _relay()
	sent = []

	def fake_tcp(*parts):
		sent.append(parts)
		return "ACK\nDONE"

	r._script_command = fake_tcp
	path = str(tmp_path / "trial.xml")
	out = r.handle_command({"cmd": "load_mark_points", "xml": "<x/>", "path": path})
	assert out["ok"] is True and out["path"] == path
	out = r.handle_command({"cmd": "mark_points"})
	assert out["ok"] is True and out["via"] == "tcp"
	assert sent == [("-LoadMarkPoints", path), ("-MarkPoints",)]
	assert r.com.calls == []


def test_mark_all_points_dispatch():
	r = _relay()
	parts = ["-MarkAllPoints", "1", "False", "0.1", "0.2", "10", "Monaco", "0.75", "PFI1"]
	out = r.handle_command(
		{"cmd": "mark_all_points", "parts": parts, "wait_ms": 22.1}
	)
	assert out["ok"] is True
	assert out["cmd"] == "mark_all_points"
	assert r.com.calls[0] == ("mark_all_points", parts, 22.1)


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


def test_probe_and_pick_channel_switches():
	r = _relay()
	r.channel = 1
	r.com.probe_live_grab = lambda preferred: {
		"wh": (32, 64),
		"channel": 3,
		"scores": {1: 0.0, 2: 0.0, 3: 12.0, 4: 0.0},
		"rows": ["ch3 64x32 max=12 ptp=12"],
	}
	r._probe_and_pick_channel()
	assert r.channel == 3


def test_drain_com_jobs_runs_fn():
	r = _relay()
	box: list = []
	err: list = []
	done = threading.Event()
	r._com_jobs.put((lambda: 7, box, err, done))
	r._drain_com_jobs()
	assert box == [7]
	assert done.is_set()
	assert err == []


def test_arm_live_grab_sends_livescan():
	# Why: PV Live button does not always fill the PrairieLink grab buffer.
	r = _relay()
	r._arm_live_grab()
	assert r.com.calls == ["live"]


def test_unknown_command_is_rejected():
	r = _relay()
	out = r.handle_command({"cmd": "shutdown"})
	assert out["ok"] is False
	assert "unknown" in out["error"]


class _StubPl:
	def GetState(self, key, index=None, subindex=None):
		if index is None:
			return "4.0"
		if subindex is None:
			return f"{key}:{index}"
		return f"{key}:{index}:{subindex}"

	def GetMotorPosition(self, axis, device=None):
		return 1.25 if device is None else 9.0


def test_prairie_com_get_state_and_motor():
	c = PrairieCom("127.0.0.1")
	c._pl = _StubPl()
	st = c.get_state("dwellTime")
	assert st["value"] == "4.0"
	st = c.get_state("micronsPerPixel", "XAxis")
	assert st["value"] == "micronsPerPixel:XAxis"
	mot = c.get_motor_position("y")
	assert mot["axis"] == "Y"
	assert mot["value"] == 1.25


def test_prairie_com_rejects_empty_key_and_bad_axis():
	c = PrairieCom("127.0.0.1")
	c._pl = _StubPl()
	try:
		c.get_state("")
		assert False
	except ValueError:
		pass
	try:
		c.get_motor_position("Q")
		assert False
	except ValueError:
		pass


def test_grab_one_uses_direct_gi_when_selected():
	import numpy as np

	from prairie_live.gi_grab import GiResult

	class FakeGi:
		last_error = ""

		def grab(self, ch, w, h):
			return GiResult(np.full((h, w), 9, np.uint16), "ACK DONE", w * h, 0x1000000, True)

	r = Relay("127.0.0.1", "0000", 1, grab=True, grab_api="gi")
	r._gi = FakeGi()
	r._gi_wh = (64, 128)
	r._grab_one()
	assert r.latest_frame().shape == (64, 128)
	assert int(r.latest_frame().max()) == 9


def test_script_command_reuses_gi_link_instead_of_new_login():
	r = _relay()

	class Link:
		timeout = 10.0

		def __init__(self):
			self.sent = []

		def send_command(self, *parts):
			self.sent.append((parts, self.timeout))
			return "ACK\nDONE"

	class Gi:
		link = Link()

	r._gi = Gi()
	out = r.handle_command({"cmd": "mark_points"})
	assert out["via"] == "tcp"
	assert Gi.link.sent == [(("-MarkPoints",), 60.0)]
	assert Gi.link.timeout == 10.0
