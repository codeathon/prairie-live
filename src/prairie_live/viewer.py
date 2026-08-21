"""Rolling 8x8 mean-intensity mosaic. Keys: t T-series, a abort, l live scan, q quit."""

from __future__ import annotations

import argparse
import time

import numpy as np

from prairie_live.grid_traces import TraceBuffer, tile_means


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


def _window_samples(window_s: float, interval_ms: int) -> int:
	return max(int(round(window_s * 1000.0 / max(interval_ms, 1))), 2)


def _style_tile(ax, r: int, c: int, grid: int) -> None:
	# Dense mosaic: ticks only on the left column and bottom row.
	ax.tick_params(labelsize=6, length=2, pad=1)
	if c != 0:
		ax.tick_params(labelleft=False)
	if r != grid - 1:
		ax.tick_params(labelbottom=False)
	ax.margins(x=0)


def _build_mosaic(fig, grid: int):
	gs = fig.add_gridspec(grid, grid, hspace=0.08, wspace=0.08)
	axes = []
	lines = []
	for r in range(grid):
		for c in range(grid):
			ax = fig.add_subplot(gs[r, c])
			(line,) = ax.plot([], [], lw=0.8, color="0.15")
			_style_tile(ax, r, c, grid)
			axes.append(ax)
			lines.append(line)
	return axes, lines


def _set_tile_ylim(ax, y: np.ndarray) -> None:
	lo = float(y.min())
	hi = float(y.max())
	if hi <= lo:
		hi = lo + 1.0
	pad = 0.05 * (hi - lo)
	ax.set_ylim(lo - pad, hi + pad)


def _draw_traces(axes, lines, buf: TraceBuffer, dt: float, grid: int) -> None:
	stack = buf.as_array()
	if stack.shape[0] == 0:
		return
	n = stack.shape[0]
	x = (np.arange(n) - (n - 1)) * dt
	for r in range(grid):
		for c in range(grid):
			i = r * grid + c
			y = stack[:, r, c]
			lines[i].set_data(x, y)
			axes[i].set_xlim(x[0], 0.0 if n > 1 else dt)
			_set_tile_ylim(axes[i], y)


def run_viewer(client, interval_ms: int, grid: int = 8, window_s: float = 8.0) -> None:
	import matplotlib.pyplot as plt
	from matplotlib.animation import FuncAnimation

	buf = TraceBuffer(_window_samples(window_s, interval_ms), grid)
	dt = interval_ms / 1000.0
	fig = plt.figure(figsize=(10, 10))
	axes, lines = _build_mosaic(fig, grid)
	title = fig.suptitle("waiting for frames  (t/a/l/q)", fontsize=10)
	state = {"n": 0, "t0": time.monotonic()}

	def on_key(event) -> None:
		if event.key == "t":
			print(client.start_tseries())
		elif event.key == "a":
			print(client.abort())
		elif event.key == "l":
			print(client.start_live())
		elif event.key in ("q", "escape"):
			plt.close(fig)

	def update(_):
		_on_frame(client, buf, axes, lines, dt, grid, title, state)
		return tuple(lines)

	fig.canvas.mpl_connect("key_press_event", on_key)
	# Keep a reference; matplotlib only holds a weakref.
	anim = FuncAnimation(fig, update, interval=interval_ms, blit=False, cache_frame_data=False)
	plt.show()
	_ = anim


def _on_frame(client, buf, axes, lines, dt, grid, title, state) -> None:
	frame = client.get_frame()
	if frame is None:
		err = getattr(client, "last_error", "") or "no frame"
		title.set_text(err[:80])
		return
	buf.push(tile_means(frame, grid))
	_draw_traces(axes, lines, buf, dt, grid)
	state["n"] += 1
	elapsed = time.monotonic() - state["t0"]
	fps = state["n"] / elapsed if elapsed > 0 else 0
	h, w = frame.shape
	title.set_text(f"{w}x{h}  {grid}x{grid} traces  {fps:.1f} fps  t/a/l/q")


def main(argv=None) -> None:
	p = argparse.ArgumentParser(description="PrairieView live rolling-intensity mosaic")
	p.add_argument("--host", default="127.0.0.1", help="PrairieView IP")
	p.add_argument("--password", default="0000")
	p.add_argument("--relay", help="optional host[:port] if this PC has no PrairieLink")
	p.add_argument("--channel", type=int, default=1)
	p.add_argument("--interval-ms", type=int, default=50)
	p.add_argument("--grid", type=int, default=8, help="tiles per FOV axis")
	p.add_argument("--window-s", type=float, default=8.0, help="rolling trace length in seconds")
	p.add_argument("--mock", action="store_true")
	args = p.parse_args(argv)
	if args.grid < 1:
		raise SystemExit("--grid must be >= 1")
	client = _make_backend(args)
	try:
		run_viewer(client, args.interval_ms, args.grid, args.window_s)
	finally:
		client.disconnect()


if __name__ == "__main__":
	main()
