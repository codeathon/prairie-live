"""Direct script -gi grab: PV writes the image into a buffer we own.

`-gi <channel> <pid> <address>` makes PV write the current image into a
buffer inside this python.exe, so it only works with PV on the same PC.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

import numpy as np

_SENTINEL = 0xFFFFFFFF
_LOW_LIMIT = 0x80000000
_MEM_COMMIT_RESERVE = 0x3000
_MEM_RELEASE = 0x8000
_PAGE_READWRITE = 0x04


def _kernel32():
	k32 = ctypes.WinDLL("kernel32", use_last_error=True)
	k32.VirtualAlloc.restype = ctypes.c_void_p
	k32.VirtualAlloc.argtypes = [
		ctypes.c_void_p,
		ctypes.c_size_t,
		ctypes.c_uint32,
		ctypes.c_uint32,
	]
	k32.VirtualFree.restype = ctypes.c_int
	k32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
	return k32


def alloc_low(nbytes: int) -> int | None:
	"""VirtualAlloc a block that ends below 2 GiB, or None (non-Windows / full)."""
	if os.name != "nt":
		return None
	k32 = _kernel32()
	for hint in range(0x01000000, _LOW_LIMIT - nbytes, 0x00800000):
		p = k32.VirtualAlloc(
			ctypes.c_void_p(hint), nbytes, _MEM_COMMIT_RESERVE, _PAGE_READWRITE
		)
		if p:
			if int(p) + nbytes <= _LOW_LIMIT:
				return int(p)
			k32.VirtualFree(ctypes.c_void_p(p), 0, _MEM_RELEASE)
	return None


class PixelBuffer:
	"""uint32 buffer with a fixed address PV can write into."""

	def __init__(self, n: int, *, low: bool = True, dtype=np.uint32):
		self.n = int(n)
		self.dtype = np.dtype(dtype)
		nbytes = self.n * self.dtype.itemsize
		self._virtual = False
		self._keep = None
		addr = alloc_low(nbytes) if low else None
		if addr is not None:
			self._virtual = True
			raw = (ctypes.c_char * nbytes).from_address(addr)
			self.pixels = np.frombuffer(raw, dtype=self.dtype, count=self.n)
		else:
			self._keep = np.empty(self.n, dtype=self.dtype)
			self.pixels = self._keep
			addr = int(self._keep.ctypes.data)
		self.addr = int(addr)

	@property
	def is_low(self) -> bool:
		return self.addr + self.n * self.dtype.itemsize <= _LOW_LIMIT

	def fill_sentinel(self) -> None:
		self.pixels[:] = np.iinfo(self.dtype).max

	def count_written(self) -> int:
		return int(np.count_nonzero(self.pixels != np.iinfo(self.dtype).max))

	def close(self) -> None:
		if self._virtual and self.addr:
			self.pixels = np.empty(0, dtype=self.dtype)
			_kernel32().VirtualFree(ctypes.c_void_p(self.addr), 0, _MEM_RELEASE)
			self._virtual = False
		self.addr = 0


@dataclass
class GiResult:
	frame: np.ndarray | None
	reply: str
	written: int
	addr: int
	low: bool

	def summary(self) -> str:
		reply = " ".join(self.reply.split())[:160] or "(empty reply)"
		where = f"addr=0x{self.addr:X} ({'<2GiB' if self.low else '>=2GiB'})"
		if self.frame is None:
			return f"-gi {where} PV wrote 0 px; reply={reply!r}"
		f = self.frame
		return (
			f"-gi {where} wrote={self.written} {int(f.shape[1])}x{int(f.shape[0])} "
			f"min={int(f.min())} max={int(f.max())} mean={float(f.mean()):.1f} "
			f"reply={reply!r}"
		)


def parse_wh_reply(text: str) -> int | None:
	for tok in str(text).replace("\r", " ").split():
		try:
			n = int(float(tok))
		except ValueError:
			continue
		if n >= 32:
			return n
	return None


class GiGrabber:
	"""Send `-gi <ch> <pid> <addr>` on a PrairieLink TCP session and read pixels."""

	def __init__(self, link, *, pid: int | None = None, low: bool = True):
		self.link = link
		self.pid = int(pid if pid is not None else os.getpid())
		self.low = bool(low)
		self._buf: PixelBuffer | None = None
		self.last_error = ""

	def fov_wh(self, fallback=(512, 512)) -> tuple[int, int]:
		"""(height, width) from -gts; fallback if PV does not answer."""
		try:
			w = parse_wh_reply(self.link.query("-gts", "pixelsPerLine"))
			h = parse_wh_reply(self.link.query("-gts", "linesPerFrame"))
		except Exception:
			return tuple(fallback)
		if w and h:
			return (int(h), int(w))
		return tuple(fallback)

	def _buffer(self, n: int) -> PixelBuffer:
		if self._buf is None or self._buf.n != n:
			if self._buf is not None:
				self._buf.close()
			self._buf = PixelBuffer(n, low=self.low)
		return self._buf

	def grab(self, channel: int, w: int, h: int) -> GiResult:
		buf = self._buffer(int(w) * int(h))
		buf.fill_sentinel()
		reply = self.link.send_command(
			"-gi", str(int(channel)), str(self.pid), str(buf.addr)
		)
		written = buf.count_written()
		frame = None
		if written:
			px = np.minimum(buf.pixels, 0xFFFF).astype(np.uint16)
			frame = px.reshape(int(h), int(w)).copy()
		res = GiResult(frame, reply or "", written, buf.addr, buf.is_low)
		self.last_error = "" if frame is not None else res.summary()
		return res

	def close(self) -> None:
		if self._buf is not None:
			self._buf.close()
			self._buf = None
		try:
			self.link.close()
		except Exception:
			pass
