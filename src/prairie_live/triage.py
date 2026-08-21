"""Scientist triage of auto-proposed blobs, with live ranking after labels."""

from __future__ import annotations

import argparse
import time

import numpy as np

from prairie_live.criteria import CriteriaModel
from prairie_live.detect import Blob, FrameBuffer, detect_blobs, um_to_radius_px
from prairie_live.features import extract_features
from prairie_live.viewer import _autoscale


class Session:
	def __init__(self, um_per_px: float, min_um: float, max_um: float, min_labels: int, out: str | None, buf_n: int):
		self.um_per_px = um_per_px
		self.min_um = min_um
		self.max_um = max_um
		self.buf = FrameBuffer(buf_n)
		self.blobs: list[Blob] = []
		self.current: Blob | None = None
		self._next_id = 1
		self.model = CriteriaModel(min_labels=min_labels, out_dir=out)
		self._last: np.ndarray | None = None

	def ingest(self, frame: np.ndarray) -> None:
		self._last = frame
		self.buf.push(frame)
		if not self.blobs and len(self.buf) >= min(8, self.buf.n):
			self.redetect()

	def redetect(self) -> None:
		mean = self.buf.mean()
		if mean is None:
			return
		lo, hi = um_to_radius_px(self.min_um, self.max_um, self.um_per_px)
		peaks = detect_blobs(mean, lo, hi)
		for y, x, r, resp in peaks:
			if self._near_existing(y, x, r):
				continue
			b = Blob(id=self._next_id, y=y, x=x, radius_px=r, response=resp)
			b.features = extract_features(mean, b, self.um_per_px)
			self.blobs.append(b)
			self._next_id += 1
		self._pick()

	def label_current(self, yes: bool) -> None:
		if self.current is None or self.current.label is not None:
			return
		self.model.add(self.current, 1 if yes else 0)
		self._pick()

	def skip(self) -> None:
		ranked = self.model.ranked_unlabeled(self.blobs)
		if self.current is None or len(ranked) < 2:
			return
		rest = [b for b in ranked if b.id != self.current.id]
		self.current = rest[0] if rest else self.current

	def select_at(self, y: float, x: float) -> None:
		hit = None
		best = 1e9
		for b in self.blobs:
			d = (b.y - y) ** 2 + (b.x - x) ** 2
			if d <= (2.0 * b.radius_px) ** 2 and d < best:
				best = d
				hit = b
		if hit is not None:
			self.current = hit

	def n_labeled(self) -> int:
		return sum(1 for b in self.blobs if b.label is not None)

	def _near_existing(self, y, x, r) -> bool:
		# Keep labeled IDs stable across re-detect; skip duplicates.
		lim = max(r, 2.0)
		for b in self.blobs:
			if (b.y - y) ** 2 + (b.x - x) ** 2 < lim * lim:
				return True
		return False

	def _pick(self) -> None:
		ranked = self.model.ranked_unlabeled(self.blobs)
		self.current = ranked[0] if ranked else None


class BlobMock:
	"""Stationary Gaussians so triage --mock can detect without a scope."""

	def __init__(self, channel: int = 1):
		self.channel = channel
		self._t = 0.0
		self._img = _blob_field(256, 256)

	def connect(self) -> None:
		pass

	def disconnect(self) -> None:
		pass

	def get_frame(self, channel: int = 1) -> np.ndarray:
		self._t += 0.05
		noise = np.random.default_rng(0).normal(0, 8, self._img.shape)
		# Tiny drift so the live pane is not a still; peaks stay findable.
		shift = int(self._t) % 2
		out = np.roll(self._img, shift, axis=1) + noise
		return np.clip(out, 0, 65535).astype(np.uint16)

	def get_state(self, key: str, index=None, subindex=None):
		return {"ok": True, "value": "1.0"}

	def get_motor_position(self, axis: str, device=None):
		return {"ok": True, "axis": axis, "value": 0.0}

	def start_tseries(self):
		return {"ok": True, "cmd": "tseries"}

	def abort(self):
		return {"ok": True, "cmd": "abort"}

	def start_live(self):
		return {"ok": True, "cmd": "live"}


def _blob_field(h: int, w: int) -> np.ndarray:
	yy, xx = np.mgrid[0:h, 0:w]
	img = np.full((h, w), 12.0)
	for y, x, sig, amp in (
		(64, 80, 5.0, 400.0),
		(70, 180, 6.0, 80.0),
		(160, 70, 5.5, 350.0),
		(180, 190, 4.5, 60.0),
		(120, 128, 5.0, 320.0),
		(40, 200, 8.0, 50.0),
		(200, 40, 5.0, 280.0),
		(90, 40, 4.0, 55.0),
		(210, 140, 6.0, 90.0),
		(130, 210, 5.0, 310.0),
	):
		img += amp * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2.0 * sig * sig))
	return img


