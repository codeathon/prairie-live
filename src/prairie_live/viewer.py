"""Continuous live display. Keys: t T-series, a abort, l live scan, q quit."""

from __future__ import annotations

import argparse
import time

import numpy as np


def _autoscale(frame: np.ndarray) -> np.ndarray:
	# 13–16 bit PMT data looks black if shown as raw uint16.
	lo, hi = np.percentile(frame, (1.0, 99.5))
	if hi <= lo:
		hi = lo + 1
	scaled = np.clip((frame.astype(np.float32) - lo) / (hi - lo), 0, 1)
	return scaled


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


def run_viewer(client, interval_ms: int) -> None:
	import matplotlib.pyplot as plt
	from matplotlib.animation import FuncAnimation

	fig, ax = plt.subplots()
	im = ax.imshow(np.zeros((32, 32)), cmap="gray", vmin=0, vmax=1)
	title = ax.set_title("waiting for frames  (t/a/l/q)")
	ax.set_axis_off()
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
		frame = client.get_frame()
		if frame is None:
			err = getattr(client, "last_error", "") or "no frame"
			# Keep the window up; COM aborts must not kill the animation.
			title.set_text(err[:80])
			return (im, title)
		im.set_data(_autoscale(frame))
		im.set_extent((0, frame.shape[1], frame.shape[0], 0))
		state["n"] += 1
		dt = time.monotonic() - state["t0"]
		fps = state["n"] / dt if dt > 0 else 0
		title.set_text(f"{frame.shape[1]}x{frame.shape[0]}  {fps:.1f} fps  t/a/l/q")
		return im, title

	fig.canvas.mpl_connect("key_press_event", on_key)
	FuncAnimation(fig, update, interval=interval_ms, blit=False, cache_frame_data=False)
	plt.show()


def main(argv=None) -> None:
	p = argparse.ArgumentParser(description="PrairieView live viewer")
	p.add_argument("--host", default="127.0.0.1", help="PrairieView IP")
	p.add_argument("--password", default="0000")
	p.add_argument("--relay", help="optional host[:port] if this PC has no PrairieLink")
	p.add_argument("--channel", type=int, default=1)
	p.add_argument("--interval-ms", type=int, default=50)
	p.add_argument("--mock", action="store_true")
	args = p.parse_args(argv)

	client = _make_backend(args)
	try:
		run_viewer(client, args.interval_ms)
	finally:
		client.disconnect()


if __name__ == "__main__":
	main()
