"""Live FOV (rolling stack mean) + traces. Keys: t/a/l/q; drag box / click disk ROI, r reset."""

from __future__ import annotations

import argparse
import time

import numpy as np

from prairie_live.roi_trace import DiskRoi, IntensityBuffer, RectRoi, roi_label, roi_mean
from prairie_live.trial_images import FrameBuffer, disk_mean, draw_point_rings, to_u8


def orient_fov(frame: np.ndarray) -> np.ndarray:
	"""PrairieLink FOV → PrairieView: 90° clockwise, then mirror left/right."""
	return np.ascontiguousarray(np.fliplr(np.rot90(frame, -1)))


def _avg_n(avg_s: float, interval_ms: int) -> int:
	# Same window math as f0_s at this poll rate; at least two frames.
	return max(int(round(avg_s * 1000.0 / max(interval_ms, 1))), 2)


def _make_backend(args):
	if args.relay:
		from prairie_live.relay_client import RelayClient

		host, _, port = args.relay.partition(":")
		client = RelayClient(host, int(port or 25100), args.channel)
		client.connect()
		return client
	if args.mock:
		return MockScope(args.channel)
	from prairie_live.com_backend import PrairieCom

	client = PrairieCom(args.host, args.password)
	client.connect()
	return client


class MockScope:
	"""Synthetic frames so the viewer can be tested off the microscope."""

	def __init__(self, channel: int = 1):
		self.channel = channel
		self._t = 0.0

	def connect(self) -> None:
		pass

	def disconnect(self) -> None:
		pass

	def get_frame(self, channel: int = 1) -> np.ndarray:
		self._t += 0.05
		y, x = np.mgrid[0:256, 0:256]
		z = np.sin((x + self._t * 40) / 18) + np.cos((y - self._t * 25) / 22)
		return ((z - z.min()) * 4000).astype(np.uint16)

	def start_tseries(self):
		print("mock T-series")
		return {"ok": True, "cmd": "tseries"}

	def abort(self):
		print("mock abort")
		return {"ok": True, "cmd": "abort"}

	def start_live(self):
		print("mock live")
		return {"ok": True, "cmd": "live"}

	def get_state(self, key: str, index=None, subindex=None):
		return {
			"ok": True,
			"cmd": "get_state",
			"key": key,
			"index": index,
			"subindex": subindex,
			"value": "mock",
		}

	def get_motor_position(self, axis: str, device=None):
		return {
			"ok": True,
			"cmd": "get_motor_position",
			"axis": axis.upper(),
			"value": 0.0,
		}


def _load_view_points(series: str | None, config: str | None, disk_radius: int | None):
	"""Prairie has no GetMarkPoints — coords come from the same xml/gpl as mp-sync."""
	cfg: dict = {}
	if config:
		from prairie_live.experiment_config import load_experiment

		cfg = load_experiment(config)
	path = series or cfg.get("series")
	radius = int(disk_radius if disk_radius is not None else cfg.get("disk_radius") or 3)
	if not path:
		return [], radius
	from prairie_live.markpoints import extract_unique_points, load_mark_points_file

	return extract_unique_points(load_mark_points_file(str(path))), radius


def _flash_overlay(avg: np.ndarray, latest: np.ndarray) -> np.ndarray | None:
	"""Local positive delta as uint8. Global brightness jumps do not count.

	Why: a 10 ms photostim spot is washed out of the rolling mean; paint it
	red on the FOV so firing points is visible even without matching XML.
	"""
	if latest.shape != avg.shape:
		return None
	pos = np.clip(latest.astype(np.float64) - avg.astype(np.float64), 0.0, None)
	pos -= float(np.median(pos))
	pos = np.clip(pos, 0.0, None)
	if float(pos.max()) < 5.0:
		return None
	return to_u8(pos, p_lo=0.0, p_hi=99.5)


def _fov_rgb(avg: np.ndarray, points: list, radius: int, latest=None) -> np.ndarray:
	# Same stretch as f0.png; rings use the same cyan overlay as trial dumps.
	u8 = to_u8(avg)
	rgb = np.stack([u8, u8, u8], axis=-1)
	heat = _flash_overlay(avg, latest) if latest is not None else None
	if heat is not None:
		# Red = this-frame flash; gray rolling mean stays underneath.
		rgb[:, :, 0] = np.maximum(rgb[:, :, 0], heat)
	if points:
		draw_point_rings(rgb, points, radius=max(int(radius), 2))
	return rgb


