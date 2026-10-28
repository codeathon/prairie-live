"""Abort + clear Mark Points on Stop."""

from __future__ import annotations

from prairie_live.pv_cleanup import EMPTY_MARK_POINTS_XML, abort_and_clear_stim
from prairie_live.relay import Relay
from test_relay_commands import FakeLink, _cmds, _relay


class FakeClient:
	def __init__(self) -> None:
		self.calls: list[str] = []

	def abort(self) -> dict:
		self.calls.append("abort")
		return {"ok": True, "cmd": "abort"}

	def load_mark_points(self, xml: str, path: str | None = None) -> dict:
		self.calls.append("load")
		assert "PVMarkPointSeriesElements" in xml
		assert "<PVMarkPointElement" not in xml
		return {"ok": True, "cmd": "load_mark_points", "path": path or "x.xml"}

	def start_live(self) -> dict:
		self.calls.append("live")
		return {"ok": True, "cmd": "live"}


def test_empty_series_has_no_points():
	assert "PVMarkPointSeriesElements" in EMPTY_MARK_POINTS_XML
	assert "PVMarkPointElement" not in EMPTY_MARK_POINTS_XML


def test_abort_and_clear_stim_order():
	c = FakeClient()
	out = abort_and_clear_stim(c, settle_s=0.0)
	assert out["ok"] is True
	# Double Abort after settle clears leftover -slm before series.
	assert c.calls == ["abort", "abort", "live"]
	assert out["notes"] == ["abort", "abort2", "live"]


def test_abort_and_clear_stim_force_interrupts_first():
	"""Stop must wake hung -slm before Abort (bool or dict interrupt)."""

	class Holding(FakeClient):
		def force_interrupt(self, why: str = "") -> bool:
			self.calls.append(f"force:{why}")
			return True

	c = Holding()
	out = abort_and_clear_stim(c, settle_s=0.0)
	assert out["ok"] is True
	assert c.calls[0].startswith("force:")
	assert "force_interrupt" in out["notes"]
	assert "interrupted" in out["notes"]
	assert c.calls[1:] == ["abort", "abort", "live"]


def test_clear_prior_skips_fake_without_abort():
	from prairie_live.pv_cleanup import clear_prior_stim_on_client

	class NoAbort:
		pass

	out = clear_prior_stim_on_client(NoAbort(), None)
	assert out["notes"] == ["skipped"]


def test_relay_abort_clear_stim_command():
	r = _relay()
	out = r.handle_command({"cmd": "abort_clear_stim"})
	assert out["ok"] is True
	assert out["cmd"] == "abort_clear_stim"
	cmds = _cmds(r)
	assert cmds[0] == ("-Silent", "-Abort")
	assert cmds[1] == ("-Silent", "-Abort")
	assert cmds[2] == ("-LiveScan", "on")
