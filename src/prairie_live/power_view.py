"""Per-power FOV boards: ΔF/F background, fired group, stimulated circles."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

from prairie_live.trial_images import to_u8
from prairie_live.viewer import orient_fov


def unique_powers(powers: list[float]) -> list[float]:
	out: list[float] = []
	seen: set[float] = set()
	for p in powers:
		fp = float(p)
		if fp in seen:
			continue
		seen.add(fp)
		out.append(fp)
	return out


def board_state(
	row: dict[str, Any],
	points: list[dict[str, Any]],
	*,
	stim_dff_min: float = 0.0,
	n_trials: int | None = None,
) -> dict[str, Any]:
	"""Overlay state for the window matching this trial's power."""
	fired = [str(i) for i in (row.get("point_ids") or [])]
	fired_set = set(fired)
	scores = row.get("point_scores") if isinstance(row.get("point_scores"), dict) else {}
	marks: list[dict[str, Any]] = []
	for pt in points:
		pid = str(pt["id"])
		raw = scores.get(pid)
		dff = float(raw) if raw is not None else None
		marks.append(
			{
				"id": pid,
				"x": float(pt["x"]),
				"y": float(pt["y"]),
				"fired": pid in fired_set,
				"changed": dff is not None and dff >= float(stim_dff_min),
				"dff": dff,
			}
		)
	ti = row.get("trial_index")
	pwr = float(row["power"]) if row.get("power") is not None else 0.0
	paths = row.get("image_paths") or {}
	prog = f"trial {ti}"
	if n_trials is not None:
		prog = f"trial {ti}/{n_trials}"
	pts = ",".join(fired) if fired else "-"
	return {
		"power": pwr,
		"trial_index": ti,
		"fired_ids": fired,
		"marks": marks,
		"image_path": paths.get("dff") or paths.get("f1") or paths.get("f0"),
		"title": f"power {pwr:g} · {prog} · pts {pts}",
	}


def load_board_bg(state: dict[str, Any]) -> np.ndarray:
	"""ΔF/F (or f1/f0) PNG, oriented like the live PrairieView FOV."""
	path = state.get("image_path")
	if path and Path(path).is_file():
		from matplotlib.image import imread

		arr = np.asarray(imread(path))
		return _orient_display(arr)
	gray = np.zeros((512, 512), dtype=np.uint8)
	return np.stack([gray, gray, gray], axis=-1)


def _orient_display(img: np.ndarray) -> np.ndarray:
	arr = np.asarray(img)
	if arr.ndim == 2:
		out = orient_fov(arr.astype(np.float64))
		if out.dtype != np.uint8:
			if out.max() <= 1.0:
				out = (np.clip(out, 0, 1) * 255).astype(np.uint8)
			else:
				out = to_u8(out)
		return np.stack([out, out, out], axis=-1)
	core = arr[..., :3]
	return np.ascontiguousarray(
		np.stack([orient_fov(core[..., c]) for c in range(3)], axis=-1)
	)


def mark_style(mark: dict[str, Any]) -> dict[str, Any]:
	"""Artist knobs for tests + drawers (cyan fired, lime changed)."""
	if mark.get("changed"):
		label = f"{mark['id']}  ΔF/F={float(mark['dff']):.2f}"
		return {
			"kind": "changed",
			"edge": "#39ff14",
			"lw": 2.8,
			"radius": 12.0,
			"label": label,
			"color": "#39ff14",
		}
	if mark.get("fired"):
		return {
			"kind": "fired",
			"edge": "#00e5ff",
			"lw": 2.4,
			"radius": 9.0,
			"label": str(mark["id"]),
			"color": "#00e5ff",
		}
	return {
		"kind": "pool",
		"edge": "#9a9a9a",
		"lw": 1.0,
		"radius": 5.0,
		"label": str(mark["id"]),
		"color": "#9a9a9a",
	}


