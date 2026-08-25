"""
Closed-loop Mark Points controller for Bruker Prairie View 5.8.

Uses only official script commands from the PV Script Command Reference:
  -LoadMarkPoints (-lmp)  load a series .xml or point-list .gpl
  -MarkPoints (-mp)       run the current series (or a saved Category/Name)
  -GetState (-gts)        query a named state key (not used to list points)
  -Abort (-stop)          abort Mark Points / scans
  -Exit (-x)              close the TCP session

There is no -GetMarkPoints / -RunMarkPoints. Groups are read from a local
series XML (or GPL), mutated in memory, written to a temp file, then
re-loaded with -lmp. Fire with bare -MarkPoints (current series).

TCP wire format (direct socket, not PrairieLink COM):
  password first, then commands with SOH (ASCII 1) between tokens, CRLF
  terminator; responses are ACK ... [data] ... DONE.

Usage:
    pl = PrairieLink(password="0000")
    groups = parse_mark_points(open("series.xml").read())
    run_closed_loop(pl, groups, iterations=10)
    pl.close()
"""

from __future__ import annotations

import argparse
import os
import socket
import tempfile
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Set, Tuple

from prairie_live.tcp_backend import DEFAULT_PORT

SOH = "\x01"


# ---------------------------------------------------------------------------
# PrairieLink TCP client (official framing)
# ---------------------------------------------------------------------------


class PrairieLink:
	"""TCP client for Prairie View's script port (scope PC: 127.0.0.1:1236)."""

	def __init__(
		self,
		host: str = "127.0.0.1",
		port: int = DEFAULT_PORT,
		password: str = "0000",
		timeout: float = 10.0,
	):
		self.host = host
		self.port = port
		self.password = password
		self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.sock.settimeout(timeout)
		self.sock.connect((host, port))
		# Password must be the first thing on the wire or PV drops the socket.
		self._send_raw(self.password)
		try:
			self._readline()
		except OSError:
			pass
		print(f"[PrairieLink] Connected to {host}:{port}")

	def _readline(self) -> str:
		buf = bytearray()
		while True:
			ch = self.sock.recv(1)
			if not ch:
				break
			buf += ch
			if ch == b"\n":
				break
		return buf.decode("ascii", errors="replace").strip()

	def _send_raw(self, text: str) -> None:
		self.sock.sendall((text + "\r\n").encode("ascii"))

	def send_command(self, *parts: str) -> str:
		"""
		Send one script command. Parts are joined with SOH (not spaces),
		as required for direct TCP/IP to Prairie View.
		"""
		if not parts:
			raise ValueError("empty command")
		self._send_raw(SOH.join(parts))
		lines = []
		while True:
			line = self._readline()
			lines.append(line)
			if line == "DONE":
				break
		return "\n".join(lines)

	def query(self, *parts: str) -> str:
		"""Send a command and return data lines between ACK and DONE."""
		raw = self.send_command(*parts)
		data_lines = [
			ln
			for ln in raw.splitlines()
			if ln.strip() and ln.strip() not in ("ACK", "DONE")
		]
		return "\n".join(data_lines).strip()

	def close(self) -> None:
		try:
			self.send_command("-Exit")
		except OSError:
			pass
		self.sock.close()
		print("[PrairieLink] Disconnected.")


# ---------------------------------------------------------------------------
# Mark points XML parsing
# ---------------------------------------------------------------------------


def parse_mark_points(xml_string: str) -> List[Dict]:
	"""
	Parse Prairie View mark points series XML into group dicts.

	Expects PVMarkPointSeriesElements / PVMarkPointGroupElement /
	PVGalvoPointElement (see Naparm-style series files). Strip XML 1.1
	declarations before calling if stdlib ET rejects the file.
	"""
	root = ET.fromstring(_strip_xml_decl(xml_string))
	groups = []

	for group_el in root.findall("PVMarkPointGroupElement"):
		group_pwr = float(group_el.attrib.get("UncagingLaserPower", 0))
		gid = group_el.attrib["Id"]
		group = {
			"id": gid,
			"name": group_el.attrib.get("Name", f"Group {gid}"),
			"is_active": group_el.attrib.get("IsActive", "True") == "True",
			"laser_pwr": group_pwr,
			"duration": float(group_el.attrib.get("Duration", 0)),
			"repetitions": int(group_el.attrib.get("Repetitions", 1)),
			"points": [],
		}
		for pt_el in group_el.findall("PVGalvoPointElement"):
			pt_pwr = float(pt_el.attrib.get("UncagingLaserPower", group_pwr))
			pid = pt_el.attrib["Id"]
			group["points"].append({
				"id": pid,
				"name": pt_el.attrib.get("Name", f"Point {pid}"),
				"is_active": pt_el.attrib.get("IsActive", "True") == "True",
				"x": float(pt_el.attrib.get("X", 0)),
				"y": float(pt_el.attrib.get("Y", 0)),
				"duration": float(pt_el.attrib.get("Duration", group["duration"])),
				"power": pt_pwr,
			})
		groups.append(group)
	return groups


