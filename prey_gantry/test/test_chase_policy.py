"""Chase policy gates and 2D flee direction."""

from prey_gantry.chase_policy import compute_chase_decision, fill_tracking_derived
from prey_gantry.config import load_sim_config
from prey_gantry.tracking_frame import TrackingFrame, TrackingQuality, TrackState, TrialPhase


def _cfg():
	return load_sim_config()


def _scene(fx, fy, px, py, heading=0.0, speed=800.0, phase=TrialPhase.running):
	frame = TrackingFrame(
		host_time_ns=1_000_000_000,
		ferret=TrackState(fx, fy, speed, heading, True),
		prey=TrackState(px, py, 0.0, 0.0, True),
		quality=TrackingQuality(1.0, 1.0),
		trial_phase=phase,
	)
	fill_tracking_derived(frame)
	return frame


def test_idle_when_trial_not_running():
	cfg = _cfg()
	d = compute_chase_decision(
		_scene(100, 100, 200, 100, phase=TrialPhase.warmup),
		cfg.chase,
		cfg.camera.width_mm,
		cfg.camera.height_mm,
	)
	assert d.reason == "trial_not_running"
	assert not d.enable_motion


def test_close_head_on_is_flee():
	cfg = _cfg()
	# Ferret at left, heading right (0°), prey to the right — in cone, close, approaching.
	d = compute_chase_decision(
		_scene(400, 600, 700, 600, heading=0.0, speed=900.0),
		cfg.chase,
		cfg.camera.width_mm,
		cfg.camera.height_mm,
	)
	assert d.enable_motion
	assert d.threat > cfg.chase.flee_threat_threshold
	assert d.use_planned_flee
	assert d.reason == "flee_plan"
	assert d.flee_x_mm > 700.0


def test_behind_ferret_low_cone_threat():
	cfg = _cfg()
	# Prey is behind a right-facing ferret.
	d = compute_chase_decision(
		_scene(800, 600, 200, 600, heading=0.0, speed=900.0),
		cfg.chase,
		cfg.camera.width_mm,
		cfg.camera.height_mm,
	)
	assert d.cone_threat < 0.5
	assert not d.use_planned_flee
