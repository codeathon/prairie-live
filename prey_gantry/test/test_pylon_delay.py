"""Camera pipeline delay: delivered ferret lags the pointer."""

from prey_gantry.engine import HuntSim
from prey_gantry.tracking_frame import TrialPhase


def test_camera_ferret_lags_pointer():
	sim = HuntSim()
	sim.trial = TrialPhase.running
	# Establish a delivered frame at the spawn pose.
	for _ in range(12):
		sim.step(0.001)
	spawn_x = sim.true_ferret.x_mm
	assert sim.controller._latest is not None
	sim.set_pointer(100.0, 100.0)
	# Shorter than grab_to_track (~5.7 ms): still the old pose.
	for _ in range(3):
		sim.step(0.001)
	assert abs(sim.controller._latest.ferret.x_mm - spawn_x) < 2.0
	for _ in range(20):
		sim.step(0.001)
	assert abs(sim.controller._latest.ferret.x_mm - 100.0) < 1.0