def _fit_fov_axes(im, h: int, w: int) -> None:
	# First imshow is a 32² placeholder; set_data(512²) does not expand xlim,
	# so the panel stayed a dark crop of the corner (photostim never visible).
	ax = getattr(im, "axes", None)
	if ax is None:
		return
	ax.set_xlim(0, w)
	ax.set_ylim(h, 0)


def _set_trace_ylim(ax, buffers: dict) -> None:
	chunks = [b.as_array() for b in buffers.values() if len(b)]
	if not chunks:
		return
	y = np.concatenate(chunks)
	lo, hi = float(y.min()), float(y.max())
	if hi <= lo:
		hi = lo + 1.0
	pad = 0.05 * (hi - lo)
	ax.set_ylim(lo - pad, hi + pad)


def _draw_line(ax, line, buf: IntensityBuffer, dt: float) -> None:
	y = buf.as_array()
	if y.size == 0:
		return
	x = (np.arange(y.size) - (y.size - 1)) * dt
	line.set_data(x, y)
	ax.set_xlim(float(x[0]) if y.size > 1 else -dt, 0.0)


def _push_scalar(traces: dict, name: str, value: float) -> None:
	if name not in traces["buffers"]:
		return
	traces["buffers"][name].push(float(value))
	_draw_line(traces["ax"], traces["lines"][name], traces["buffers"][name], traces["dt"])


def _update_traces(frame: np.ndarray, traces: dict) -> None:
	# fov/p99 always move when ANY photostim hits the image, even if the
	# XML disks are a different set of points than the ones just fired.
	_push_scalar(traces, "fov", float(np.mean(frame)))
	_push_scalar(traces, "p99", float(np.percentile(frame, 99.5)))
	vals: list[float] = []
	for pt in traces["points"]:
		pid = str(pt["id"])
		m = disk_mean(frame, pt["x"], pt["y"], traces["radius"])
		if m is None:
			continue
		_push_scalar(traces, pid, m)
		vals.append(m)
	if vals and "pool" in traces["buffers"]:
		_push_scalar(traces, "pool", float(np.mean(vals)))
	roi = traces.get("roi")
	if roi is not None:
		m = roi_mean(frame, roi)
		if m is not None:
			_push_scalar(traces, "roi", m)
	_set_trace_ylim(traces["ax"], traces["buffers"])


_STALE_TICKS = 40  # ~2 s at the default 50 ms view interval


def _frame_sig(frame: np.ndarray) -> tuple:
	return (
		frame.shape,
		int(frame.min()),
		int(frame.max()),
		int(frame.flat[0]),
		int(frame.flat[-1]),
	)


def _live_tag(state: dict, frame: np.ndarray) -> str:
	# LIVE = pixels changed recently. STALE = PrairieLink is not following Live Scan.
	sig = _frame_sig(frame)
	if sig == state.get("sig"):
		state["same"] = int(state.get("same") or 0) + 1
	else:
		state["sig"] = sig
		state["same"] = 0
	return "LIVE" if state["same"] < _STALE_TICKS else "STALE"


def _apply_status(title, text: str, state: dict) -> None:
	"""Axes title is easy to miss on a black FOV — also overlay, caption, stdout."""
	title.set_text(text)
	hud = state.get("hud")
	if hud is not None:
		hud.set_text(text)
	fig = state.get("fig")
	if fig is not None:
		fig.suptitle(text, fontsize=12, color="#ffee55")
		mgr = getattr(fig.canvas, "manager", None)
		setter = getattr(mgr, "set_window_title", None) if mgr is not None else None
		if callable(setter):
			setter("prairie-live  " + text[:90])
	if state.get("log") and (time.monotonic() - float(state.get("print_t") or 0)) >= 1.0:
		state["print_t"] = time.monotonic()
		print(text, flush=True)


