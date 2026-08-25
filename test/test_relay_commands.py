from prairie_live.com_backend import PrairieCom
from prairie_live.relay import Relay


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


def _relay():
	r = Relay("127.0.0.1", "0000", 1)
	r.com = FakeCom()
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
	# Unit tests stub TCP script port (no PrairieView on CI).
	calls = []

	def _script(*parts):
		calls.append(parts)
		return "ACK\nDONE"

	r._script_command = _script
	xml = "<PVMarkPointSeriesElements/>"
	path = str(tmp_path / "trial.xml")
	out = r.handle_command({"cmd": "load_mark_points", "xml": xml, "path": path})
	assert out["ok"] is True
	assert out["cmd"] == "load_mark_points"
	assert out["path"] == path
	assert calls[0] == ("-LoadMarkPoints", path)
	out = r.handle_command({"cmd": "mark_points"})
	assert out == {"ok": True, "cmd": "mark_points"}
	assert calls[1] == ("-MarkPoints",)


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
