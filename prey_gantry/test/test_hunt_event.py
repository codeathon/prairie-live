"""Hunt-event re-arm matches pylon-track 1500 ms interval."""

from prey_gantry.hunt_event import HuntArmState, evaluate_hunt_event, note_hunt_flee_complete


def test_rising_edge_arms_once():
	st = HuntArmState()
	assert evaluate_hunt_event(True, 1_000, 1500, st)
	assert not evaluate_hunt_event(True, 2_000, 1500, st)


def test_rearm_after_interval_from_complete():
	st = HuntArmState()
	assert evaluate_hunt_event(True, 0, 1500, st)
	note_hunt_flee_complete(st, 10_000_000)
	# Sustained high: gated until 1.5 s after complete (not a new rising edge).
	assert not evaluate_hunt_event(True, 10_000_000 + 1_400_000_000, 1500, st)
	assert evaluate_hunt_event(True, 10_000_000 + 1_500_000_000, 1500, st)
