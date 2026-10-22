"""Stdlib HTTP UI: form, relay light, COM ports, XML fields, run loop."""

from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from prairie_live.com_ports import list_com_ports, pick_serial
from prairie_live.gui_session import (
	apply_gui_overrides,
	group_sizes_for_pool,
	n_groups_for_pool,
	parse_powers_field,
	planned_trial_count,
	trial_log_line,
)
from prairie_live.lab_defaults import DEFAULT_RELAY, DEFAULT_SERIAL, STIM_MODES, TRIGGERS, lab_defaults
from prairie_live.live_feed import LiveFeed
from prairie_live.markpoints import extract_unique_points, load_mark_points_file
from prairie_live.power_view import board_state, render_board_png, unique_powers
from prairie_live.relay_status import probe_relay
from prairie_live.xml_summary import summarize_fat_xml

WEBUI = Path(__file__).resolve().parent / "webui"


class AppState:
	def __init__(self) -> None:
		self.lock = threading.Lock()
		self.log: list[str] = []
		self.running = False
		self.trials: list[dict] = []
		self.ranked: list[dict] = []
		self.points: list[dict] = []
		self.opt: dict[str, Any] = {}
		self.xml_fields: dict[str, Any] = {}
		self.series_path: str | None = None
		self.stop = threading.Event()
		self.q: queue.Queue = queue.Queue()
		self.error: str | None = None
		self.xml_dir = Path("trial_images") / "uploads"
		self.xml_dir.mkdir(parents=True, exist_ok=True)
		self.boards: dict[float, dict[str, Any]] = {}
		self.planned_powers: list[float] = []
		self.n_trials: int | None = None
		self.live = LiveFeed(DEFAULT_RELAY)

	def append(self, line: str) -> None:
		with self.lock:
			self.log.append(line.rstrip())
			self.log = self.log[-400:]


STATE = AppState()


def _json(handler: BaseHTTPRequestHandler, obj: Any, status: int = 200) -> None:
	raw = json.dumps(obj).encode("utf-8")
	handler.send_response(status)
	handler.send_header("Content-Type", "application/json")
	handler.send_header("Content-Length", str(len(raw)))
	handler.end_headers()
	handler.wfile.write(raw)


