"""Zaber-shaped API: RTT then trapezoid to an absolute point."""

from prey_gantry.zaber_sim import SimulatedGantry


def test_move_absolute_arrives_after_rtt_and_travel():
	g = SimulatedGantry(0, 0, 1000, 5000, rtt_ms=4, width_mm=2000, height_mm=2000)
	g.move_absolute(200, 0, wait_until_idle=False, velocity=1000, acceleration=5000)
	# Before RTT, still at origin.
	g.step(0.002, 0.002)
	assert g.x_mm < 5
	t = 0.002
	for _ in range(800):
		t += 0.001
		g.step(0.001, t)
	assert abs(g.x_mm - 200) < 3
	assert not g.is_busy()
