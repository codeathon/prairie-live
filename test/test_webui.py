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
	assert d["via_relay"] == "10.33.107.221:25100"
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


def test_com_ports_never_empty():
	assert list_com_ports()


def test_pick_serial_uses_a_port_this_pc_has():
	from prairie_live.com_ports import pick_serial

	only_com1 = [{"device": "COM1", "description": "Communications Port (COM1)"}]
	assert pick_serial(only_com1, "COM3") == "COM1"
	both = only_com1 + [{"device": "COM3", "description": "USB"}]
	assert pick_serial(both, "COM3") == "COM3"
	assert pick_serial([], "COM3") == "COM3"


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


def _http_server():
	from http.server import ThreadingHTTPServer

	from prairie_live.web_app import Handler

	httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
	threading.Thread(target=httpd.serve_forever, daemon=True).start()
	host, port = httpd.server_address
	return httpd, f"http://{host}:{port}"


def _get(url: str):
	import urllib.request

	with urllib.request.urlopen(url, timeout=5) as r:
		return r.status, r.headers.get_content_type(), r.read()


def _post(url: str, obj: dict):
	import json
	import urllib.request

	req = urllib.request.Request(
		url,
		data=json.dumps(obj).encode("utf-8"),
		headers={"Content-Type": "application/json"},
		method="POST",
	)
	with urllib.request.urlopen(req, timeout=5) as r:
		return r.status, json.loads(r.read().decode("utf-8"))


def test_web_ui_serves_page_not_tk_form():
	httpd, base = _http_server()
	try:
		status, ctype, body = _get(base + "/")
		assert status == 200
		assert "html" in ctype
		text = body.decode("utf-8")
		assert "prairie-live mapping" in text
		assert "Live scan" in text
		assert "tile-head" in text
		assert "fov-wrap" in text
		assert "tile-caps" in text
		assert "Fat Mark Points XML" in text
		assert "tkinter" not in text.lower()
		assert 'src="/app.js"' in text
		js_status, js_ctype, js = _get(base + "/app.js")
		assert js_status == 200
		assert b"ensurePowerTiles" in js
		assert b'tile-head' in js
		assert b"fov-wrap" in js
		css_status, _, css = _get(base + "/style.css")
		assert css_status == 200
		assert b".fov-grid" in css
		assert b".tile-caps" in css
	finally:
		httpd.shutdown()


def test_web_ui_parse_xml_opens_power_plan(tmp_path):
	import base64
	import json
	from pathlib import Path

	from prairie_live.web_app import STATE

	STATE.xml_dir = tmp_path
	httpd, base = _http_server()
	try:
		raw = Path("samples/MarkPoints_8fov.xml").read_bytes()
		status, res = _post(
			base + "/api/parse-xml",
			{
				"name": "MarkPoints_8fov.xml",
				"content_b64": base64.b64encode(raw).decode("ascii"),
				"powers": "75,140,200",
			},
		)
		assert status == 200
		assert res["ok"] is True
		assert res["n_groups"] == 2
		assert res["group_sizes"] == [4, 4]
		assert "8 points" in res["plan"]
		assert res["fields"]["n_points"] == 8
		assert STATE.planned_powers == [75.0, 140.0, 200.0]
		st_status, _, st_raw = _get(base + "/api/status")
		assert st_status == 200
		st = json.loads(st_raw.decode("utf-8"))
		assert [b["power"] for b in st["boards"]] == [75.0, 140.0, 200.0]
		assert all(b["ready"] is False for b in st["boards"])
	finally:
		httpd.shutdown()


def test_gui_trigger_none_overrides_serial():
	from prairie_live.gui_session import apply_gui_overrides

	opt = apply_gui_overrides(
		series="x.gpl",
		powers=[160.0],
		seed=0,
		repetitions=1,
		stim_dff_min=0.0,
		n_points=6,
		serial="COM3",
		stim_mode="series",
		trigger="none",
	)
	assert opt["trigger"] == "none"
	assert opt["stim_mode"] == "series"
