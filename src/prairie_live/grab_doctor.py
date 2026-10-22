"""Scope-PC report for "NO FRAME": python -m prairie_live grab-doctor --password UAKF

The relay's -gi grab makes PV write pixels into this python.exe's memory. The
report checks what decides whether that write can happen (which PV build is
running, elevation and integrity of both processes, 32/64-bit, recent Windows /
Defender changes, and whether a PrairieLink COM build is still registered) and
then sends -gi with a buffer below 2 GiB and one wherever numpy puts it, so
PV's reply text is visible.
"""

from __future__ import annotations

import argparse
import ctypes
import glob
import os
import platform
import subprocess
import sys
import time
import traceback

import numpy as np

_PV_NAME_HINTS = ("prairie",)


class Report:
	def __init__(self):
		self.lines: list[str] = []

	def section(self, title: str) -> None:
		self.add("")
		self.add(f"=== {title} ===")

	def add(self, line: str = "") -> None:
		print(line, flush=True)
		self.lines.append(line)

	def text(self) -> str:
		return "\n".join(self.lines) + "\n"


def _guard(rep: Report, label: str, fn) -> None:
	try:
		fn()
	except Exception as e:
		rep.add(f"{label}: ERROR {type(e).__name__}: {e}")
		for ln in traceback.format_exc().strip().splitlines()[-3:]:
			rep.add(f"    {ln}")


# --- Windows process info (ctypes only; works without admin) ----------------

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TOKEN_QUERY = 0x0008
_TOKEN_ELEVATION = 20
_TOKEN_INTEGRITY_LEVEL = 25


def _win():
	k32 = ctypes.WinDLL("kernel32", use_last_error=True)
	adv = ctypes.WinDLL("advapi32", use_last_error=True)
	k32.OpenProcess.restype = ctypes.c_void_p
	k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
	k32.CloseHandle.argtypes = [ctypes.c_void_p]
	k32.QueryFullProcessImageNameW.argtypes = [
		ctypes.c_void_p,
		ctypes.c_uint32,
		ctypes.c_wchar_p,
		ctypes.POINTER(ctypes.c_uint32),
	]
	k32.IsWow64Process.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
	adv.OpenProcessToken.argtypes = [
		ctypes.c_void_p,
		ctypes.c_uint32,
		ctypes.POINTER(ctypes.c_void_p),
	]
	adv.GetTokenInformation.argtypes = [
		ctypes.c_void_p,
		ctypes.c_int,
		ctypes.c_void_p,
		ctypes.c_uint32,
		ctypes.POINTER(ctypes.c_uint32),
	]
	adv.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
	adv.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
	adv.GetSidSubAuthority.restype = ctypes.POINTER(ctypes.c_uint32)
	adv.GetSidSubAuthority.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
	return k32, adv


