"""Save per-trial F0 / F1 / ΔF/F PNGs for mp-sync QC.

Layout:
  <images_dir>/<run_id>/t0000/{f0,f1,dff}.png
  <images_dir>/<run_id>/t0001/...

Why: PrairieView does not show which group fired; these dumps let you verify
stim timing and targeting offline. Uses stdlib PNG (no matplotlib required).
"""

from __future__ import annotations

import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


def new_run_id() -> str:
	"""UTC timestamp folder name so successive mp-sync runs do not collide."""
	return datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")


def run_dir(images_dir: str | Path, run_id: str | None = None) -> Path:
	root = Path(images_dir)
	rid = run_id or new_run_id()
	path = root / rid
	path.mkdir(parents=True, exist_ok=True)
	return path


def mean_stack(frames: list[np.ndarray]) -> np.ndarray:
	if not frames:
		raise ValueError("mean_stack needs at least one frame")
	return np.mean(np.stack(frames, axis=0), axis=0).astype(np.float64)


def to_u8(img: np.ndarray, *, p_lo: float = 1.0, p_hi: float = 99.0) -> np.ndarray:
	"""Percentile-stretch float/int frame → uint8 for display."""
	lo, hi = np.percentile(img, (p_lo, p_hi))
	if hi <= lo:
		hi = lo + 1.0
	scaled = (img.astype(np.float64) - lo) / (hi - lo)
	return np.clip(scaled * 255.0, 0, 255).astype(np.uint8)


def draw_point_rings(
	rgb: np.ndarray,
	points: list[dict],
	*,
	radius: int = 3,
	color: tuple[int, int, int] = (0, 255, 255),
) -> np.ndarray:
	"""Draw FOV-normalized point rings onto an RGB image (in place + return)."""
	h, w = rgb.shape[:2]
	r = max(int(radius), 2)
	for pt in points:
		cx = int(round(float(pt["x"]) * (w - 1)))
		cy = int(round(float(pt["y"]) * (h - 1)))
		yy, xx = np.ogrid[:h, :w]
		ring = (xx - cx) ** 2 + (yy - cy) ** 2
		# Hollow ring so the cell center stays visible.
		on = (ring <= (r + 1) ** 2) & (ring >= max(r - 1, 0) ** 2)
		rgb[on] = color
	return rgb


def gray_to_marked_rgb(
	gray: np.ndarray,
	points: list[dict],
	*,
	radius: int = 3,
	color: tuple[int, int, int] = (0, 255, 255),
) -> np.ndarray:
	u8 = to_u8(gray)
	rgb = np.stack([u8, u8, u8], axis=-1)
	return draw_point_rings(rgb, points, radius=radius, color=color)


def dff_rgb(f0: np.ndarray, f1: np.ndarray) -> np.ndarray:
	"""Signed ΔF/F as blue–white–red RGB (no matplotlib)."""
	eps = 1e-6
	dff = (f1 - f0) / (np.abs(f0) + eps)
	lim = float(np.percentile(np.abs(dff), 98))
	if lim <= 0:
		lim = 1.0
	t = np.clip(dff / lim, -1.0, 1.0)
	# t<0 → blue, t>0 → red, t=0 → white.
	r = np.where(t < 0, 1.0 + t, 1.0)
	g = 1.0 - np.abs(t)
	b = np.where(t > 0, 1.0 - t, 1.0)
	rgb = np.stack([r, g, b], axis=-1)
	return (np.clip(rgb, 0, 1) * 255.0).astype(np.uint8)


def write_png_rgb(path: str | Path, rgb: np.ndarray) -> None:
	"""Minimal RGB8 PNG writer (stdlib only)."""
	if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
		raise ValueError(f"expected HxWx3 uint8, got {rgb.shape} {rgb.dtype}")
	h, w, _ = rgb.shape
	raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

	def chunk(tag: bytes, data: bytes) -> bytes:
		return (
			struct.pack(">I", len(data))
			+ tag
			+ data
			+ struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
		)

	ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit RGB
	png = (
		b"\x89PNG\r\n\x1a\n"
		+ chunk(b"IHDR", ihdr)
		+ chunk(b"IDAT", zlib.compress(raw, 9))
		+ chunk(b"IEND", b"")
	)
	Path(path).write_bytes(png)


def save_trial_images(
	run_root: str | Path,
	*,
	trial_index: int,
	frames_f0: list[np.ndarray],
	frames_f1: list[np.ndarray],
	points: list[dict],
	radius: int = 3,
) -> dict[str, str]:
	"""
	Write <run_root>/tXXXX/{f0,f1,dff}.png.

	Returns absolute paths keyed by kind for JSONL; also includes trial_dir.
	"""
	trial_dir = Path(run_root) / f"t{trial_index:04d}"
	trial_dir.mkdir(parents=True, exist_ok=True)
	f0 = mean_stack(frames_f0)
	f1 = mean_stack(frames_f1)

	f0_rgb = gray_to_marked_rgb(f0, points, radius=radius)
	f1_rgb = gray_to_marked_rgb(f1, points, radius=radius)
	# Lime rings on ΔF/F so marks stay visible on the signed map.
	dff_out = draw_point_rings(
		dff_rgb(f0, f1),
		points,
		radius=radius,
		color=(0, 255, 0),
	)

	paths: dict[str, str] = {"trial_dir": str(trial_dir.resolve())}
	for kind, arr in (("f0", f0_rgb), ("f1", f1_rgb), ("dff", dff_out)):
		path = trial_dir / f"{kind}.png"
		write_png_rgb(path, arr)
		paths[kind] = str(path.resolve())
	return paths
