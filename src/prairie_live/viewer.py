"""Live FOV + rolling ROI mean intensity. Keys: t/a/l/q, r reset ROI."""

from __future__ import annotations

import argparse
import time

import numpy as np

from prairie_live.roi_trace import (
	DiskRoi,
	IntensityBuffer,
	RectRoi,
	roi_label,
	roi_mean,
)


def _autoscale(frame: np.ndarray) -> np.ndarray:
	# 13–16 bit PMT data looks black if shown as raw uint16.
	lo, hi = np.percentile(frame, (1.0, 99.5))
	if hi <= lo:
		hi = lo + 1
	scaled = np.clip((frame.astype(np.float32) - lo) / (hi - lo), 0, 1)
	return scaled


def _window_samples(window_s: float, interval_ms: int) -> int:
	return max(int(round(window_s * 1000.0 / max(interval_ms, 1))), 2)


def _set_trace_ylim(ax, y: np.ndarray) -> None:
	lo = float(y.min())
	hi = float(y.max())
	if hi <= lo:
		hi = lo + 1.0
	pad = 0.05 * (hi - lo)
	ax.set_ylim(lo - pad, hi + pad)


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
		# Stable offset (not per-frame min-max) so ROI means move in raw counts.
		self._t += 0.05
		y, x = np.mgrid[0:256, 0:256]
		z = np.sin((x + self._t * 40) / 18) + np.cos((y - self._t * 25) / 22)
		# Localized pulse: drag a box here vs elsewhere to see two traces.
		cy, cx = 80.0, 180.0
		blob = np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * 18.0 ** 2))
		pulse = 0.5 + 0.5 * np.sin(self._t * 2.2)
		frame = 3000.0 + 800.0 * z + blob * pulse * 8000.0
		return np.clip(frame, 0, 65535).astype(np.uint16)

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


def _draw_trace(ax, line, buf: IntensityBuffer, dt: float) -> None:
	y = buf.as_array()
	if y.size == 0:
		return
	x = (np.arange(y.size) - (y.size - 1)) * dt
	line.set_data(x, y)
	ax.set_xlim(x[0], 0.0 if y.size > 1 else dt)
	_set_trace_ylim(ax, y)


def _sync_overlay(rect, circ, roi: RectRoi | DiskRoi | None) -> None:
	"""Show the matching artist; hide the other when ROI kind switches."""
	if isinstance(roi, RectRoi):
		x = min(roi.x0, roi.x1)
		y = min(roi.y0, roi.y1)
		rect.set_xy((x, y))
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


def _build_figure():
	"""Live image (left) + rolling mean (right). Overlay hidden until an ROI."""
	import matplotlib.pyplot as plt
	from matplotlib.patches import Circle, Rectangle

	fig, (ax_img, ax_tr) = plt.subplots(
		1, 2, figsize=(12, 5.5), gridspec_kw={"width_ratios": [1.05, 1.0]}
	)
	im = ax_img.imshow(np.zeros((32, 32)), cmap="gray", vmin=0, vmax=1)
	ax_img.set_axis_off()
	rect = Rectangle((0, 0), 0, 0, fill=False, edgecolor="yellow", lw=1.4)
	circ = Circle((0, 0), 1, fill=False, edgecolor="yellow", lw=1.4)
	rect.set_visible(False)
	circ.set_visible(False)
	ax_img.add_patch(rect)
	ax_img.add_patch(circ)
	(line,) = ax_tr.plot([], [], lw=1.2, color="0.15")
	ax_tr.set_xlabel("s")
	ax_tr.set_ylabel("mean (counts)")
	ax_tr.axvline(0.0, color="0.7", lw=0.6)
	title = fig.suptitle(
		"waiting for frames  (drag ROI, click disk, r reset)", fontsize=10
	)
	return fig, ax_img, ax_tr, im, line, title, rect, circ


# Display-pixel span: below → click disk; at/above → RectangleSelector box.
_CLICK_PX = 5


