"""Continuous live display (rolling stack mean). Keys: t T-series, a abort, l, q."""

from __future__ import annotations

import argparse
import time

import numpy as np

from prairie_live.trial_images import FrameBuffer, to_u8


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


def _on_frame(client, buf: FrameBuffer, im, title, state) -> None:
	frame = client.get_frame()
	if frame is None:
		err = getattr(client, "last_error", "") or "no frame"
		# Keep the window up; COM aborts must not kill the animation.
		title.set_text(err[:80])
		return
	buf.push(frame)
	avg = buf.mean()
	if avg is None:
		return
	# Same percentile stretch as trial_images f0.png (to_u8), mapped to 0–1.
	im.set_data(to_u8(avg).astype(np.float32) / 255.0)
	im.set_extent((0, avg.shape[1], avg.shape[0], 0))
	state["n"] += 1
	dt = time.monotonic() - state["t0"]
	fps = state["n"] / dt if dt > 0 else 0
	h, w = avg.shape
	title.set_text(
		f"{w}x{h}  avg {len(buf)}/{buf.n}  {fps:.1f} fps  t/a/l/q"
	)


def run_viewer(client, interval_ms: int, avg_s: float = 0.5) -> None:
	import matplotlib.pyplot as plt
	from matplotlib.animation import FuncAnimation

	buf = FrameBuffer(_avg_n(avg_s, interval_ms))
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
		_on_frame(client, buf, im, title, state)
		return (im, title)

	fig.canvas.mpl_connect("key_press_event", on_key)
	# Keep a reference; matplotlib only holds a weakref.
	anim = FuncAnimation(
		fig, update, interval=interval_ms, blit=False, cache_frame_data=False
	)
	plt.show()
	_ = anim


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
	p.add_argument("--mock", action="store_true")
	args = p.parse_args(argv)
	if args.avg_s <= 0:
		raise SystemExit("--avg-s must be > 0")

	client = _make_backend(args)
	try:
		run_viewer(client, args.interval_ms, args.avg_s)
	finally:
		client.disconnect()


if __name__ == "__main__":
	main()
