"""Post-run FOV + ranked table: click either side, Save XML for next round."""

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
		return show_candidate_picker(ranked, meta=meta, fov=fov, out_xml=out_xml)
	except Exception as exc:
		print(f"candidate picker skipped: {exc}")
		try:
			return show_candidate_fov(ranked, meta=meta, fov=fov, out_xml=out_xml)
		except Exception as exc2:
			print(f"candidate FOV skipped: {exc2}")
			return None


def default_picked_ids(ranked: list[dict]) -> set[str]:
	"""Top tercile starts selected."""
	return {str(r["id"]) for r in ranked if r.get("tercile") == "top"}


def toggle_picked(picked: set[str], pid: str) -> set[str]:
	out = set(picked)
	sid = str(pid)
	if sid in out:
		out.discard(sid)
	else:
		out.add(sid)
	return out


def selected_rows(ranked: list[dict], picked: set[str]) -> list[dict]:
	want = {str(p) for p in picked}
	return [r for r in ranked if str(r["id"]) in want]


def table_cells(ranked: list[dict]) -> list[dict]:
	rows: list[dict] = []
	for r in ranked:
		pwr = "-" if r.get("min_power") is None else f"{r['min_power']:g}"
		dff = "-" if r.get("dff") is None else f"{float(r['dff']):.3f}"
		rows.append(
			{
				"id": str(r["id"]),
				"min_power": pwr,
				"dff": dff,
				"tercile": str(r.get("tercile") or "none"),
			}
		)
	return rows


def save_status(n_selected: int, path: Path | None) -> str:
	if path is None:
		return f"{n_selected} selected"
	return f"{n_selected} selected → {path}"


def show_candidate_picker(
	ranked: list[dict],
	*,
	meta: dict,
	fov: np.ndarray | None,
	out_xml: Path,
	parent=None,
) -> Path | None:
	"""Split window: FOV left, ranked table right, Save XML button."""
	import tkinter as tk
	from tkinter import ttk

	from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
	from matplotlib.figure import Figure
	from matplotlib.patches import Circle

	own_root = parent is None
	if own_root:
		root = tk.Tk()
		root.withdraw()
		parent = root
	win = tk.Toplevel(parent)
	win.title("candidates — click FOV or table, then Save XML")
	win.geometry("980x620")

	picked = default_picked_ids(ranked)
	saved: list[Path] = []

	bg = _fov_bg(fov)
	fig = Figure(figsize=(5.4, 5.2), dpi=100)
	ax = fig.add_subplot(111)
	left = ttk.Frame(win)
	right = ttk.Frame(win, padding=8)
	left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
	right.pack(side=tk.RIGHT, fill=tk.BOTH)

	canvas = FigureCanvasTkAgg(fig, master=left)
	canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

	cols = ("id", "min_power", "dff", "tercile")
	tree = ttk.Treeview(right, columns=cols, show="headings", height=18, selectmode="extended")
	for col, w, hd in (
		("id", 50, "id"),
		("min_power", 80, "min power"),
		("dff", 70, "ΔF/F"),
		("tercile", 70, "tercile"),
	):
		tree.heading(col, text=hd)
		tree.column(col, width=w, anchor=tk.CENTER)
	tree.pack(fill=tk.BOTH, expand=True)
	for cell in table_cells(ranked):
		tree.insert("", tk.END, iid=cell["id"], values=(cell["id"], cell["min_power"], cell["dff"], cell["tercile"]))

	status = tk.StringVar(value=save_status(len(picked), None))
	ttk.Label(right, textvariable=status, wraplength=320).pack(fill=tk.X, pady=(8, 4))
	btns = ttk.Frame(right)
	btns.pack(fill=tk.X)

	artists: list = []
	syncing = {"on": False}

	def _draw_fov() -> None:
		ax.clear()
		ax.imshow(bg, origin="upper")
		ax.set_axis_off()
		ax.set_title("click a ring  ·  green=top  yellow=mid", color="#444")
		artists.clear()
		h, w = int(bg.shape[0]), int(bg.shape[1])
		for r in ranked:
			cx = float(r["x"]) * (w - 1)
			cy = float(r["y"]) * (h - 1)
			rgb = tuple(c / 255.0 for c in TERCILE_RGB[r["tercile"]])
			ring = Circle((cx, cy), 8, fill=False, ec=rgb, lw=2.0)
			ax.add_patch(ring)
			txt = ax.text(cx + 10, cy, _label(r), color=rgb, fontsize=8)
			artists.append((str(r["id"]), ring, txt))
		_restyle(artists, picked)
		canvas.draw_idle()

	def _sync_table() -> None:
		syncing["on"] = True
		tree.selection_set(list(picked))
		status.set(save_status(len(picked), saved[0] if saved else None))
		syncing["on"] = False

	def _set_picked(new: set[str]) -> None:
		picked.clear()
		picked.update(new)
		_restyle(artists, picked)
		canvas.draw_idle()
		_sync_table()

	def on_fov_click(event) -> None:
		if event.inaxes is not ax or event.xdata is None:
			return
		hit = _nearest(ranked, bg.shape, event.xdata, event.ydata)
		if hit is None:
			return
		_set_picked(toggle_picked(picked, hit))

	def on_table_click(event) -> None:
		if syncing["on"]:
			return
		item = tree.identify_row(event.y)
		if not item:
			return
		_set_picked(toggle_picked(picked, item))

	def do_top() -> None:
		_set_picked(default_picked_ids(ranked))

	def do_clear() -> None:
		_set_picked(set())

	def do_save() -> None:
		sel = selected_rows(ranked, picked)
		if not sel:
			status.set("no points selected")
			return
		path = write_candidates_xml(sel, meta, out_xml)
		saved.clear()
		saved.append(path)
		status.set(save_status(len(sel), path))
		print(f"next-round XML → {path}")

	fig.canvas.mpl_connect("button_press_event", on_fov_click)
	tree.bind("<Button-1>", on_table_click)
	ttk.Button(btns, text="Select top third", command=do_top).pack(fill=tk.X, pady=2)
	ttk.Button(btns, text="Clear", command=do_clear).pack(fill=tk.X, pady=2)
	ttk.Button(btns, text="Save XML", command=do_save).pack(fill=tk.X, pady=2)

	_draw_fov()
	_sync_table()

	def on_close() -> None:
		win.destroy()
		if own_root:
			parent.destroy()

	win.protocol("WM_DELETE_WINDOW", on_close)
	if own_root:
		parent.wait_window(win)
	else:
		parent.wait_window(win)
	return saved[0] if saved else (out_xml if out_xml.is_file() else None)


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