def _file(handler: BaseHTTPRequestHandler, path: Path, ctype: str) -> None:
	data = path.read_bytes()
	handler.send_response(200)
	handler.send_header("Content-Type", ctype)
	handler.send_header("Cache-Control", "no-store")
	handler.send_header("Content-Length", str(len(data)))
	handler.end_headers()
	handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
	def log_message(self, fmt: str, *args) -> None:
		print(f"webui {self.address_string()} {fmt % args}")

	def do_GET(self) -> None:
		u = urlparse(self.path)
		if u.path in ("/", "/index.html"):
			_file(self, WEBUI / "index.html", "text/html; charset=utf-8")
			return
		if u.path == "/app.js":
			_file(self, WEBUI / "app.js", "application/javascript")
			return
		if u.path == "/style.css":
			_file(self, WEBUI / "style.css", "text/css")
			return
		if u.path == "/api/defaults":
			d = lab_defaults()
			ports = list_com_ports()
			d["serial"] = pick_serial(ports, d["serial"])
			_json(
				self,
				{
					"via_relay": d["via_relay"],
					"serial": d["serial"],
					"stim_mode": d["stim_mode"],
					"stim_modes": list(STIM_MODES),
					"trigger": d["trigger"],
					"triggers": list(TRIGGERS),
					"powers": "",
					"seed": d["seed"],
					"repetitions": d["repetitions"],
					"stim_dff_min": d["stim_dff_min"],
					"com_ports": ports,
					"relay": probe_relay(DEFAULT_RELAY),
				},
			)
			return
		if u.path == "/api/com-ports":
			_json(self, {"ports": list_com_ports(), "default": DEFAULT_SERIAL})
			return
		if u.path == "/api/relay-status":
			q = parse_qs(u.query)
			addr = (q.get("addr") or [DEFAULT_RELAY])[0]
			_json(self, probe_relay(addr))
			return
		if u.path == "/api/live.png":
			png = STATE.live.png_bytes()
			if not png:
				self.send_error(204)
				return
			self.send_response(200)
			self.send_header("Content-Type", "image/png")
			self.send_header("Cache-Control", "no-store")
			self.send_header("Content-Length", str(len(png)))
			self.end_headers()
			self.wfile.write(png)
			return
		if u.path == "/api/board.png":
			q = parse_qs(u.query)
			try:
				pwr = float((q.get("power") or ["0"])[0])
			except ValueError:
				self.send_error(400)
				return
			with STATE.lock:
				st = STATE.boards.get(pwr)
			if not st:
				self.send_error(204)
				return
			png = render_board_png(st)
			self.send_response(200)
			self.send_header("Content-Type", "image/png")
			self.send_header("Cache-Control", "no-store")
			self.send_header("Content-Length", str(len(png)))
			self.end_headers()
			self.wfile.write(png)
			return
		if u.path == "/api/points":
			from prairie_live.scan_geometry import overlay_points

			geom, wh = STATE.live.scan_geometry()
			out = overlay_points(
				list(STATE.points or []), geom, STATE.live.scan_geometry_error()
			)
			out["frame_wh"] = list(wh) if wh else None
			_json(self, out)
			return
		if u.path == "/api/status":
			self._drain()
			with STATE.lock:
				boards = [
					{
						"power": p,
						"title": (STATE.boards.get(p) or {}).get("title")
						or f"power {p:g}  waiting…",
						"ready": p in STATE.boards,
					}
					for p in (STATE.planned_powers or list(STATE.boards.keys()))
				]
				_json(
					self,
					{
						"running": STATE.running,
						"log": list(STATE.log),
						"error": STATE.error,
						"n_trials": len(STATE.trials),
						"has_candidates": bool(STATE.ranked),
						"live": STATE.live.snapshot(),
						"boards": boards,
						"ranked": [
							{
								"id": r["id"],
								"min_power": r.get("min_power"),
								"dff": r.get("dff"),
								"tercile": r.get("tercile"),
								"stimulated": r.get("stimulated"),
								"x": r.get("x"),
								"y": r.get("y"),
							}
							for r in STATE.ranked
						],
					},
				)
			return
		self.send_error(404)

	def do_POST(self) -> None:
		u = urlparse(self.path)
		n = int(self.headers.get("Content-Length") or 0)
		raw = self.rfile.read(n) if n else b""
		if u.path == "/api/parse-xml":
			self._parse_xml(raw)
			return
		if u.path == "/api/run":
			self._run(raw)
			return
		if u.path == "/api/live-addr":
			try:
				body = json.loads(raw.decode("utf-8") or "{}")
				addr = str(body.get("addr") or DEFAULT_RELAY)
				STATE.live.set_addr(addr)
				_json(self, {"ok": True, "addr": addr, **STATE.live.snapshot()})
			except Exception as exc:
				_json(self, {"ok": False, "error": str(exc)}, 400)
			return
		if u.path == "/api/stop":
			STATE.stop.set()
			STATE.append("stop requested — finishing current trial")
			_json(self, {"ok": True})
			return
		if u.path == "/api/save-candidates":
			self._save(raw)
			return
		self.send_error(404)

	def _parse_xml(self, raw: bytes) -> None:
		try:
			body = json.loads(raw.decode("utf-8") or "{}")
			path = body.get("path")
			b64 = body.get("content_b64")
			name = body.get("name") or "upload.xml"
			if b64:
				import base64

				STATE.xml_dir.mkdir(parents=True, exist_ok=True)
				path = str(STATE.xml_dir / Path(name).name)
				Path(path).write_bytes(base64.b64decode(b64))
			if not path or not Path(path).is_file():
				raise ValueError("choose a Fat Mark Points XML")
			fields = summarize_fat_xml(path)
			pts = extract_unique_points(load_mark_points_file(path))
			ng = n_groups_for_pool(len(pts))
			sizes = group_sizes_for_pool(len(pts), n_groups=ng)
			STATE.series_path = path
			STATE.xml_fields = fields
			STATE.points = pts
			if body.get("powers"):
				STATE.planned_powers = unique_powers(parse_powers_field(str(body["powers"])))
				STATE.boards = {}
			_json(
				self,
				{
					"ok": True,
					"path": path,
					"fields": fields,
					"n_groups": ng,
					"group_sizes": sizes,
					"plan": f"{len(pts)} points → {len(sizes)} groups ({'+'.join(map(str, sizes))})",
				},
			)
		except Exception as exc:
			_json(self, {"ok": False, "error": str(exc)}, 400)

	def _run(self, raw: bytes) -> None:
		if STATE.running:
			_json(self, {"ok": False, "error": "already running"}, 409)
			return
		try:
			body = json.loads(raw.decode("utf-8"))
			series = body.get("series") or STATE.series_path
			if not series or not Path(series).is_file():
				raise ValueError("choose a Fat Mark Points XML")
			powers = parse_powers_field(str(body.get("powers") or ""))
			pts = extract_unique_points(load_mark_points_file(series))
			fields = summarize_fat_xml(series)
			opt = apply_gui_overrides(
				None,
				series=series,
				powers=powers,
				seed=int(body.get("seed") or 0),
				repetitions=int(body.get("repetitions") or 1),
				stim_dff_min=float(body.get("stim_dff_min") or 0.0),
				n_points=len(pts),
				via_relay=body.get("via_relay") or DEFAULT_RELAY,
				serial=body.get("serial") or DEFAULT_SERIAL,
				stim_mode=body.get("stim_mode") or "slm",
				trigger=body.get("trigger") or "serial",
				xml_fields=fields,
			)
			n_t = planned_trial_count(len(pts), powers, n_groups=int(opt["n_groups"]))
			STATE.stop.clear()
			STATE.running = True
			STATE.error = None
			STATE.trials = []
			STATE.ranked = []
			STATE.points = pts
			STATE.opt = opt
			STATE.planned_powers = unique_powers(powers)
			STATE.n_trials = n_t
			STATE.boards = {}
			STATE.live.set_addr(str(opt.get("via_relay") or DEFAULT_RELAY))
			STATE.append(
				f"start  {len(pts)} pts  groups={opt['n_groups']}  "
				f"every point at each of {powers}  relay={opt['via_relay']}  "
				f"COM={opt['serial']}  mode={opt['stim_mode']}  "
				f"trigger={opt['trigger']}  trials={n_t}"
			)

			def work() -> None:
				from prairie_live.sync_loop import execute_session

				try:
					trials = execute_session(
						opt,
						on_trial=lambda row: STATE.q.put(("trial", dict(row))),
						show_candidate_gui=False,
						should_stop=STATE.stop.is_set,
					)
					STATE.q.put(("done", {"trials": trials}))
				except Exception as exc:
					STATE.q.put(("error", str(exc)))

			threading.Thread(target=work, daemon=True).start()
			_json(self, {"ok": True, "n_trials": n_t})
		except Exception as exc:
			STATE.running = False
			_json(self, {"ok": False, "error": str(exc)}, 400)

	def _save(self, raw: bytes) -> None:
		from prairie_live.candidates import write_candidates_xml
		from prairie_live.experiment_config import apply_stim_to_meta
		from prairie_live.markpoints import template_meta

		try:
			body = json.loads(raw.decode("utf-8") or "{}")
			ids = {str(i) for i in (body.get("ids") or [])}
			sel = [r for r in STATE.ranked if str(r["id"]) in ids]
			if not sel:
				raise ValueError("no points selected")
			out = Path(STATE.opt.get("images_dir") or "trial_images")
			for row in reversed(STATE.trials):
				td = (row.get("image_paths") or {}).get("trial_dir")
				if td:
					out = Path(td).parent
					break
			path = write_candidates_xml(
				sel, apply_stim_to_meta(template_meta([]), STATE.opt), out / "next_series.xml"
			)
			STATE.append(f"next-round XML → {path}")
			_json(self, {"ok": True, "path": str(path)})
		except Exception as exc:
			_json(self, {"ok": False, "error": str(exc)}, 400)

	def _drain(self) -> None:
		from prairie_live.candidates import rank_candidate_points

		try:
			while True:
				kind, payload = STATE.q.get_nowait()
				if kind == "trial":
					STATE.append(trial_log_line(payload))
					from prairie_live.scan_geometry import points_for_image

					geom, _ = STATE.live.scan_geometry()
					st = board_state(
						payload,
						points_for_image(list(STATE.points or []), geom),
						stim_dff_min=float(STATE.opt.get("stim_dff_min") or 0.0),
						n_trials=STATE.n_trials,
					)
					with STATE.lock:
						STATE.trials.append(payload)
						STATE.boards[float(st["power"])] = st
				elif kind == "done":
					trials = payload.get("trials") or []
					with STATE.lock:
						STATE.trials = trials
						STATE.ranked = rank_candidate_points(
							trials,
							STATE.points,
							stim_dff_min=float(STATE.opt.get("stim_dff_min") or 0.0),
						)
						STATE.running = False
					STATE.append(f"done  {len(trials)} trials — pick candidates")
				elif kind == "error":
					STATE.running = False
					STATE.error = str(payload)
					STATE.append(f"error: {payload}")
		except queue.Empty:
			pass


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
	STATE.live.start()
	httpd = ThreadingHTTPServer((host, port), Handler)
	url = f"http://{host}:{port}/"
	print(f"web UI → {url}")
	try:
		import webbrowser

		webbrowser.open(url)
	except Exception:
		pass
	httpd.serve_forever()
