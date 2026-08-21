"""Bin live frames into a spatial grid of mean intensities.

Crop leftover edge pixels instead of stretching so each subplot maps
to a real FOV rectangle. Means stay in raw PMT counts — autoscaling
would hide the intensity changes this display is for.
"""

from __future__ import annotations

import numpy as np


def tile_means(frame: np.ndarray, grid: int = 8) -> np.ndarray:
	"""Return (grid, grid) mean intensity; top-left tile is FOV top-left."""
	if grid < 1:
		raise ValueError("grid must be >= 1")
	img = np.asarray(frame, dtype=np.float32)
	if img.ndim != 2:
		raise ValueError("frame must be 2-D")
	h, w = img.shape
	th = (h // grid) * grid
	tw = (w // grid) * grid
	if th == 0 or tw == 0:
		return np.zeros((grid, grid), dtype=np.float32)
	crop = img[:th, :tw]
	rh, rw = th // grid, tw // grid
	tiles = crop.reshape(grid, rh, grid, rw)
	return tiles.mean(axis=(1, 3))


class TraceBuffer:
	"""Newest-on-the-right rolling stack of tile-mean frames."""

	def __init__(self, n: int, grid: int = 8):
		if n < 1:
			raise ValueError("n must be >= 1")
		self.n = n
		self.grid = grid
		self._data = np.zeros((n, grid, grid), dtype=np.float32)
		self._i = 0
		self._filled = 0

	def push(self, tiles: np.ndarray) -> None:
		row = np.asarray(tiles, dtype=np.float32)
		if row.shape != (self.grid, self.grid):
			raise ValueError(f"expected {(self.grid, self.grid)}, got {row.shape}")
		self._data[self._i % self.n] = row
		self._i += 1
		if self._filled < self.n:
			self._filled += 1

	def __len__(self) -> int:
		return self._filled

	def as_array(self) -> np.ndarray:
		"""Chronological (oldest first), shape (T, grid, grid)."""
		if self._filled < self.n:
			return self._data[: self._filled].copy()
		start = self._i % self.n
		return np.concatenate((self._data[start:], self._data[:start]), axis=0)
