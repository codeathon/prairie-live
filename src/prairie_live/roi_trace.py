"""Mean intensity of a live-frame ROI, plus a rolling sample buffer.

Pixel-space ROIs (not FOV-normalized) so the viewer can drag boxes and
click disks on the imshow axes. Means stay in raw PMT counts — autoscaling
would hide the intensity changes this plot is for.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class RectRoi:
	"""Axis-aligned box in pixel coordinates (any corner order)."""

	x0: float
	y0: float
	x1: float
	y1: float


@dataclass(frozen=True)
class DiskRoi:
	"""Circle in pixel coordinates; radius is pixels."""

	cx: float
	cy: float
	radius: int


def roi_mean(frame: np.ndarray, roi: RectRoi | DiskRoi | None) -> float | None:
	"""Mean of `roi`, or the full frame when roi is None. None if empty/OOB."""
	img = np.asarray(frame)
	if img.ndim != 2:
		raise ValueError("frame must be 2-D")
	if roi is None:
		return float(img.astype(np.float32).mean())
	if isinstance(roi, RectRoi):
		return _rect_mean(img, roi)
	return _disk_mean_px(img, roi)


def roi_label(roi: RectRoi | DiskRoi | None) -> str:
	"""Short status string for the viewer title."""
	if roi is None:
		return "full FOV"
	if isinstance(roi, RectRoi):
		w = abs(roi.x1 - roi.x0)
		h = abs(roi.y1 - roi.y0)
		return f"rect {w:.0f}×{h:.0f}px"
	return f"disk r={roi.radius}px @ ({roi.cx:.0f},{roi.cy:.0f})"


def _rect_mean(img: np.ndarray, roi: RectRoi) -> float | None:
	h, w = img.shape
	x0 = int(np.floor(min(roi.x0, roi.x1)))
	x1 = int(np.ceil(max(roi.x0, roi.x1)))
	y0 = int(np.floor(min(roi.y0, roi.y1)))
	y1 = int(np.ceil(max(roi.y0, roi.y1)))
	x0 = max(x0, 0)
	y0 = max(y0, 0)
	x1 = min(x1, w)
	y1 = min(y1, h)
	if x1 <= x0 or y1 <= y0:
		return None
	return float(img[y0:y1, x0:x1].astype(np.float32).mean())


def _disk_mean_px(img: np.ndarray, roi: DiskRoi) -> float | None:
	h, w = img.shape
	r = int(roi.radius)
	if r < 1:
		raise ValueError("disk radius must be >= 1")
	cx = int(round(roi.cx))
	cy = int(round(roi.cy))
	# Entire disk misses the frame → not scoreable on this image.
	if cx + r < 0 or cy + r < 0 or cx - r >= w or cy - r >= h:
		return None
	yy, xx = np.ogrid[:h, :w]
	mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= r ** 2
	if not np.any(mask):
		return None
	return float(img[mask].astype(np.float32).mean())


class IntensityBuffer:
	"""Newest-on-the-right rolling mean-intensity samples."""

	def __init__(self, n: int):
		if n < 1:
			raise ValueError("n must be >= 1")
		self.n = n
		self._data = np.zeros(n, dtype=np.float32)
		self._i = 0
		self._filled = 0

	def push(self, value: float) -> None:
		self._data[self._i % self.n] = np.float32(value)
		self._i += 1
		if self._filled < self.n:
			self._filled += 1

	def clear(self) -> None:
		# Changing ROI mid-session would mix two traces on one y-scale.
		self._i = 0
		self._filled = 0
		self._data[:] = 0

	def __len__(self) -> int:
		return self._filled

	def as_array(self) -> np.ndarray:
		"""Chronological (oldest first), shape (T,)."""
		if self._filled < self.n:
			return self._data[: self._filled].copy()
		start = self._i % self.n
		return np.concatenate((self._data[start:], self._data[:start]))
