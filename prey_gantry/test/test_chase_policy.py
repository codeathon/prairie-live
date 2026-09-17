"""Soft keep-away policy: preferred gap, slight nudge, wall dodge."""

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


def test_close_nudge_away_not_flee():
	cfg = _cfg()
	# Ferret presses from the left — prey should ease right, no discrete flee.
	d = compute_chase_decision(
		_scene(400, 600, 550, 600, heading=0.0, speed=900.0),
		cfg.chase,
		cfg.camera.width_mm,
		cfg.camera.height_mm,
	)
	assert d.enable_motion
	assert not d.use_planned_flee
	assert d.reason in ("nudge_away", "press")
	assert d.target_vx_mm_s > 0
	assert abs(d.target_vx_mm_s) <= cfg.chase.max_engage_speed_mm_s + 1


def test_far_reels_back_to_keep_hunt_alive():
	cfg = _cfg()
	# Gap >> preferred — prey should move toward ferret (negative x here).
	d = compute_chase_decision(
		_scene(400, 600, 1400, 600, heading=0.0, speed=100.0),
		cfg.chase,
		cfg.camera.width_mm,
		cfg.camera.height_mm,
	)
	assert d.reason == "reel_in"
	assert d.target_vx_mm_s < 0


def test_corner_pushes_inward():
	cfg = _cfg()
	d = compute_chase_decision(
		_scene(400, 400, 40, 40, heading=225.0, speed=200.0),
		cfg.chase,
		cfg.camera.width_mm,
		cfg.camera.height_mm,
	)
	assert d.wall_push > 0.3
	assert d.target_vx_mm_s > 0
	assert d.target_vy_mm_s > 0
	assert d.reason == "edge_dodge"
