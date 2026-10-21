import numpy as np

from prairie_live.detect import FEATURE_NAMES, Blob, FrameBuffer, detect_blobs, um_to_radius_px
from prairie_live.features import extract_features


def _gaussians(h=128, w=128):
	yy, xx = np.mgrid[0:h, 0:w]
	img = np.full((h, w), 8.0)
	centers = ((40, 40, 5.0, 300.0), (40, 90, 5.0, 280.0), (90, 60, 6.0, 260.0))
	for y, x, sig, amp in centers:
		img += amp * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2.0 * sig * sig))
	return img, centers


def test_um_fallback_treats_um_as_pixels():
	assert um_to_radius_px(5, 20, 0) == (5.0, 20.0)
	lo, hi = um_to_radius_px(5, 20, 0.5)
	assert abs(lo - 10.0) < 1e-9
	assert abs(hi - 40.0) < 1e-9


def test_detect_finds_synthetic_blobs():
	img, centers = _gaussians()
	found = detect_blobs(img, min_radius=4.0, max_radius=12.0)
	assert len(found) >= 3
	for y0, x0, sig, _ in centers:
		ok = any((p[0] - y0) ** 2 + (p[1] - x0) ** 2 < (2 * sig) ** 2 for p in found)
		assert ok, f"missed blob at {(y0, x0)}"


def test_features_named_and_finite():
	img, _ = _gaussians()
	b = Blob(id=1, y=40, x=40, radius_px=6.0, response=1.0)
	feats = extract_features(img, b, um_per_px=1.0)
	assert list(feats) == list(FEATURE_NAMES)
	assert all(np.isfinite(v) for v in feats.values())
	assert feats["snr"] > 0
	assert feats["radius_um"] == 6.0


def test_frame_buffer_mean():
	buf = FrameBuffer(n=3)
	a = np.ones((4, 4), dtype=np.uint16)
	b = np.full((4, 4), 3, dtype=np.uint16)
	buf.push(a)
	buf.push(b)
	m = buf.mean()
	assert m.shape == (4, 4)
	assert abs(float(m[0, 0]) - 2.0) < 1e-6