def _pids() -> list[int]:
	psapi = ctypes.WinDLL("psapi")
	arr = (ctypes.c_uint32 * 8192)()
	needed = ctypes.c_uint32()
	psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(needed))
	return [int(p) for p in arr[: needed.value // 4] if p]


def process_info(pid: int) -> dict:
	k32, adv = _win()
	info: dict = {"pid": pid}
	h = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
	if not h:
		info["error"] = f"OpenProcess failed (winerror {ctypes.get_last_error()})"
		return info
	try:
		buf = ctypes.create_unicode_buffer(1024)
		size = ctypes.c_uint32(1024)
		if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
			info["path"] = buf.value
		wow = ctypes.c_int(0)
		if k32.IsWow64Process(h, ctypes.byref(wow)):
			info["bits"] = 32 if wow.value else 64
		tok = ctypes.c_void_p()
		if adv.OpenProcessToken(h, _TOKEN_QUERY, ctypes.byref(tok)):
			try:
				elev = ctypes.c_uint32(0)
				ret = ctypes.c_uint32(0)
				if adv.GetTokenInformation(
					tok, _TOKEN_ELEVATION, ctypes.byref(elev), 4, ctypes.byref(ret)
				):
					info["elevated"] = bool(elev.value)
				lab = ctypes.create_string_buffer(256)
				if adv.GetTokenInformation(
					tok, _TOKEN_INTEGRITY_LEVEL, lab, 256, ctypes.byref(ret)
				):
					sid = ctypes.c_void_p.from_buffer(lab).value
					count = adv.GetSidSubAuthorityCount(sid)[0]
					rid = adv.GetSidSubAuthority(sid, count - 1)[0]
					info["integrity"] = {
						0x1000: "low",
						0x2000: "medium",
						0x2100: "medium+",
						0x3000: "high",
						0x4000: "system",
					}.get(rid, hex(rid))
			finally:
				k32.CloseHandle(tok)
	finally:
		k32.CloseHandle(h)
	return info


def fmt_proc(info: dict) -> str:
	parts = [f"pid={info.get('pid')}"]
	for k in ("bits", "elevated", "integrity"):
		if k in info:
			parts.append(f"{k}={info[k]}")
	if "error" in info:
		parts.append(info["error"])
	parts.append(info.get("path", "?"))
	return "  ".join(str(p) for p in parts)


def file_version(path: str) -> str:
	try:
		import win32api

		vi = win32api.GetFileVersionInfo(path, "\\")
		ms, ls = vi["FileVersionMS"], vi["FileVersionLS"]
		return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
	except Exception:
		return "?"


def file_line(path: str) -> str:
	st = os.stat(path)
	mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
	return f"{path}  ver={file_version(path)}  modified={mtime}  bytes={st.st_size}"


# --- report sections ---------------------------------------------------------


def section_python(rep: Report) -> None:
	rep.section("this python")
	rep.add(f"exe={sys.executable}")
	rep.add(f"version={sys.version.split()[0]} {platform.architecture()[0]}")
	rep.add(f"pid={os.getpid()}  user={os.environ.get('USERNAME', '?')}")
	if os.name == "nt":
		rep.add(fmt_proc(process_info(os.getpid())))
		others = [
			p for p in _pids()
			if p != os.getpid()
			and os.path.basename(process_info(p).get("path", "")).lower()
			in ("python.exe", "pythonw.exe")
		]
		rep.add(f"other python.exe running: {others or 'none'}")


def section_prairie_processes(rep: Report) -> None:
	rep.section("running PrairieView processes")
	found = 0
	for pid in _pids():
		info = process_info(pid)
		path = info.get("path", "")
		if any(h in path.lower() for h in _PV_NAME_HINTS):
			found += 1
			rep.add(fmt_proc(info))
			rep.add(f"    file: {file_line(path)}")
	if not found:
		rep.add("no process path contains 'prairie' — is PrairieView open?")


def section_com_registration(rep: Report) -> None:
	import winreg

	rep.section("PrairieLink COM registration (not used by prairie-live)")
	rep.add("prairie-live talks to PV only on its script port. PrairieLink 6.0 is a")
	rep.add("test-only build; if it is listed below, have it unregistered.")
	views = (("64-bit view", winreg.KEY_WOW64_64KEY), ("32-bit view", winreg.KEY_WOW64_32KEY))
	for progid in ("PrairieLink64.Application", "PrairieLink.Application"):
		for label, flag in views:
			try:
				with winreg.OpenKey(
					winreg.HKEY_CLASSES_ROOT, progid + r"\CLSID", 0, winreg.KEY_READ | flag
				) as k:
					clsid = winreg.QueryValueEx(k, "")[0]
			except OSError:
				rep.add(f"{progid} ({label}): not registered")
				continue
			rep.add(f"{progid} ({label}): CLSID {clsid}")
			try:
				with winreg.OpenKey(
					winreg.HKEY_CLASSES_ROOT,
					rf"CLSID\{clsid}\InprocServer32",
					0,
					winreg.KEY_READ | flag,
				) as k:
					i = 0
					while True:
						try:
							name, val, _ = winreg.EnumValue(k, i)
						except OSError:
							break
						rep.add(f"    {name or '(default)'} = {val}")
						i += 1
			except OSError as e:
				rep.add(f"    InprocServer32 unreadable: {e}")


def section_installs(rep: Report) -> None:
	rep.section("PrairieLink / PrairieView files on disk")
	roots = [os.environ.get("ProgramFiles", r"C:\Program Files")]
	x86 = os.environ.get("ProgramFiles(x86)")
	if x86:
		roots.append(x86)
	seen = 0
	for root in roots:
		for pat in ("PrairieLink*.dll", "Prairie View.exe", "PrairieView.exe"):
			for p in sorted(glob.glob(os.path.join(root, "Prairie*", "**", pat), recursive=True)):
				seen += 1
				rep.add(file_line(p))
	if not seen:
		rep.add("nothing under Program Files\\Prairie*")


def _powershell(rep: Report, label: str, script: str) -> None:
	rep.section(label)
	out = subprocess.run(
		["powershell", "-NoProfile", "-Command", script],
		capture_output=True,
		text=True,
		timeout=60,
	)
	text = (out.stdout or "").strip() or (out.stderr or "").strip() or "(no output)"
	for ln in text.splitlines()[:40]:
		rep.add(ln.rstrip())


def section_windows_changes(rep: Report, days: int) -> None:
	_powershell(
		rep,
		f"Windows updates in the last {days} days",
		"Get-HotFix | Where-Object { $_.InstalledOn -gt (Get-Date).AddDays(-%d) } | "
		"Sort-Object InstalledOn | Format-Table -AutoSize HotFixID,Description,InstalledOn | "
		"Out-String -Width 160" % days,
	)
	_powershell(
		rep,
		f"Defender / exploit-guard events in the last {days} days",
		"Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Windows Defender/Operational';"
		"StartTime=(Get-Date).AddDays(-%d)} -MaxEvents 200 -ErrorAction SilentlyContinue | "
		"Where-Object { $_.Id -in 1006,1015,1116,1117,1121,1122,1123,1124,5007 } | "
		"Select-Object -First 20 | ForEach-Object { '{0:yyyy-MM-dd HH:mm} id={1} {2}' -f "
		"$_.TimeCreated,$_.Id,(($_.Message -split \"`n\")[0..3] -join ' ') }" % days,
	)


def section_raw_gi(rep: Report, host: str, password: str, channel: int) -> None:
	from prairie_live.gi_grab import GiGrabber
	from prairie_live.prairie_link import PrairieLink

	rep.section("direct script -gi (bypasses PrairieLink's buffer)")
	for low in (True, False):
		label = "low buffer (<2 GiB)" if low else "numpy buffer (any address)"
		g = GiGrabber(PrairieLink(host=host, password=password, timeout=15.0), low=low)
		try:
			h, w = g.fov_wh()
			rep.add(f"{label}: -gts says {w}x{h}")
			for ch in sorted({channel, 1, 2}):
				try:
					rep.add(f"  ch{ch}: {g.grab(ch, w, h).summary()}")
				except Exception as e:
					rep.add(f"  ch{ch}: ERROR {type(e).__name__}: {e}")
		finally:
			g.close()


def section_raw_rrd(rep: Report, host: str, password: str, wait_s: float) -> None:
	from prairie_live.gi_grab import PixelBuffer
	from prairie_live.prairie_link import PrairieLink

	rep.section("raw data stream -srd / -rrd (what ReadImage uses)")
	link = PrairieLink(host=host, password=password, timeout=15.0)
	buf = PixelBuffer(4 * 1024 * 1024, low=True, dtype=np.int16)
	try:
		rep.add(f"-srd True: {link.send_command('-srd', 'True')!r}")
		rep.add(f"Stop and start Live Scan in PV now; reading in {wait_s:.0f}s …")
		time.sleep(wait_s)
		for i in range(5):
			buf.fill_sentinel()
			reply = link.query("-rrd", str(os.getpid()), str(buf.addr), str(buf.n))
			rep.add(
				f"  -rrd #{i + 1} addr=0x{buf.addr:X}: reply={reply!r} "
				f"written={buf.count_written()}"
			)
			time.sleep(0.5)
	finally:
		try:
			rep.add(f"-srd False: {link.send_command('-srd', 'False')!r}")
		except Exception as e:
			rep.add(f"-srd False: ERROR {e}")
		buf.close()
		link.close()


def main(argv=None) -> None:
	p = argparse.ArgumentParser(prog="prairie_live grab-doctor")
	p.add_argument("--pv-host", default="127.0.0.1")
	p.add_argument("--password", default="0000")
	p.add_argument("--channel", type=int, default=1)
	p.add_argument("--days", type=int, default=14, help="look-back for updates/events")
	p.add_argument("--rrd", action="store_true", help="also test -srd/-rrd streaming")
	p.add_argument("--rrd-wait", type=float, default=8.0)
	p.add_argument("--out", default="grab_doctor.txt")
	args = p.parse_args(argv)

	rep = Report()
	rep.add(f"grab-doctor {time.strftime('%Y-%m-%d %H:%M:%S')}  host={args.pv_host}")
	rep.add("Leave Live Scan running in PV; watch PV for any error popup during -gi.")
	_guard(rep, "python", lambda: section_python(rep))
	if os.name == "nt":
		_guard(rep, "processes", lambda: section_prairie_processes(rep))
		_guard(rep, "registry", lambda: section_com_registration(rep))
		_guard(rep, "installs", lambda: section_installs(rep))
		_guard(rep, "windows", lambda: section_windows_changes(rep, args.days))
	_guard(rep, "raw -gi", lambda: section_raw_gi(rep, args.pv_host, args.password, args.channel))
	if args.rrd:
		_guard(rep, "raw -rrd", lambda: section_raw_rrd(rep, args.pv_host, args.password, args.rrd_wait))
	with open(args.out, "w", encoding="utf-8") as f:
		f.write(rep.text())
	print(f"\nreport written to {os.path.abspath(args.out)}")


if __name__ == "__main__":
	main()
