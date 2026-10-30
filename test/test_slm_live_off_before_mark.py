"""Live off only when arming Trig In 1; Trigger=None keeps Live on to burn."""

import pytest

from prairie_live.sync_loop import (
	_fire_mark_all_points,
	_slm_needs_live_off,
	_stop_live_for_stim,
)


class _FakeRelay:
	def __init__(self, *, fail_times: int = 0) -> None:
		self.calls: list[str] = []
		self._fail_left = int(fail_times)
		self.last_ensure: bool | None = None

	def stop_live(self) -> dict:
		self.calls.append("stop_live")
		if self._fail_left > 0:
			self._fail_left -= 1
			return {"ok": False, "error": "still live"}
		return {"ok": True, "cmd": "live_off", "attempts": 3}

	def mark_all_points(self, parts, **kw) -> dict:
		self.last_ensure = kw.get("ensure_live_off")
		self.calls.append("mark_all_points:" + ",".join(str(p) for p in parts[:2]))
		return {"ok": True}


def test_fire_mark_all_points_asks_relay_to_ensure_live_off():
	# Trig In 1: client must not Live-off then release — relay does off+-slm.
	relay = _FakeRelay()
	_fire_mark_all_points(
		parts=["-MarkAllPoints", "1", "False", "0.5", "0.5", "36", "Monaco", "160", "Trig In 1", "1"],
		wait_ms=0.0,
		pl=None,
		relay=relay,
		via_relay=True,
		timeout=5.0,
		ensure_live_off=True,
	)
	assert relay.calls == [
		"mark_all_points:-MarkAllPoints,1",
	]
	assert relay.last_ensure is True


def test_fire_mark_all_points_trigger_none_leaves_live_on():
	# Trigger=None + Live off → DONE with no burns; keep Live on for uncage.
	relay = _FakeRelay()
	_fire_mark_all_points(
		parts=["-MarkAllPoints", "1", "False", "0.5", "0.5", "36", "Monaco", "160", "None"],
		wait_ms=0.0,
		pl=None,
		relay=relay,
		via_relay=True,
		timeout=5.0,
		ensure_live_off=False,
	)
	assert relay.calls == ["mark_all_points:-MarkAllPoints,1"]
	assert relay.last_ensure is False


def test_slm_needs_live_off_only_for_bnc_trigger():
	assert _slm_needs_live_off("Trig In 1") is True
	assert _slm_needs_live_off("None") is False
	assert _slm_needs_live_off("none") is False
	assert _slm_needs_live_off("") is False


def test_stop_live_for_stim_retries_until_ok():
	relay = _FakeRelay(fail_times=2)
	_stop_live_for_stim(relay=relay, pl=None, via_relay=True, label="test")
	assert relay.calls == ["stop_live", "stop_live", "stop_live"]


def test_stop_live_for_stim_raises_if_never_off():
	relay = _FakeRelay(fail_times=10)
	with pytest.raises(RuntimeError, match="could not ensure LiveScan off"):
		_stop_live_for_stim(relay=relay, pl=None, via_relay=True, label="test")
