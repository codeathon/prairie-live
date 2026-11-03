"""LiveFeed must not -LiveScan on during a serial SLM Run."""

from __future__ import annotations

from prairie_live.live_feed import LiveFeed


def test_hold_for_stim_sets_snapshot_flag():
	feed = LiveFeed("127.0.0.1:25100")
	assert feed.snapshot()["stim_hold"] is False
	feed.hold_for_stim(True)
	assert feed.snapshot()["stim_hold"] is True
	assert "stim hold" in feed.snapshot()["detail"]
	feed.hold_for_stim(False)
	assert feed.snapshot()["stim_hold"] is False