def load_mark_points_file(path: str) -> List[Dict]:
	"""Read groups from a local series .xml (PV has no GetMarkPoints)."""
	with open(path, encoding="utf-8") as f:
		return parse_mark_points(f.read())


def groups_to_xml(groups: List[Dict]) -> str:
	"""Serialise group dicts to PVMarkPointSeriesElements XML."""
	root = ET.Element("PVMarkPointSeriesElements")
	for g in groups:
		g_el = ET.SubElement(root, "PVMarkPointGroupElement")
		g_el.set("Id", g["id"])
		g_el.set("Name", g["name"])
		g_el.set("IsActive", str(g["is_active"]))
		g_el.set("UncagingLaser", "Uncaging")
		g_el.set("UncagingLaserPower", str(g["laser_pwr"]))
		g_el.set("Duration", str(g["duration"]))
		g_el.set("Repetitions", str(g["repetitions"]))
		for pt in g["points"]:
			pt_el = ET.SubElement(g_el, "PVGalvoPointElement")
			pt_el.set("Id", pt["id"])
			pt_el.set("Name", pt["name"])
			pt_el.set("IsActive", str(pt["is_active"]))
			pt_el.set("X", str(pt["x"]))
			pt_el.set("Y", str(pt["y"]))
			pt_el.set("Duration", str(pt["duration"]))
			pt_el.set("UncagingLaserPower", str(pt["power"]))
	return ET.tostring(root, encoding="unicode", xml_declaration=False)


def _strip_xml_decl(text: str) -> str:
	# PV often writes XML 1.1; stdlib ET only accepts 1.0.
	if text.lstrip().startswith("<?xml"):
		end = text.find("?>")
		if end != -1:
			return text[end + 2 :].lstrip()
	return text


# ---------------------------------------------------------------------------
# Inspection helpers
# ---------------------------------------------------------------------------


def get_active_point_ids(groups: List[Dict]) -> Set[Tuple[str, str]]:
	"""Return {(group_id, point_id)} for every currently active point."""
	return {
		(g["id"], pt["id"])
		for g in groups
		for pt in g["points"]
		if g["is_active"] and pt["is_active"]
	}


def get_active_points_detail(groups: List[Dict]) -> List[Dict]:
	"""Return detail dicts for every active group+point."""
	result = []
	for g in groups:
		if not g["is_active"]:
			continue
		for pt in g["points"]:
			if pt["is_active"]:
				result.append({
					"group_id": g["id"],
					"group_name": g["name"],
					"point_id": pt["id"],
					"point_name": pt["name"],
					"x": pt["x"],
					"y": pt["y"],
					"power": pt["power"],
					"duration": pt["duration"],
				})
	return result


def find_candidates_for_reactivation(
	groups: List[Dict],
	previous_active_ids: Set[Tuple[str, str]],
) -> List[Dict]:
	"""Points that were not active last iteration."""
	candidates = []
	for g in groups:
		for pt in g["points"]:
			if (g["id"], pt["id"]) not in previous_active_ids:
				candidates.append({
					"group_id": g["id"],
					"group_name": g["name"],
					"point_id": pt["id"],
					"point_name": pt["name"],
					"x": pt["x"],
					"y": pt["y"],
					"power": pt["power"],
				})
	return candidates


def estimate_series_ms(groups: List[Dict]) -> float:
	"""Rough upper bound for sleep after -MarkPoints (no Done-for-MP API)."""
	total = 0.0
	for g in groups:
		if not g["is_active"]:
			continue
		for pt in g["points"]:
			if pt["is_active"]:
				total += pt["duration"] * max(g["repetitions"], 1)
	return total


# ---------------------------------------------------------------------------
# Mutation helpers
# ---------------------------------------------------------------------------


def set_group_active(groups: List[Dict], group_name: str, active: bool) -> List[Dict]:
	updated = []
	for g in dict_list_copy(groups):
		if g["name"] == group_name:
			g["is_active"] = active
		updated.append(g)
	return updated


def set_point_active(
	groups: List[Dict], group_id: str, point_id: str, active: bool
) -> List[Dict]:
	updated = []
	for g in dict_list_copy(groups):
		if g["id"] == group_id:
			g["points"] = [
				{**pt, "is_active": active} if pt["id"] == point_id else pt
				for pt in g["points"]
			]
		updated.append(g)
	return updated


def dict_list_copy(groups: List[Dict]) -> List[Dict]:
	return [{**g, "points": list(g["points"])} for g in groups]


