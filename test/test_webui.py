"""Web UI helpers: lab defaults, COM list, XML fields, relay probe (no network)."""

from __future__ import annotations

import socket
import threading

from prairie_live.com_ports import list_com_ports
from prairie_live.gui_session import apply_gui_overrides
from prairie_live.lab_defaults import DEFAULT_RELAY, DEFAULT_SERIAL, STIM_MODES, lab_defaults
from prairie_live.relay_status import parse_relay_addr, probe_relay
from prairie_live.power_view import board_state, render_board_png
from prairie_live.trial_images import png_bytes_rgb
from prairie_live.xml_summary import summarize_fat_xml


def test_lab_defaults_no_experiment_json():
	d = lab_defaults()
	assert d["via_relay"] == "10.33.107.147:25100"
	assert d["serial"] == "COM3"
	assert d["stim_mode"] == "slm"
	assert "slm" in STIM_MODES and "series" in STIM_MODES


def test_apply_overrides_without_json():
	opt = apply_gui_overrides(
		None,
		series="samples/MarkPoints_8fov.xml",
		powers=[75.0, 140.0],
		seed=1,
		repetitions=1,
		stim_dff_min=0.0,
		n_points=8,
		via_relay=DEFAULT_RELAY,
		serial=DEFAULT_SERIAL,
		stim_mode="slm",
	)
	assert opt["via_relay"] == DEFAULT_RELAY
	assert opt["serial"] == "COM3"
	assert opt["stim_mode"] == "slm"
	assert opt["cover_all"] is True
	assert opt["n_groups"] == 2


def test_com_ports_always_offer_com3():
	ports = list_com_ports()
	devs = {p["device"].upper() for p in ports}
	assert "COM3" in devs


def test_xml_summary_from_sample():
	s = summarize_fat_xml("samples/MarkPoints_8fov.xml")
	assert s["n_points"] == 8
	assert s["duration_ms"] is not None and s["duration_ms"] > 1
	assert s["inter_point_delay_ms"] == 10.0
	assert s["spiral"] is True
	assert s["spiral_size_um"] is not None
	assert s["fov_width_um"] is not None
	assert abs(s["fov_width_um"] - 1136.7) < 2.0
	assert s["pixel_size_um"] is not None
	assert "FOV" in s["fov_note"]


def test_parse_relay_addr():
	assert parse_relay_addr("10.33.107.147:25100") == ("10.33.107.147", 25100)
	assert parse_relay_addr("10.33.107.147") == ("10.33.107.147", 25100)


def test_probe_relay_green_when_ping_ok():
	got = {"line": None}

	def server():
		s = socket.socket()
		s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		s.bind(("127.0.0.1", 0))
		s.listen(1)
		port[0] = s.getsockname()[1]
		ready.set()
		c, _ = s.accept()
		got["line"] = c.recv(64)
		c.sendall(b'{"ok": true, "cmd": "ping"}\n')
		c.close()
		s.close()

	port = [0]
	ready = threading.Event()
	t = threading.Thread(target=server, daemon=True)
	t.start()
	assert ready.wait(2)
	# probe uses ctrl = port+1, so advertise port-1
	st = probe_relay(f"127.0.0.1:{port[0] - 1}", timeout=1.0)
	assert st["up"] is True
	assert st["pv"] is True
	assert b"ping" in (got["line"] or b"")


def test_probe_relay_down():
	st = probe_relay("127.0.0.1:1", timeout=0.2)
	assert st["up"] is False
	assert st["pv"] is False


def test_board_png_bytes():
	import numpy as np

	tiny = np.zeros((8, 8, 3), dtype=np.uint8)
	tiny[:] = 40
	png = png_bytes_rgb(tiny)
	assert png.startswith(b"\x89PNG")
	st = board_state(
		{
			"trial_index": 0,
			"power": 140,
			"point_ids": ["1"],
			"point_scores": {"1": 0.4},
		},
		[{"id": "1", "x": 0.2, "y": 0.3}, {"id": "2", "x": 0.8, "y": 0.8}],
		stim_dff_min=0.1,
	)
	out = render_board_png(st)
	assert out.startswith(b"\x89PNG")