def _on_frame(client, buf: FrameBuffer, im, title, state, traces=None) -> None:
	frame = client.get_frame()
	if frame is None:
		# RelayClient has no last_error — "no frame from relay" means --grab is off
		# or the analysis socket never received a packet.
		err = getattr(client, "last_error", "") or "no frame from relay"
		_apply_status(title, err[:96], state)
		return
	h0, w0 = frame.shape[:2]
	if min(h0, w0) < 8:
		# 1×1 grab is not an FOV — don't fill the rolling mean with it.
		_apply_status(
			title,
			f"grab {w0}x{h0} (not an FOV)  pull relay + restart --grab",
			state,
		)
		return
	# COM buffer is 90° CCW from PV, then also mirrored left/right.
	frame = orient_fov(frame)
	buf.push(frame)
	avg = buf.mean()
	if avg is None:
		return
	points = (traces or {}).get("points") or []
	radius = int((traces or {}).get("radius") or 3)
	im.set_data(_fov_rgb(avg, points, radius, latest=frame))
	h, w = avg.shape
	im.set_extent((0, w, h, 0))
	_fit_fov_axes(im, h, w)
	if traces:
		_update_traces(frame, traces)
	state["n"] += 1
	dt = time.monotonic() - state["t0"]
	fps = state["n"] / dt if dt > 0 else 0
	n_pts = len(points)
	tag = _live_tag(state, frame)
	roi = (traces or {}).get("roi")
	roi_txt = f"{roi_label(roi)}  " if roi is not None else ""
	# max=0 on a 512² title means grab never saw PMT data (wrong channel / not Live).
	# STALE means Live in PV is not filling the PrairieLink buffer — press l.
	_apply_status(
		title,
		f"{w}x{h}  {tag}  avg {len(buf)}/{buf.n}  pts={n_pts}  {roi_txt}"
		f"max={float(frame.max()):.0f}  {fps:.1f} fps  t/a/l/q",
		state,
	)


def _make_layout(n_cols: int, *, nrows: int = 1, width_ratios=None):
	"""One viewer window. New panels: bump nrows/n_cols and add_subplot(gs[r, c]).

	Why: do not call plt.figure()/plt.subplots() again — extra views stay on
	this GridSpec so the operator has a single window.
	"""
	import matplotlib.pyplot as plt

	fig = plt.figure(figsize=(6.4 * max(n_cols, 1), 5.4 * max(nrows, 1)))
	gs = fig.add_gridspec(
		nrows, n_cols, width_ratios=width_ratios, wspace=0.28, hspace=0.32
	)
	return fig, gs


def _fill_trace_axis(ax, points: list, n: int, dt: float, radius: int) -> dict:
	"""Right-hand (or later grid) panel: per-point disk means + pool."""
	import matplotlib.pyplot as plt

	ax.set_xlabel("s")
	ax.set_ylabel("mean (counts)")
	ax.set_title("FOV / markpoint means (raw counts)", fontsize=10)
	ax.axvline(0.0, color="0.7", lw=0.6)
	buffers: dict[str, IntensityBuffer] = {}
	lines: dict = {}
	# Whole-frame traces so photostim of newly created PV points still shows
	# when --series is a different XML (Prairie has no GetMarkPoints).
	buffers["fov"] = IntensityBuffer(n)
	(fov_line,) = ax.plot([], [], lw=1.6, color="0.25", label="fov")
	lines["fov"] = fov_line
	buffers["p99"] = IntensityBuffer(n)
	(p99_line,) = ax.plot([], [], lw=1.2, color="C1", label="p99")
	lines["p99"] = p99_line
	cmap = plt.get_cmap("tab10")
	for i, pt in enumerate(points):
		pid = str(pt["id"])
		buffers[pid] = IntensityBuffer(n)
		(line,) = ax.plot([], [], lw=0.9, color=cmap(i % 10), label=pid)
		lines[pid] = line
	buffers["pool"] = IntensityBuffer(n)
	(pool_line,) = ax.plot([], [], lw=2.2, color="0.1", label="pool")
	lines["pool"] = pool_line
	buffers["roi"] = IntensityBuffer(n)
	(roi_line,) = ax.plot([], [], lw=1.8, color="gold", label="roi")
	lines["roi"] = roi_line
	ax.legend(loc="upper left", fontsize=8, ncol=2)
	return {
		"points": points,
		"radius": radius,
		"buffers": buffers,
		"lines": lines,
		"ax": ax,
		"dt": dt,
		"roi": None,
	}