# ---------------------------------------------------------------------------
# Official PV command helpers
# ---------------------------------------------------------------------------


def reload_mark_points(pl: PrairieLink, groups: List[Dict], path: str | None = None) -> str:
	"""
	Write series XML and -LoadMarkPoints it.

	Path must be visible to the Prairie View PC. On the scope box itself,
	a local tempfile is fine. From a remote analysis PC, write into a
	shared/scope path and pass it here.
	"""
	xml_string = groups_to_xml(groups)
	if path is None:
		fd, path = tempfile.mkstemp(suffix=".xml", prefix="prairie_mp_")
		os.close(fd)
		owned = True
	else:
		owned = False
	try:
		with open(path, "w", encoding="utf-8") as f:
			f.write(xml_string)
		# Series load: -LoadMarkPoints <xml path>
		pl.send_command("-LoadMarkPoints", path)
	finally:
		if owned:
			# Keep file until PV has loaded; delete after a short settle.
			time.sleep(0.05)
			try:
				os.unlink(path)
			except OSError:
				pass
	return path


def run_mark_points(pl: PrairieLink) -> None:
	"""Run the current Mark Point series (-MarkPoints with no args)."""
	pl.send_command("-MarkPoints")


def run_mark_points_saved(pl: PrairieLink, category: str, experiment: str) -> None:
	"""Run a saved Mark Point Series by Category and Experiment name."""
	pl.send_command("-MarkPoints", category, experiment)


def run_mark_points_group(pl: PrairieLink, groups: List[Dict], group_index: int) -> List[Dict]:
	"""
	No -RunMarkPointsGroup in PV. Activate only group_index, -lmp, then -mp.
	Returns the groups list that was loaded.
	"""
	if group_index < 0 or group_index >= len(groups):
		raise IndexError(f"group_index {group_index} out of range")
	updated = dict_list_copy(groups)
	for i, g in enumerate(updated):
		g["is_active"] = i == group_index
		for pt in g["points"]:
			pt["is_active"] = g["is_active"] and pt.get("is_active", True)
	reload_mark_points(pl, updated)
	run_mark_points(pl)
	return updated


def abort(pl: PrairieLink) -> None:
	pl.send_command("-Abort")


def wait_for_series_complete(groups: List[Dict], pad_ms: float = 100.0) -> None:
	"""
	Block for an estimated series duration.

	PV has no 'Mark Points busy' query. -GetState requires a key and does
	not return the live Mark Points UI state. -WaitForScan only covers
	scans, not necessarily Mark Points.
	"""
	ms = estimate_series_ms(groups) + pad_ms
	time.sleep(max(ms, 0.0) / 1000.0)


def get_state(pl: PrairieLink, key: str, index: str | None = None, subindex: str | None = None) -> str:
	"""Official -GetState (-gts); key is required."""
	parts = ["-GetState", key]
	if index is not None:
		parts.append(index)
	if subindex is not None:
		parts.append(subindex)
	return pl.query(*parts)


# ---------------------------------------------------------------------------
# Closed-loop controller
# ---------------------------------------------------------------------------


def run_closed_loop(
	pl: PrairieLink,
	groups: List[Dict],
	iterations: int = 10,
	inter_iteration_delay: float = 0.5,
	strategy: str = "repeat_active",
	scope_xml_path: str | None = None,
) -> None:
	"""
	Run a closed-loop Mark Points series for `iterations` cycles.

	`groups` is the in-memory source of truth (loaded from a local XML).
	Each iteration rewrites activation flags, -LoadMarkPoints, then
	-MarkPoints.

	strategy:
	  repeat_active — keep whatever was active last iteration
	  rotate        — activate points that were inactive last time
	  always_all    — all groups/points every iteration
	"""
	print(
		f"\n[ClosedLoop] Starting: {iterations} iterations, "
		f"strategy='{strategy}'"
	)

	previous_active_ids: Set[Tuple[str, str]] = set()
	current = dict_list_copy(groups)

	for i in range(iterations):
		print(f"\n--- Iteration {i + 1}/{iterations} ---")
		_print_groups(current)

		this_active_ids = get_active_point_ids(current)
		active_detail = get_active_points_detail(current)
		print(f"  Active points this iteration: {len(active_detail)}")
		for ap in active_detail:
			print(
				f"    [{ap['group_name']}] {ap['point_name']} "
				f"@ ({ap['x']:.3f}, {ap['y']:.3f})  "
				f"power={ap['power']}%  dur={ap['duration']}ms"
			)

		candidates = find_candidates_for_reactivation(current, previous_active_ids)
		if candidates:
			print(f"  Candidate points not yet activated: {len(candidates)}")
			for c in candidates:
				print(
					f"    [{c['group_name']}] {c['point_name']} "
					f"@ ({c['x']:.3f}, {c['y']:.3f})"
				)

		next_groups = _apply_strategy(
			current, previous_active_ids, this_active_ids, strategy
		)

		reload_mark_points(pl, next_groups, path=scope_xml_path)
		print("  Firing mark points series (-MarkPoints) ...")
		run_mark_points(pl)
		wait_for_series_complete(next_groups)
		print("  Series complete (duration estimate).")

		previous_active_ids = this_active_ids
		current = next_groups

		if i < iterations - 1:
			time.sleep(inter_iteration_delay)

	print("\n[ClosedLoop] Finished all iterations.")