def _bind_roi_input(fig, ax_img, set_roi, disk_radius: int, state: dict):
	"""Drag a rectangle or click a disk; keep the two gestures from overlapping."""
	from matplotlib.widgets import RectangleSelector

	def on_rect(eclick, erelease) -> None:
		if eclick.xdata is None or erelease.xdata is None:
			return
		set_roi(RectRoi(eclick.xdata, eclick.ydata, erelease.xdata, erelease.ydata))

	def on_press(event) -> None:
		if event.inaxes is ax_img and event.xdata is not None:
			state["press"] = (event.xdata, event.ydata, event.x, event.y)

	def on_release(event) -> None:
		press = state["press"]
		state["press"] = None
		if press is None or event.inaxes is not ax_img or event.xdata is None:
			return
		if abs(event.x - press[2]) < _CLICK_PX and abs(event.y - press[3]) < _CLICK_PX:
			set_roi(DiskRoi(event.xdata, event.ydata, disk_radius))

	# interactive=False: we draw our own overlay so disk and rect look the same.
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


def run_viewer(
	client,
	interval_ms: int,
	window_s: float = 8.0,
	disk_radius: int = 8,
) -> None:
	import matplotlib.pyplot as plt
	from matplotlib.animation import FuncAnimation

	buf = IntensityBuffer(_window_samples(window_s, interval_ms))
	dt = interval_ms / 1000.0
	fig, ax_img, ax_tr, im, line, title, rect, circ = _build_figure()
	state = {"n": 0, "t0": time.monotonic(), "roi": None, "press": None}

	def set_roi(roi: RectRoi | DiskRoi | None) -> None:
		# New region → drop the old trace so y-scale is not a mix of two ROIs.
		state["roi"] = roi
		buf.clear()
		_sync_overlay(rect, circ, roi)
		fig.canvas.draw_idle()

	def on_key(event) -> None:
		if event.key == "t":
			print(client.start_tseries())
		elif event.key == "a":
			print(client.abort())
		elif event.key == "l":
			print(client.start_live())
		elif event.key == "r":
			set_roi(None)
		elif event.key in ("q", "escape"):
			plt.close(fig)

	def update(_):
		_on_frame(client, buf, im, ax_tr, line, dt, title, state)
		return (im, line, title)

	selector = _bind_roi_input(fig, ax_img, set_roi, disk_radius, state)
	fig.canvas.mpl_connect("key_press_event", on_key)
	# Keep a reference; matplotlib only holds a weakref.
	anim = FuncAnimation(
		fig, update, interval=interval_ms, blit=False, cache_frame_data=False
	)
	plt.show()
	_ = (anim, selector)


def _on_frame(client, buf, im, ax_tr, line, dt, title, state) -> None:
	frame = client.get_frame()
	if frame is None:
		err = getattr(client, "last_error", "") or "no frame"
		title.set_text(err[:80])
		return
	mean = roi_mean(frame, state["roi"])
	if mean is not None:
		buf.push(mean)
	im.set_data(_autoscale(frame))
	im.set_extent((0, frame.shape[1], frame.shape[0], 0))
	_draw_trace(ax_tr, line, buf, dt)
	state["n"] += 1
	elapsed = time.monotonic() - state["t0"]
	fps = state["n"] / elapsed if elapsed > 0 else 0
	h, w = frame.shape
	n = mean if mean is not None else float("nan")
	title.set_text(
		f"{w}x{h}  {roi_label(state['roi'])}  mean={n:.0f}  "
		f"{fps:.1f} fps  drag/click ROI  r reset  t/a/l/q"
	)


def main(argv=None) -> None:
	p = argparse.ArgumentParser(description="PrairieView live FOV + ROI intensity")
	p.add_argument("--host", default="127.0.0.1", help="PrairieView IP")
	p.add_argument("--password", default="0000")
	p.add_argument("--relay", help="optional host[:port] if this PC has no PrairieLink")
	p.add_argument("--channel", type=int, default=1)
	p.add_argument("--interval-ms", type=int, default=50)
	p.add_argument("--window-s", type=float, default=8.0, help="rolling trace length")
	p.add_argument(
		"--disk-radius",
		type=int,
		default=8,
		help="pixel radius for click-to-place disk ROI",
	)
	p.add_argument("--mock", action="store_true")
	args = p.parse_args(argv)
	if args.window_s <= 0:
		raise SystemExit("--window-s must be > 0")
	if args.disk_radius < 1:
		raise SystemExit("--disk-radius must be >= 1")
	client = _make_backend(args)
	try:
		run_viewer(client, args.interval_ms, args.window_s, args.disk_radius)
	finally:
		client.disconnect()


if __name__ == "__main__":
	main()