def _sync_overlay(rect, circ, roi: RectRoi | DiskRoi | None) -> None:
	"""Show the matching artist; hide the other when ROI kind switches."""
	if isinstance(roi, RectRoi):
		rect.set_xy((min(roi.x0, roi.x1), min(roi.y0, roi.y1)))
		rect.set_width(abs(roi.x1 - roi.x0))
		rect.set_height(abs(roi.y1 - roi.y0))
		rect.set_visible(True)
		circ.set_visible(False)
		return
	if isinstance(roi, DiskRoi):
		circ.center = (roi.cx, roi.cy)
		circ.radius = float(roi.radius)
		circ.set_visible(True)
		rect.set_visible(False)
		return
	rect.set_visible(False)
	circ.set_visible(False)


def _set_roi(traces: dict, roi: RectRoi | DiskRoi | None, rect=None, circ=None) -> None:
	# New region → drop the old trace so the y-scale is not a mix of two ROIs.
	traces["roi"] = roi
	traces["buffers"]["roi"].clear()
	traces["lines"]["roi"].set_data([], [])
	if rect is not None and circ is not None:
		_sync_overlay(rect, circ, roi)


# Display-pixel span: below → click disk; at/above → RectangleSelector box.
_CLICK_PX = 5


def _bind_roi_input(fig, ax_img, set_roi, roi_radius: int):
	"""Drag a rectangle or click a disk; keep the two gestures from overlapping."""
	from matplotlib.widgets import RectangleSelector

	press: dict = {"at": None}

	def on_rect(eclick, erelease) -> None:
		if eclick.xdata is None or erelease.xdata is None:
			return
		set_roi(RectRoi(eclick.xdata, eclick.ydata, erelease.xdata, erelease.ydata))

	def on_press(event) -> None:
		if event.inaxes is ax_img and event.xdata is not None:
			press["at"] = (event.x, event.y)

	def on_release(event) -> None:
		at, press["at"] = press["at"], None
		if at is None or event.inaxes is not ax_img or event.xdata is None:
			return
		if abs(event.x - at[0]) < _CLICK_PX and abs(event.y - at[1]) < _CLICK_PX:
			set_roi(DiskRoi(event.xdata, event.ydata, roi_radius))

	selector = RectangleSelector(
		ax_img,
		on_rect,
		useblit=False,
		button=[1],
		minspanx=_CLICK_PX,
		minspany=_CLICK_PX,
		spancoords="pixels",
		interactive=False,
		props=dict(facecolor="none", edgecolor="yellow", linewidth=1.2),
	)
	fig.canvas.mpl_connect("button_press_event", on_press)
	fig.canvas.mpl_connect("button_release_event", on_release)
	return selector


def _bind_viewer_keys(client, fig, on_reset=None) -> None:
	import matplotlib.pyplot as plt

	def on_key(event) -> None:
		if event.key == "t":
			print(client.start_tseries())
		elif event.key == "a":
			print(client.abort())
		elif event.key == "l":
			print(client.start_live())
		elif event.key == "r" and on_reset is not None:
			on_reset()
		elif event.key in ("q", "escape"):
			plt.close(fig)

	fig.canvas.mpl_connect("key_press_event", on_key)


