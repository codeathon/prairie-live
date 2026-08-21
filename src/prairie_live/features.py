"""Named per-blob features so logistic weights are readable criteria."""

from __future__ import annotations

import numpy as np

from prairie_live.detect import FEATURE_NAMES, Blob


def extract_features(img: np.ndarray, blob: Blob, um_per_px: float) -> dict[str, float]:
	h, w = img.shape
	y, x, r = blob.y, blob.x, max(blob.radius_px, 1.0)
	disk, ring = _disk_and_ring(h, w, y, x, r)
	pix = img[disk]
	if pix.size == 0:
		pix = np.array([0.0], dtype=np.float32)
	ring_pix = img[ring]
	snr = _snr(pix, ring_pix)
	scale = um_per_px if um_per_px > 0 else 1.0
	r_um = r * scale
	ecc, circ = _shape(img, disk, y, x)
	cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
	half = max(min(h, w) / 2.0, 1.0)
	out = {
		"mean": float(pix.mean()),
		"max": float(pix.max()),
		"snr": snr,
		"area_um2": float(np.pi * r_um * r_um),
		"radius_um": float(r_um),
		"circularity": circ,
		"eccentricity": ecc,
		"dist_from_center": float(np.hypot((y - cy) / half, (x - cx) / half)),
		"edge_dist": float(min(x, y, w - 1 - x, h - 1 - y) / half),
		"x_frac": float(x / max(w - 1, 1)),
		"y_frac": float(y / max(h - 1, 1)),
	}
	return {k: float(out[k]) for k in FEATURE_NAMES}


def feature_vector(feats: dict[str, float]) -> np.ndarray:
	return np.array([feats[k] for k in FEATURE_NAMES], dtype=np.float64)


def _snr(pix: np.ndarray, ring: np.ndarray) -> float:
	if ring.size < 4:
		return float(pix.mean())
	return float((pix.mean() - ring.mean()) / (float(ring.std()) + 1e-6))


def _disk_and_ring(h, w, y, x, r):
	yy, xx = np.ogrid[0:h, 0:w]
	d2 = (yy - y) ** 2 + (xx - x) ** 2
	disk = d2 <= r * r
	ring = (d2 > r * r) & (d2 <= (2.0 * r) ** 2)
	return disk, ring


def _shape(img: np.ndarray, disk: np.ndarray, y: float, x: float) -> tuple[float, float]:
	# Second moments of pixels above local mean: eccentricity ~1 is a line.
	ys, xs = np.nonzero(disk)
	if ys.size < 5:
		return 0.0, 1.0
	vals = img[ys, xs]
	keep = vals >= float(vals.mean())
	ys, xs = ys[keep], xs[keep]
	if ys.size < 5:
		return 0.0, 1.0
	cy, cx = float(ys.mean()), float(xs.mean())
	yy, xx = ys - cy, xs - cx
	cov_yy = float(np.mean(yy * yy))
	cov_xx = float(np.mean(xx * xx))
	cov_xy = float(np.mean(yy * xx))
	tr = cov_yy + cov_xx
	det = cov_yy * cov_xx - cov_xy * cov_xy
	disc = max(tr * tr - 4.0 * det, 0.0)
	l1 = 0.5 * (tr + np.sqrt(disc))
	l2 = 0.5 * (tr - np.sqrt(disc))
	if l1 <= 1e-12:
		return 0.0, 1.0
	# l2/l1 ~ 1 for a round soma, ~0 for a streak (vessel/dendrite).
	circ = float(max(min(l2 / l1, 1.0), 0.0))
	ecc = float(np.sqrt(max(1.0 - circ, 0.0)))
	return ecc, circ
