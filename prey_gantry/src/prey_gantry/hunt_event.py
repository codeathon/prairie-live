"""Hunt-event re-arm. Why: copy pylon-track so flees cannot stack every 20 ms."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HuntArmState:
	threat_was_high: bool = False
	last_event_ns: int = 0


def evaluate_hunt_event(
	threat_high: bool,
	now_ns: int,
	min_interval_ms: int,
	state: HuntArmState,
) -> bool:
	"""Match pylon-track: rising edge, or sustained high after last_event_ns."""
	interval_ns = max(0, min_interval_ms) * 1_000_000
	rising = threat_high and not state.threat_was_high
	interval_elapsed = state.last_event_ns > 0 and (now_ns - state.last_event_ns) >= interval_ns
	fire = rising or (threat_high and interval_elapsed)
	state.threat_was_high = threat_high
	return fire


def note_hunt_flee_complete(state: HuntArmState, now_ns: int) -> None:
	# Why: interval is measured from flee end, not from the rising edge.
	state.last_event_ns = now_ns
