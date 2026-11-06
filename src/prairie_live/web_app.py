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
		# Offline random groups (manual PV run on scope PC).
		self.random_groups: list[dict] = []
		self.selected_group_ids: list[str] = []
		self.random_groups_power: float | None = None
		self.random_groups_reps: int | None = None
		self.random_groups_path: str | None = None
		self.random_groups_meta: dict[str, Any] = {}
		# Ordered draws from group-catalog Generate (length N); empty for pool mode.
		self.group_sequence: list[dict] = []

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
			with STATE.lock:
				groups = list(STATE.random_groups or [])
				sel = set(STATE.selected_group_ids or [])
			# Color Mark Points by random-group membership (no centroid circles —
			# group averages sit near FOV center and are hard to pick).
			by_pid: dict[str, list[dict[str, Any]]] = {}
			g_rows: list[dict[str, Any]] = []
			for g in groups:
				rgb = [int(x) for x in (g.get("rgb") or [0, 255, 255])]
				gid = str(g["id"])
				row = {
					"id": gid,
					"name": str(g.get("name") or f"G{gid}"),
					"point_ids": list(g.get("point_ids") or []),
					"rgb": rgb,
					"selected": gid in sel,
				}
				g_rows.append(row)
				for pid in row["point_ids"]:
					by_pid.setdefault(str(pid), []).append(row)
			for p in out.get("points") or []:
				membership = by_pid.get(str(p.get("id")), [])
				p["group_ids"] = [m["id"] for m in membership]
				if not membership:
					continue
				picked = next((m for m in membership if m["selected"]), membership[0])
				p["rgb"] = list(picked["rgb"])
				p["group_selected"] = any(m["selected"] for m in membership)
			out["groups"] = g_rows
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
						"random_groups": [
							{
								"id": g["id"],
								"name": g.get("name"),
								"point_ids": g.get("point_ids"),
								"rgb": g.get("rgb"),
							}
							for g in STATE.random_groups
						],
						"group_sequence": [
							{
								"ord": g.get("ord"),
								"id": g["id"],
								"name": g.get("name"),
								"point_ids": g.get("point_ids"),
								"rgb": g.get("rgb"),
							}
							for g in STATE.group_sequence
						],
						"selected_group_ids": list(STATE.selected_group_ids),
						"random_groups_power": STATE.random_groups_power,
						"random_groups_path": STATE.random_groups_path,
						"ranked": [
							{
								"id": r["id"],
								"key": r.get("key") or r["id"],
								"power": r.get("power", r.get("min_power")),
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
		if u.path == "/api/live-baseline":
			try:
				snap = STATE.live.request_baseline()
				_json(self, {"ok": True, **snap})
			except Exception as exc:
				_json(self, {"ok": False, "error": str(exc)}, 400)
			return
		if u.path == "/api/live-mode":
			try:
				body = json.loads(raw.decode("utf-8") or "{}")
				snap = STATE.live.set_mode(str(body.get("mode") or "raw"))
				_json(self, {"ok": True, **snap})
			except Exception as exc:
				_json(self, {"ok": False, "error": str(exc)}, 400)
			return
		if u.path == "/api/series-kind":
			self._series_kind(raw)
			return
		if u.path == "/api/stop":
			STATE.stop.set()
			STATE.append("stop requested — finishing current trial")
			_json(self, {"ok": True})
			return
		if u.path == "/api/save-candidates":
			self._save(raw)
			return
		if u.path == "/api/random-groups":
			self._random_groups(raw)
			return
		if u.path == "/api/select-groups":
			self._select_groups(raw)
			return
		if u.path == "/api/selected-groups-xml":
			self._selected_groups_xml(raw)
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
				raise ValueError("choose a Mark Points XML")
			from prairie_live.random_groups import (
				catalog_groups_from_steps,
				classify_series,
			)

			steps = load_mark_points_file(path)
			kind = classify_series(steps)
			fields = summarize_fat_xml(path)
			if kind == "group_catalog":
				catalog = catalog_groups_from_steps(steps)
				try:
					pts = extract_unique_points(steps)
				except ValueError:
					pts = []
				# Slim Group/Indices XML has no coords — keep the previously
				# loaded fat Mark Points pool for Live overlay / optional .gpl.
				if not pts:
					with STATE.lock:
						prior = list(STATE.points or [])
					if prior:
						pts = prior
						fields = dict(fields)
						fields["fov_note"] = (
							f"{fields.get('fov_note') or 'Group catalog'} "
							f"(keeping {len(pts)} points from earlier fat XML)"
						)
				ng = len(catalog)
				sizes: list[int] = [len(g["point_ids"]) for g in catalog]
				with STATE.lock:
					STATE.random_groups = catalog
					STATE.selected_group_ids = []
					STATE.group_sequence = []
			else:
				pts = extract_unique_points(steps)
				ng = n_groups_for_pool(len(pts))
				sizes = group_sizes_for_pool(len(pts), n_groups=ng)
				with STATE.lock:
					STATE.group_sequence = []
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
					"kind": kind,
					"n_groups": ng,
					"group_sizes": sizes,
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
			# Prefer row keys (id@power); bare ids still match for older clients.
			want = {str(i) for i in (body.get("ids") or body.get("keys") or [])}
			sel = [
				r
				for r in STATE.ranked
				if str(r.get("key") or r["id"]) in want or str(r["id"]) in want
			]
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

	def _series_meta(self) -> dict[str, Any]:
		from prairie_live.markpoints import template_meta

		series = STATE.series_path
		if series and Path(series).is_file():
			return template_meta(load_mark_points_file(series))
		return dict(STATE.random_groups_meta or template_meta([]))

	def _series_kind(self, raw: bytes) -> None:
		from prairie_live.markpoints import template_meta
		from prairie_live.random_groups import (
			catalog_groups_from_steps,
			classify_series,
		)

		try:
			body = json.loads(raw.decode("utf-8") or "{}")
			series = body.get("series") or STATE.series_path
			if not series or not Path(series).is_file():
				raise ValueError("choose a Fat Mark Points XML first")
			steps = load_mark_points_file(series)
			kind = classify_series(steps)
			n_groups = 0
			n_points = 0
			slim = False
			initial_delay = "22.1"
			if kind == "group_catalog":
				cat = catalog_groups_from_steps(steps)
				n_groups = len(cat)
				n_points = len({pid for g in cat for pid in g["point_ids"]})
				slim = all(bool(g.get("slim")) for g in cat)
				src0 = (cat[0].get("source_step") or {}) if cat else {}
				if src0.get("initial_delay") not in (None, ""):
					initial_delay = str(src0["initial_delay"])
				else:
					meta = template_meta(steps)
					if meta.get("initial_delay") not in (None, ""):
						initial_delay = str(meta["initial_delay"])
			else:
				pts = extract_unique_points(steps)
				n_points = len(pts)
			_json(
				self,
				{
					"ok": True,
					"kind": kind,
					"n_groups": n_groups,
					"n_points": n_points,
					"slim": slim,
					"initial_delay": initial_delay,
					"series": str(series),
				},
			)
		except Exception as exc:
			_json(self, {"ok": False, "error": str(exc)}, 400)

	def _random_groups(self, raw: bytes) -> None:
		from prairie_live.markpoints import template_meta
		from prairie_live.random_groups import (
			build_group_sequence,
			build_random_groups,
			catalog_groups_from_steps,
			catalog_power,
			classify_series,
			export_timestamp,
			groups_gpl_text,
			groups_xml_text,
			sequence_xml_text,
			write_groups_gpl,
			write_groups_xml,
			write_sequence_xml,
		)

		try:
			body = json.loads(raw.decode("utf-8") or "{}")
			series = body.get("series") or STATE.series_path
			if not series or not Path(series).is_file():
				raise ValueError("choose a Fat Mark Points XML first")
			seed_raw = body.get("seed")
			seed = int(seed_raw) if seed_raw not in (None, "") else None
			steps = load_mark_points_file(series)
			kind = str(body.get("mode") or classify_series(steps))
			if kind in ("sequence", "group_catalog"):
				self._random_group_sequence(
					body,
					series=str(series),
					steps=steps,
					seed=seed,
					template_meta=template_meta,
					catalog_groups_from_steps=catalog_groups_from_steps,
					build_group_sequence=build_group_sequence,
					catalog_power=catalog_power,
					sequence_xml_text=sequence_xml_text,
					write_sequence_xml=write_sequence_xml,
					groups_gpl_text=groups_gpl_text,
					write_groups_gpl=write_groups_gpl,
				)
				return
			n_groups = int(body.get("n_groups") or 0)
			power = float(body.get("power") if body.get("power") not in (None, "") else 160)
			reps_raw = body.get("repetitions")
			repetitions = (
				int(reps_raw) if reps_raw not in (None, "") else 1
			)
			if repetitions < 1:
				raise ValueError("repetitions must be >= 1")
			pts = extract_unique_points(steps)
			meta = template_meta(steps)
			meta["repetitions"] = repetitions
			groups = build_random_groups(pts, n_groups, seed=seed)
			xml_text = groups_xml_text(groups, power=power, meta=meta)
			gpl_text = groups_gpl_text(groups, power=power, meta=meta)
			ts = export_timestamp()
			base_name = f"random_groups_{ts}"
			save_path = body.get("path") or body.get("save_path")
			path_str: str | None = None
			gpl_path: str | None = None
			if save_path:
				xml_path = Path(str(save_path))
				# Keep user path stem but ensure .xml; stamp only when using defaults.
				path_str = str(
					write_groups_xml(groups, power=power, meta=meta, path=xml_path)
				)
				gpl_path = str(
					write_groups_gpl(
						groups,
						power=power,
						meta=meta,
						path=xml_path.with_suffix(".gpl"),
					)
				)
			else:
				dest = STATE.xml_dir / f"{base_name}.xml"
				path_str = str(
					write_groups_xml(groups, power=power, meta=meta, path=dest)
				)
				gpl_path = str(
					write_groups_gpl(
						groups,
						power=power,
						meta=meta,
						path=dest.with_suffix(".gpl"),
					)
				)
			with STATE.lock:
				STATE.points = pts
				STATE.series_path = series
				STATE.random_groups = groups
				STATE.selected_group_ids = []
				STATE.group_sequence = []
				STATE.random_groups_power = power
				STATE.random_groups_reps = repetitions
				STATE.random_groups_path = path_str
				STATE.random_groups_meta = meta
			covered = {pid for g in groups for pid in g["point_ids"]}
			pool_ids = {str(p["id"]) for p in pts}
			STATE.append(
				f"random groups  n={len(groups)}  size=3  power={power:g}  "
				f"reps={repetitions}  cover={len(covered)}/{len(pool_ids)}  "
				f"→ {path_str} + {gpl_path}"
			)
			_json(
				self,
				{
					"ok": True,
					"mode": "points_pool",
					"path": path_str,
					"gpl_path": gpl_path,
					"n_groups": len(groups),
					"n_points": len(pool_ids),
					"n_covered": len(covered),
					"power": power,
					"repetitions": repetitions,
					"xml": xml_text,
					"gpl": gpl_text,
					"timestamp": ts,
					"filename": f"{base_name}.xml",
					"gpl_filename": f"{base_name}.gpl",
					"groups": [
						{
							"id": g["id"],
							"name": g["name"],
							"point_ids": g["point_ids"],
							"rgb": g["rgb"],
						}
						for g in groups
					],
				},
			)
		except Exception as exc:
			_json(self, {"ok": False, "error": str(exc)}, 400)

	def _random_group_sequence(self, body: dict, **kw: Any) -> None:
		from prairie_live.random_groups import sequence_is_slim

		series = kw["series"]
		steps = kw["steps"]
		seed = kw["seed"]
		template_meta = kw["template_meta"]
		catalog_groups_from_steps = kw["catalog_groups_from_steps"]
		build_group_sequence = kw["build_group_sequence"]
		catalog_power = kw["catalog_power"]
		sequence_xml_text = kw["sequence_xml_text"]
		write_sequence_xml = kw["write_sequence_xml"]
		groups_gpl_text = kw["groups_gpl_text"]
		write_groups_gpl = kw["write_groups_gpl"]

		from prairie_live.random_groups import export_timestamp

		n_raw = body.get("n_sequences", body.get("n_groups"))
		n_seq = int(n_raw or 0)
		if n_seq < 1:
			raise ValueError("n_sequences must be >= 1")
		catalog = catalog_groups_from_steps(steps)
		meta = template_meta(steps)
		delay_raw = body.get("initial_delay")
		if delay_raw not in (None, ""):
			meta["initial_delay"] = str(delay_raw)
		sequence = build_group_sequence(catalog, n_seq, seed=seed)
		power = catalog_power(catalog)
		xml_text = sequence_xml_text(sequence, meta=meta)
		slim = sequence_is_slim(sequence)
		# Slim Indices/Group series has no coords — companion .gpl cannot be built;
		# user keeps the existing Galvo Point List loaded in PrairieView.
		gpl_text: str | None = None
		gpl_path: str | None = None
		ts = export_timestamp()
		base_name = f"group_sequence_{ts}"
		save_path = body.get("path") or body.get("save_path")
		if save_path:
			xml_path = Path(str(save_path))
			path_str = str(write_sequence_xml(sequence, meta=meta, path=xml_path))
			if not slim:
				gpl_path = str(
					write_groups_gpl(
						catalog,
						power=power,
						meta=meta,
						path=xml_path.with_suffix(".gpl"),
					)
				)
				gpl_text = groups_gpl_text(catalog, power=power, meta=meta)
		else:
			dest = STATE.xml_dir / f"{base_name}.xml"
			path_str = str(write_sequence_xml(sequence, meta=meta, path=dest))
			if not slim:
				gpl_path = str(
					write_groups_gpl(
						catalog, power=power, meta=meta, path=dest.with_suffix(".gpl")
					)
				)
				gpl_text = groups_gpl_text(catalog, power=power, meta=meta)
		try:
			pts = extract_unique_points(steps)
		except ValueError:
			pts = []
		n_points = len(pts) or len({pid for g in catalog for pid in g["point_ids"]})
		seq_rows = [
			{
				"ord": i,
				"id": str(g["id"]),
				"name": str(g.get("name") or f"G{g['id']}"),
				"point_ids": list(g.get("point_ids") or []),
				"rgb": list(g.get("rgb") or []),
			}
			for i, g in enumerate(sequence, start=1)
		]
		# Overlay uses catalog colors; bottom box shows the ordered sequence.
		with STATE.lock:
			STATE.points = pts
			STATE.series_path = series
			STATE.random_groups = catalog
			STATE.group_sequence = seq_rows
			STATE.selected_group_ids = []
			STATE.random_groups_power = power
			STATE.random_groups_reps = int(meta.get("repetitions") or 1)
			STATE.random_groups_path = path_str
			STATE.random_groups_meta = meta
		gpl_bit = f" + {gpl_path}" if gpl_path else " (no .gpl; keep PV galvo list)"
		STATE.append(
			f"group sequence  n={n_seq}  catalog={len(catalog)}  "
			f"power={power:g}  → {path_str}{gpl_bit}"
		)
		_json(
			self,
			{
				"ok": True,
				"mode": "sequence",
				"slim": slim,
				"path": path_str,
				"gpl_path": gpl_path,
				"n_groups": n_seq,
				"n_sequences": n_seq,
				"n_catalog": len(catalog),
				"n_points": n_points,
				"power": power,
				"xml": xml_text,
				"gpl": gpl_text,
				"timestamp": ts,
				"initial_delay": meta.get("initial_delay"),
				"filename": f"{base_name}.xml",
				"gpl_filename": f"{base_name}.gpl" if gpl_text else None,
				"groups": [
					{
						"id": g["id"],
						"name": g["name"],
						"point_ids": g["point_ids"],
						"rgb": g["rgb"],
					}
					for g in catalog
				],
				"sequence": seq_rows,
				"sequence_ids": [str(g["id"]) for g in sequence],
			},
		)

	def _select_groups(self, raw: bytes) -> None:
		try:
			body = json.loads(raw.decode("utf-8") or "{}")
			ids = [str(i) for i in (body.get("ids") or [])]
			known = {str(g["id"]) for g in STATE.random_groups}
			bad = [i for i in ids if i not in known]
			if bad and STATE.random_groups:
				raise ValueError(f"unknown group id(s): {', '.join(bad)}")
			with STATE.lock:
				STATE.selected_group_ids = ids
			_json(self, {"ok": True, "ids": ids})
		except Exception as exc:
			_json(self, {"ok": False, "error": str(exc)}, 400)

	def _selected_groups_xml(self, raw: bytes) -> None:
		from prairie_live.random_groups import (
			export_timestamp,
			groups_gpl_text,
			groups_xml_text,
			select_groups_by_id,
			write_groups_gpl,
			write_groups_xml,
		)

		try:
			body = json.loads(raw.decode("utf-8") or "{}")
			ids = body.get("ids") or list(STATE.selected_group_ids)
			if not ids:
				raise ValueError("no groups selected")
			if STATE.random_groups_power is None:
				raise ValueError("generate random groups first")
			picked = select_groups_by_id(STATE.random_groups, ids)
			meta = STATE.random_groups_meta or self._series_meta()
			power = float(STATE.random_groups_power)
			# Series = selected groups only; companion .gpl = full renumbered
			# pool so Indices in the series still resolve (Point N ↔ Index N).
			catalog = STATE.random_groups or picked
			xml_text = groups_xml_text(picked, power=power, meta=meta)
			gpl_text = groups_gpl_text(catalog, power=power, meta=meta)
			ts = export_timestamp()
			base_name = f"selected_groups_{ts}"
			save_path = body.get("path") or body.get("save_path")
			if save_path:
				path = write_groups_xml(
					picked, power=power, meta=meta, path=Path(str(save_path))
				)
				gpl_path = write_groups_gpl(
					catalog, power=power, meta=meta, path=path.with_suffix(".gpl")
				)
			else:
				path = write_groups_xml(
					picked,
					power=power,
					meta=meta,
					path=STATE.xml_dir / f"{base_name}.xml",
				)
				gpl_path = write_groups_gpl(
					catalog,
					power=power,
					meta=meta,
					path=path.with_suffix(".gpl"),
				)
			STATE.append(f"selected groups ({len(picked)}) → {path} + {gpl_path}")
			_json(
				self,
				{
					"ok": True,
					"path": str(path),
					"gpl_path": str(gpl_path),
					"n_groups": len(picked),
					"xml": xml_text,
					"gpl": gpl_text,
					"timestamp": ts,
					"filename": f"{base_name}.xml",
					"gpl_filename": f"{base_name}.gpl",
				},
			)
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