def um_per_pixel(client) -> float:
	try:
		r = client.get_state("micronsPerPixel", "XAxis")
		return float(r.get("value"))
	except (TypeError, ValueError, AttributeError):
		return 1.0


def run_triage(client, sess: Session, interval_ms: int) -> None:
	import matplotlib.pyplot as plt
	from matplotlib.animation import FuncAnimation
	from matplotlib.patches import Circle

	fig, ax = plt.subplots()
	im = ax.imshow(np.zeros((32, 32)), cmap="gray", vmin=0, vmax=1)
	title = ax.set_title("triage  y accept  n reject  . skip  d redetect  q")
	ax.set_axis_off()
	patches: list = []

	def on_key(event) -> None:
		_handle_key(event, sess, fig)

	def on_click(event) -> None:
		if event.inaxes is not ax or event.xdata is None:
			return
		sess.select_at(event.ydata, event.xdata)

	def update(_):
		frame = client.get_frame()
		if frame is None:
			return (im, title)
		sess.ingest(frame)
		im.set_data(_autoscale(frame))
		im.set_extent((0, frame.shape[1], frame.shape[0], 0))
		_draw_blobs(ax, sess, patches)
		title.set_text(_status(sess))
		return (im, title)

	fig.canvas.mpl_connect("key_press_event", on_key)
	fig.canvas.mpl_connect("button_press_event", on_click)
	# Keep a reference; matplotlib only holds a weakref (viewer.py bug).
	anim = FuncAnimation(fig, update, interval=interval_ms, blit=False, cache_frame_data=False)
	plt.show()
	_ = anim


def _handle_key(event, sess: Session, fig) -> None:
	if event.key == "y":
		sess.label_current(True)
	elif event.key == "n":
		sess.label_current(False)
	elif event.key == ".":
		sess.skip()
	elif event.key == "d":
		sess.redetect()
	elif event.key in ("q", "escape"):
		import matplotlib.pyplot as plt

		plt.close(fig)


def _draw_blobs(ax, sess: Session, patches: list) -> None:
	for p in patches:
		p.remove()
	patches.clear()
	ranked = sess.model.ranked_unlabeled(sess.blobs)
	suggest = {b.id for b in ranked[:3]} if sess.model.clf is not None else set()
	for b in sess.blobs:
		color, lw = _blob_style(b, sess.current, suggest)
		c = Circle((b.x, b.y), b.radius_px, fill=False, edgecolor=color, linewidth=lw)
		ax.add_patch(c)
		patches.append(c)


def _blob_style(b: Blob, current, suggest) -> tuple[str, float]:
	if current is not None and b.id == current.id:
		return "yellow", 2.5
	if b.label == 1:
		return "lime", 1.5
	if b.label == 0:
		return "red", 1.5
	if b.id in suggest:
		return "cyan", 2.2
	return "0.6", 1.0


def _status(sess: Session) -> str:
	n = sess.n_labeled()
	on = "model on" if sess.model.clf is not None else "model off"
	p = ""
	if sess.current is not None and sess.current.p_hat is not None:
		p = f"  next p={sess.current.p_hat:.2f}"
	cid = sess.current.id if sess.current else "-"
	return f"id {cid}  labeled {n}  {on}{p}  y/n . d q"


def main(argv=None) -> None:
	p = argparse.ArgumentParser(description="Live cell triage with in-session learning")
	p.add_argument("--host", default="127.0.0.1")
	p.add_argument("--password", default="0000")
	p.add_argument("--relay", help="host[:port] of prairie_live.relay")
	p.add_argument("--channel", type=int, default=1)
	p.add_argument("--interval-ms", type=int, default=50)
	p.add_argument("--mock", action="store_true")
	p.add_argument("--min-um", type=float, default=5.0)
	p.add_argument("--max-um", type=float, default=20.0)
	p.add_argument("--min-labels", type=int, default=8)
	p.add_argument("--out", default="labels")
	p.add_argument("--buffer-frames", type=int, default=23)
	args = p.parse_args(argv)
	client = _triage_client(args)
	try:
		um = um_per_pixel(client)
		sess = Session(um, args.min_um, args.max_um, args.min_labels, args.out, args.buffer_frames)
		run_triage(client, sess, args.interval_ms)
	finally:
		client.disconnect()


def _triage_client(args):
	if args.mock:
		c = BlobMock(args.channel)
		c.connect()
		return c
	from prairie_live.viewer import _make_backend

	return _make_backend(args)


if __name__ == "__main__":
	main()