def render_board_png(state: dict[str, Any]) -> bytes:
	"""RGB PNG for a power tile (web grid)."""
	from prairie_live.trial_images import draw_point_rings, png_bytes_rgb

	bg = load_board_bg(state)
	if bg.dtype != np.uint8:
		if bg.max() <= 1.0:
			bg = (np.clip(bg, 0, 1) * 255).astype(np.uint8)
		else:
			bg = to_u8(bg.astype(np.float64))
	if bg.ndim == 2:
		bg = np.stack([bg, bg, bg], axis=-1)
	bg = np.ascontiguousarray(bg[..., :3])
	pool = [m for m in state.get("marks") or [] if not m.get("fired") and not m.get("changed")]
	fired = [m for m in state.get("marks") or [] if m.get("fired") and not m.get("changed")]
	changed = [m for m in state.get("marks") or [] if m.get("changed")]
	draw_point_rings(bg, pool, radius=4, color=(154, 154, 154))
	draw_point_rings(bg, fired, radius=6, color=(0, 229, 255))
	draw_point_rings(bg, changed, radius=8, color=(57, 255, 20))
	return png_bytes_rgb(bg)


def gui_allowed() -> bool:
	if os.environ.get("PRAIRIE_LIVE_NO_GUI"):
		return False
	backend = os.environ.get("MPLBACKEND") or ""
	if "agg" in backend.lower():
		return False
	return True


class PowerBoards:
	"""One matplotlib window per unique power; update the matching board."""

	def __init__(
		self,
		powers: list[float],
		points: list[dict[str, Any]],
		*,
		stim_dff_min: float = 0.0,
		n_trials: int | None = None,
		show: bool = True,
	) -> None:
		self.powers = unique_powers(powers)
		self.points = points
		self.stim_dff_min = float(stim_dff_min)
		self.n_trials = n_trials
		self.states: dict[float, dict[str, Any]] = {}
		self.show = bool(show) and gui_allowed()
		self._figs: dict[float, tuple[Any, Any]] = {}
		if self.show:
			self._open()

	def _open(self) -> None:
		import matplotlib.pyplot as plt

		plt.ion()
		for p in self.powers:
			fig, ax = plt.subplots(figsize=(5.6, 5.2))
			mgr = getattr(fig.canvas, "manager", None)
			if mgr is not None:
				mgr.set_window_title(f"power {p:g}")
			ax.set_axis_off()
			ax.set_title(f"power {p:g}  waiting…", color="#ffee55")
			self._figs[p] = (fig, ax)
			fig.show()

	def update(self, row: dict[str, Any]) -> dict[str, Any]:
		st = board_state(
			row,
			self.points,
			stim_dff_min=self.stim_dff_min,
			n_trials=self.n_trials,
		)
		self.states[float(st["power"])] = st
		if self.show:
			self._redraw(st)
		return st

	def _redraw(self, st: dict[str, Any]) -> None:
		key = float(st["power"])
		pair = self._figs.get(key)
		if pair is None:
			return
		fig, ax = pair
		ax.clear()
		bg = load_board_bg(st)
		ax.imshow(bg, origin="upper")
		h, w = int(bg.shape[0]), int(bg.shape[1])
		from matplotlib.patches import Circle

		for mark in st["marks"]:
			style = mark_style(mark)
			cx = float(mark["x"]) * (w - 1)
			cy = float(mark["y"]) * (h - 1)
			ax.add_patch(
				Circle(
					(cx, cy),
					style["radius"],
					fill=False,
					ec=style["edge"],
					lw=style["lw"],
				)
			)
			ax.text(cx + 11, cy, style["label"], color=style["color"], fontsize=8)
		ax.set_axis_off()
		ax.set_title(st["title"], color="#ffee55")
		fig.canvas.draw_idle()
		fig.canvas.flush_events()

	def close(self) -> None:
		if not self._figs:
			return
		import matplotlib.pyplot as plt

		for fig, _ax in self._figs.values():
			plt.close(fig)
		self._figs.clear()