def _apply_strategy(
	groups: List[Dict],
	previous_active: Set[Tuple[str, str]],
	current_active: Set[Tuple[str, str]],
	strategy: str,
) -> List[Dict]:
	updated = dict_list_copy(groups)

	if strategy == "always_all":
		for g in updated:
			g["is_active"] = True
			for pt in g["points"]:
				pt["is_active"] = True

	elif strategy == "repeat_active":
		for g in updated:
			any_pt_active = any(
				(g["id"], pt["id"]) in current_active for pt in g["points"]
			)
			g["is_active"] = any_pt_active
			for pt in g["points"]:
				pt["is_active"] = (g["id"], pt["id"]) in current_active

	elif strategy == "rotate":
		all_ids = {(g["id"], pt["id"]) for g in updated for pt in g["points"]}
		inactive_last = all_ids - previous_active
		if not inactive_last:
			inactive_last = all_ids
		for g in updated:
			any_pt_next = any(
				(g["id"], pt["id"]) in inactive_last for pt in g["points"]
			)
			g["is_active"] = any_pt_next
			for pt in g["points"]:
				pt["is_active"] = (g["id"], pt["id"]) in inactive_last

	else:
		raise ValueError(
			f"Unknown strategy: '{strategy}'. "
			"Choose 'repeat_active', 'rotate', or 'always_all'."
		)
	return updated


def _print_groups(groups: List[Dict]) -> None:
	print(f"  Mark points: {len(groups)} group(s)")
	for g in groups:
		flag = "+" if g["is_active"] else "-"
		print(
			f"  [{flag}] Group '{g['name']}' "
			f"power={g['laser_pwr']}%  "
			f"dur={g['duration']}ms  "
			f"rep={g['repetitions']}  "
			f"({len(g['points'])} point(s))"
		)
		for pt in g["points"]:
			pflag = "+" if pt["is_active"] else "-"
			print(
				f"      [{pflag}] {pt['name']} "
				f"({pt['x']:.3f}, {pt['y']:.3f})  "
				f"power={pt['power']}%  dur={pt['duration']}ms"
			)


def inspect_mark_points(groups: List[Dict]) -> None:
	"""Pretty-print groups loaded from file (no PV round-trip)."""
	_print_groups(groups)
	active = get_active_points_detail(groups)
	print(f"\nTotal active points: {len(active)}")
	all_pts = [pt for g in groups for pt in g["points"]]
	print(f"Total points defined: {len(all_pts)}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
	parser = argparse.ArgumentParser(
		description="Prairie View 5.8 closed-loop Mark Points (official cmds)"
	)
	parser.add_argument(
		"--host",
		default="127.0.0.1",
		help="Prairie View host (scope PC: 127.0.0.1)",
	)
	parser.add_argument(
		"--port",
		type=int,
		default=DEFAULT_PORT,
		help=f"PrairieLink TCP script port (default: {DEFAULT_PORT})",
	)
	parser.add_argument("--password", default="0000", help="PrairieLink password")
	parser.add_argument(
		"--series",
		required=True,
		help="Local Mark Points series .xml (source of truth; no GetMarkPoints)",
	)
	parser.add_argument(
		"--scope-xml",
		help="Path on the Prairie View PC for -LoadMarkPoints (default: local tempfile)",
	)
	parser.add_argument("--iterations", type=int, default=10)
	parser.add_argument("--delay", type=float, default=0.5)
	parser.add_argument(
		"--strategy",
		default="repeat_active",
		choices=["repeat_active", "rotate", "always_all"],
	)
	parser.add_argument(
		"--inspect",
		action="store_true",
		help="Print groups from --series and exit (no TCP).",
	)
	args = parser.parse_args()

	groups = load_mark_points_file(args.series)

	if args.inspect:
		inspect_mark_points(groups)
	else:
		pl = PrairieLink(host=args.host, port=args.port, password=args.password)
		try:
			run_closed_loop(
				pl,
				groups,
				iterations=args.iterations,
				inter_iteration_delay=args.delay,
				strategy=args.strategy,
				scope_xml_path=args.scope_xml,
			)
		finally:
			pl.close()
