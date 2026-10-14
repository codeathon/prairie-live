"""Post-run FOV: tercile-colored rings, click to pick next-round XML."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from prairie_live.candidates import TERCILE_RGB, write_candidates_xml
from prairie_live.trial_images import to_u8
from prairie_live.viewer import orient_fov


def maybe_show_candidates(
	ranked: list[dict],
	*,
	meta: dict,
	fov: np.ndarray | None,
	out_xml: Path,
	show: bool,
) -> Path | None:
	if not show or not ranked:
		return None
	if os.environ.get("PRAIRIE_LIVE_NO_GUI"):
		return None
	backend = os.environ.get("MPLBACKEND") or ""
	try:
		import matplotlib

		backend = backend or str(matplotlib.get_backend())
	except Exception:
		pass
	if "agg" in backend.lower():
		return None
	try:
		return show_candidate_fov(ranked, meta=meta, fov=fov, out_xml=out_xml)
	except Exception as exc:
		print(f"candidate FOV skipped: {exc}")
		return None


def show_candidate_fov(
	ranked: list[dict],
	*,
	meta: dict,
	fov: np.ndarray | None,
	out_xml: Path,
) -> Path | None:
	import matplotlib.pyplot as plt

	bg = _fov_bg(fov)
	fig, ax = plt.subplots(figsize=(7.2, 6.4))
	ax.imshow(bg, origin="upper")
	ax.set_axis_off()
	ax.set_title("candidates  click=select  s=save XML  q=close", color="#ffee55")
	picked: set[str] = {r["id"] for r in ranked if r.get("tercile") == "top"}
	artists = []
	for r in ranked:
		cx = float(r["x"]) * (bg.shape[1] - 1)
		cy = float(r["y"]) * (bg.shape[0] - 1)
		rgb = tuple(c / 255.0 for c in TERCILE_RGB[r["tercile"]])
		ring = plt.Circle((cx, cy), 8, fill=False, ec=rgb, lw=2.0)
		ax.add_patch(ring)
		label = _label(r)
		txt = ax.text(cx + 10, cy, label, color=rgb, fontsize=8)
		artists.append((r["id"], ring, txt))
	_restyle(artists, picked)

	def on_click(event):
		if event.inaxes is not ax or event.xdata is None:
			return
		hit = _nearest(ranked, bg.shape, event.xdata, event.ydata)
		if hit is None:
			return
		if hit in picked:
			picked.discard(hit)
		else:
			picked.add(hit)
		_restyle(artists, picked)
		fig.canvas.draw_idle()

	def on_key(event):
		if event.key == "s":
			sel = [r for r in ranked if r["id"] in picked]
			if not sel:
				print("no points selected")
				return
			path = write_candidates_xml(sel, meta, out_xml)
			print(f"next-round XML → {path}")
			fig.suptitle(str(path), fontsize=9, color="#ffee55")
			fig.canvas.draw_idle()
		elif event.key in ("q", "escape"):
			plt.close(fig)

	fig.canvas.mpl_connect("button_press_event", on_click)
	fig.canvas.mpl_connect("key_press_event", on_key)
	plt.show()
	return out_xml if out_xml.is_file() else None


def _label(r: dict) -> str:
	if not r.get("stimulated"):
		return f"{r['id']}  no stim"
	return f"{r['id']}  p={r['min_power']}  ΔF/F={float(r['dff']):.3f}"


def _fov_bg(fov: np.ndarray | None) -> np.ndarray:
	if fov is None or fov.size == 0:
		gray = np.zeros((512, 512), dtype=np.uint8)
	else:
		img = np.asarray(fov)
		if img.ndim > 2:
			img = img[..., 0]
		gray = to_u8(orient_fov(img.astype(np.float64)))
	return np.stack([gray, gray, gray], axis=-1)


def _nearest(ranked, shape, x, y) -> str | None:
	h, w = int(shape[0]), int(shape[1])
	best_id = None
	best_d = 14.0 ** 2
	for r in ranked:
		cx = float(r["x"]) * (w - 1)
		cy = float(r["y"]) * (h - 1)
		d = (cx - x) ** 2 + (cy - y) ** 2
		if d < best_d:
			best_d = d
			best_id = str(r["id"])
	return best_id


def _restyle(artists, picked: set[str]) -> None:
	for pid, ring, txt in artists:
		ring.set_linewidth(3.2 if pid in picked else 1.6)
		txt.set_fontweight("bold" if pid in picked else "normal")
