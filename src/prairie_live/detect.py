"""LoG blob proposals on a mean-projected live buffer.

v1 skips motion correction: a 2 s mean is enough to find somata on a
stable FOV, and labeled IDs must survive a later re-detect.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

FEATURE_NAMES = (
	"mean",
	"max",
	"snr",
	"area_um2",
	"radius_um",
	"circularity",
	"eccentricity",
	"dist_from_center",
	"edge_dist",
	"x_frac",
	"y_frac",
)


@dataclass
class Blob:
	id: int
	y: float
	x: float
	radius_px: float
	response: float
	features: dict[str, float] = field(default_factory=dict)
	label: int | None = None
	p_hat: float | None = None


class FrameBuffer:
	def __init__(self, n: int = 23):
		# ~2 s at 11.4 fps; longer would smear a drifting FOV.
		self.n = n
		self._frames: list[np.ndarray] = []

	def push(self, frame: np.ndarray) -> None:
		self._frames.append(np.asarray(frame, dtype=np.float32))
		if len(self._frames) > self.n:
			self._frames.pop(0)

	def mean(self) -> np.ndarray | None:
		if not self._frames:
			return None
		return np.mean(self._frames, axis=0)

	def __len__(self) -> int:
		return len(self._frames)


def um_to_radius_px(min_um: float, max_um: float, um_per_px: float) -> tuple[float, float]:
	# Mock / failed GetState: treat the um window as pixels so detect still runs.
	scale = um_per_px if um_per_px > 0 else 1.0
	return min_um / scale, max_um / scale


def detect_blobs(
	img: np.ndarray,
	min_radius: float,
	max_radius: float,
	n_sigma: int = 8,
	rel_thresh: float = 0.2,
) -> list[tuple[float, float, float, float]]:
	"""Return (y, x, radius_px, response) brightest-first, NMS'd."""
	norm = _zscore(img)
	h, w = norm.shape
	min_s = max(min_radius / np.sqrt(2.0), 1.0)
	max_s = max(max_radius / np.sqrt(2.0), min_s + 0.5)
	resp, sig = _scale_space(norm, min_s, max_s, n_sigma)
	peaks = _local_maxima(resp, rel_thresh)
	out = []
	for y, x, val in peaks:
		sigma = float(sig[int(y), int(x)])
		r = sigma * np.sqrt(2.0)
		if r < min_radius * 0.7 or r > max_radius * 1.3:
			continue
		if not (1 <= y < h - 1 and 1 <= x < w - 1):
			continue
		out.append((y, x, r, val))
	out.sort(key=lambda t: t[3], reverse=True)
	return _nms(out, min_dist=min_radius)


def _zscore(img: np.ndarray) -> np.ndarray:
	x = np.asarray(img, dtype=np.float32)
	return (x - x.mean()) / (float(x.std()) + 1e-6)


def _scale_space(img, min_s, max_s, n_sigma):
	from scipy.ndimage import gaussian_laplace

	best = np.full(img.shape, -np.inf, dtype=np.float32)
	sig = np.zeros(img.shape, dtype=np.float32)
	for sigma in np.linspace(min_s, max_s, n_sigma):
		# Bright blobs are negative LoG; sigma^2 makes scales comparable.
		log = -gaussian_laplace(img, sigma=float(sigma)) * (sigma ** 2)
		better = log > best
		best[better] = log[better]
		sig[better] = sigma
	return best, sig


def _local_maxima(resp: np.ndarray, rel_thresh: float):
	from scipy.ndimage import maximum_filter

	peak = maximum_filter(resp, size=3)
	cut = rel_thresh * float(np.max(resp))
	mask = (resp == peak) & (resp >= cut)
	ys, xs = np.nonzero(mask)
	return [(float(y), float(x), float(resp[y, x])) for y, x in zip(ys, xs)]


def _nms(peaks, min_dist: float):
	keep = []
	for y, x, r, val in peaks:
		if all((y - ky) ** 2 + (x - kx) ** 2 >= min_dist ** 2 for ky, kx, _, _ in keep):
			keep.append((y, x, r, val))
	return keep