def run_viewer(
	client,
	interval_ms: int,
	avg_s: float = 0.5,
	points: list | None = None,
	disk_radius: int = 3,
	window_s: float = 8.0,
	roi_radius: int = 8,
) -> None:
	import matplotlib.pyplot as plt
	from matplotlib.animation import FuncAnimation

	points = list(points or [])
	buf = FrameBuffer(_avg_n(avg_s, interval_ms))
	dt = interval_ms / 1000.0
	# Always two panels: FOV + traces (fov/p99 even when --series is omitted).
	fig, gs = _make_layout(2, width_ratios=[1.05, 1.0])
	fig.patch.set_facecolor("#222222")
	fig.subplots_adjust(top=0.88)
	ax_img = fig.add_subplot(gs[0, 0])
	ax_img.set_facecolor("#111111")
	im = ax_img.imshow(np.zeros((512, 512, 3), dtype=np.uint8), origin="upper")
	# Yellow on the image — axes title on a black FOV was easy to miss.
	wait = "waiting for frames from relay  (drag/click ROI, r reset, t/a/l/q)"
	title = ax_img.set_title(wait, color="#ffee55", fontsize=12)
	hud = ax_img.text(
		0.02,
		0.98,
		wait,
		transform=ax_img.transAxes,
		va="top",
		ha="left",
		fontsize=13,
		color="#ffee55",
		bbox={"facecolor": "black", "alpha": 0.75, "pad": 6},
	)
	ax_img.set_axis_off()
	fig.suptitle(wait, fontsize=12, color="#ffee55")
	# Next panel: fig.add_subplot(gs[0, 2]) after _make_layout(n_cols=3), not a new window.
	ax_tr = fig.add_subplot(gs[0, 1])
	traces = _fill_trace_axis(
		ax_tr, points, _avg_n(window_s, interval_ms), dt, disk_radius
	)
	from matplotlib.patches import Circle, Rectangle

	rect = Rectangle((0, 0), 0, 0, fill=False, edgecolor="gold", lw=1.4, visible=False)
	circ = Circle((0, 0), 1, fill=False, edgecolor="gold", lw=1.4, visible=False)
	ax_img.add_patch(rect)
	ax_img.add_patch(circ)

	def set_roi(roi) -> None:
		_set_roi(traces, roi, rect, circ)
		fig.canvas.draw_idle()

	selector = _bind_roi_input(fig, ax_img, set_roi, roi_radius)
	_bind_viewer_keys(client, fig, on_reset=lambda: set_roi(None))
	state = {
		"n": 0,
		"t0": time.monotonic(),
		"hud": hud,
		"fig": fig,
		"log": True,
		"print_t": 0.0,
	}
	mgr = getattr(fig.canvas, "manager", None)
	if mgr is not None and callable(getattr(mgr, "set_window_title", None)):
		fig.canvas.manager.set_window_title("prairie-live  waiting for frames")

	def update(_):
		_on_frame(client, buf, im, title, state, traces)
		return (im, title, hud)

	# Keep a reference on the figure; matplotlib only holds a weakref.
	anim = FuncAnimation(
		fig, update, interval=interval_ms, blit=False, cache_frame_data=False
	)
	fig._prairie_anim = anim
	fig._prairie_roi_selector = selector
	plt.show()


def main(argv=None) -> None:
	p = argparse.ArgumentParser(description="PrairieView live viewer")
	p.add_argument("--host", default="127.0.0.1", help="PrairieView IP")
	p.add_argument("--password", default="0000")
	p.add_argument("--relay", help="optional host[:port] if this PC has no PrairieLink")
	p.add_argument("--channel", type=int, default=1)
	p.add_argument("--interval-ms", type=int, default=50)
	p.add_argument(
		"--avg-s",
		type=float,
		default=0.5,
		help="rolling image-mean window (seconds); same stack mean as f0.png",
	)
	p.add_argument("--series", default=None, help="MarkPoints .xml/.gpl for disk ROIs")
	p.add_argument("--config", default=None, help="experiment.json (series + disk_radius)")
	p.add_argument("--disk-radius", type=int, default=None, help="pixel radius (default 3)")
	p.add_argument(
		"--window-s",
		type=float,
		default=8.0,
		help="trace window length (seconds)",
	)
	p.add_argument(
		"--roi-radius", type=int, default=8, help="pixel radius for click-to-place disk ROI"
	)
	p.add_argument("--mock", action="store_true")
	args = p.parse_args(argv)
	print("prairie-live view HUD v4 — yellow overlay + window title show max=", flush=True)
	if args.avg_s <= 0:
		raise SystemExit("--avg-s must be > 0")
	if args.window_s <= 0:
		raise SystemExit("--window-s must be > 0")
	points, radius = _load_view_points(args.series, args.config, args.disk_radius)
	if args.disk_radius is not None and args.disk_radius < 1:
		raise SystemExit("--disk-radius must be >= 1")

	client = _make_backend(args)
	try:
		run_viewer(
			client,
			args.interval_ms,
			args.avg_s,
			points=points,
			disk_radius=radius,
			window_s=args.window_s,
			roi_radius=args.roi_radius,
		)
	finally:
		client.disconnect()


if __name__ == "__main__":
	main()
