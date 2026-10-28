"""Live off must be ensured, then -MarkAllPoints with no idle gap."""

import pytest

from prairie_live.sync_loop import _fire_mark_all_points, _stop_live_for_stim


class _FakeRelay:
	def __init__(self, *, fail_times: int = 0) -> None:
		self.calls: list[str] = []
		self._fail_left = int(fail_times)

	def stop_live(self) -> dict:
		self.calls.append("stop_live")
		if self._fail_left > 0:
			self._fail_left -= 1
			return {"ok": False, "error": "still live"}
		return {"ok": True, "cmd": "live_off", "attempts": 3}

	def mark_all_points(self, parts, **_kw) -> dict:
		self.calls.append("mark_all_points:" + ",".join(str(p) for p in parts[:2]))
		return {"ok": True}


def test_fire_mark_all_points_stops_live_then_sends_slm():
	relay = _FakeRelay()
	_fire_mark_all_points(
		parts=["-MarkAllPoints", "1", "False", "0.5", "0.5", "36", "Monaco", "160", "Trig In 1", "1"],
		wait_ms=0.0,
		pl=None,
		relay=relay,
		via_relay=True,
		timeout=5.0,
	)
	assert relay.calls[0] == "stop_live"
	assert relay.calls[1].startswith("mark_all_points:")


def test_stop_live_for_stim_retries_until_ok():
	relay = _FakeRelay(fail_times=2)
	_stop_live_for_stim(relay=relay, pl=None, via_relay=True, label="test")
	assert relay.calls == ["stop_live", "stop_live", "stop_live"]


def test_stop_live_for_stim_raises_if_never_off():
	relay = _FakeRelay(fail_times=10)
	with pytest.raises(RuntimeError, match="could not ensure LiveScan off"):
		_stop_live_for_stim(relay=relay, pl=None, via_relay=True, label="test")
