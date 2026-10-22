"""Turn PrairieView galvo line grabs (1×W / W×1) into a 2-D FOV."""

from __future__ import annotations

import numpy as np

# 1×1 frames are junk. A 512×1 live line is not.
_JUNK = 8
_FULL = 32


class ScanAssembler:
	def __init__(self) -> None:
		self._buf: np.ndarray | None = None
		self._row = 0

	def push(self, img: np.ndarray) -> np.ndarray | None:
		img = np.ascontiguousarray(img, dtype=np.uint16)
		img = _as_hw(img)
		if img is None:
			return None
		h, w = int(img.shape[0]), int(img.shape[1])
		if h >= _FULL and w >= _FULL:
			self._buf = None
			self._row = 0
			return img
		if max(h, w) < _JUNK:
			return None
		return self._add_strip(img, h, w)

	def _add_strip(self, img: np.ndarray, h: int, w: int) -> np.ndarray:
		chunk = img if h <= w else img.T
		rows, width = int(chunk.shape[0]), int(chunk.shape[1])
		if width < _JUNK:
			return img
		if self._buf is None or int(self._buf.shape[1]) != width:
			self._buf = np.zeros((width, width), dtype=np.uint16)
			self._row = 0
		height = int(self._buf.shape[0])
		if self._row >= height:
			self._row = 0
		n = min(rows, height - self._row)
		self._buf[self._row : self._row + n] = chunk[:n]
		self._row += n
		if self._row >= height:
			self._row = 0
		return self._buf


def _as_hw(img: np.ndarray) -> np.ndarray | None:
	if img.ndim == 1:
		return img.reshape(1, -1)
	if img.ndim == 2:
		return img
	squeezed = np.squeeze(img)
	if squeezed.ndim == 1:
		return squeezed.reshape(1, -1)
	if squeezed.ndim == 2:
		return squeezed
	return None
