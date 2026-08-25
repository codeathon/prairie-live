"""Mark Points series XML: parse, write, flatten points, build stim groups.

PrairieView has no GetMarkPoints. The series file (prefer fat MarkPoints.xml
with nested <Point X Y Z>) is the source of truth for coords and power.
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from typing import Any

# Default spiral attrs when cloning points into a new group step.
_SPIRAL_DEFAULTS = {
	"is_spiral": "True",
	"spiral_width": "0.2",
	"spiral_height": "0.2",
	"spiral_size_um": "20",
}


def parse_mark_points(xml_string: str) -> list[dict]:
	"""
	Parse a Prairie View Mark Points series XML into stim-step dicts.

	Preferred layout (self-contained coords):

	  <PVMarkPointSeriesElements Use3D="True" AllPointsAtOnce="True" ...>
	    <PVMarkPointElement UncagingLaserPower="..." ...>
	      <PVGalvoPointElement Points="Group 1" Indices="1-9" ...>
	        <Point Index="1" X="0.15" Y="0.13" Z="0" IsSpiral="True" .../>
	      </PVGalvoPointElement>
	    </PVMarkPointElement>
	  </PVMarkPointSeriesElements>
	"""
	root = ET.fromstring(_strip_xml_decl(xml_string))
	tag = _local(root.tag)
	if tag == "PVGalvoPointList":
		raise ValueError(
			"this looks like a point-list .gpl, not a Mark Points series .xml"
		)

	series_meta = {
		"use_3d": root.attrib.get("Use3D", "False"),
		"all_points_at_once": root.attrib.get("AllPointsAtOnce", "False"),
		"calc_funct_map": root.attrib.get("CalcFunctMap", "False"),
		"iterations": root.attrib.get("Iterations", "1"),
		"iteration_delay": root.attrib.get("IterationDelay", "0.00"),
	}

	groups = _parse_mark_point_elements(root, series_meta)
	if groups:
		return groups
	groups = _parse_group_elements(root)
	if groups:
		return groups

	kids = sorted({_local(c.tag) for c in root.iter() if _local(c.tag) != tag})
	raise ValueError(
		f"no Mark Points steps found under <{tag}>. "
		f"Tags seen: {kids or '(none)'}. "
		"Expected PVMarkPointElement under PVMarkPointSeriesElements."
	)


def load_mark_points_file(path: str) -> list[dict]:
	"""Read stim steps from a local series .xml."""
	with open(path, encoding="utf-8") as f:
		return parse_mark_points(f.read())


def extract_unique_points(steps: list[dict]) -> list[dict]:
	"""
	Flatten unique FOV points from series steps (points-only pool).

	Dedupes by point Index when present; skips name-only placeholders
	with (0,0) that only mirror the group label.
	"""
	seen: set[str] = set()
	out: list[dict] = []
	for step in steps:
		gname = step.get("raw_points") or step.get("name") or ""
		for pt in step.get("points", []):
			# Skip slim placeholder rows (group name, no coords).
			if (
				pt.get("name") == gname
				and float(pt.get("x", 0)) == 0.0
				and float(pt.get("y", 0)) == 0.0
				and len(step.get("points", [])) == 1
			):
				continue
			pid = str(pt.get("id", len(out) + 1))
			if pid in seen:
				continue
			seen.add(pid)
			row = copy.deepcopy(pt)
			row["id"] = pid
			# Carry spiral defaults so rewritten XML stays loadable.
			for k, v in _SPIRAL_DEFAULTS.items():
				row.setdefault(k, v)
			out.append(row)
	if not out:
		raise ValueError(
			"no FOV points with coordinates found — use a fat MarkPoints.xml "
			"with nested <Point X Y Z>, not a slim group-name-only series"
		)
	return out


def template_meta(steps: list[dict]) -> dict[str, Any]:
	"""Series + step timing defaults taken from the first source step."""
	if not steps:
		return {
			"use_3d": "True",
			"all_points_at_once": "True",
			"calc_funct_map": "False",
			"iterations": "1",
			"iteration_delay": "0.00",
			"duration": 10.0,
			"repetitions": 1,
			"initial_delay": "0.00",
			"inter_point_delay": "0.01",
			"spiral_revolutions": "5",
			"uncaging_laser": "Uncaging",
			"trigger_selection": "None",
			"trigger_frequency": "None",
			"trigger_count": "1",
			"async_sync_frequency": "None",
			"voltage_output_category": "None",
			"voltage_rec_category": "None",
			"parameter_set": "CurrentSettings",
		}
	s0 = steps[0]
	keys = (
		"use_3d",
		"all_points_at_once",
		"calc_funct_map",
		"iterations",
		"iteration_delay",
		"duration",
		"repetitions",
		"initial_delay",
		"inter_point_delay",
		"spiral_revolutions",
		"uncaging_laser",
		"trigger_selection",
		"trigger_frequency",
		"trigger_count",
		"async_sync_frequency",
		"voltage_output_category",
		"voltage_rec_category",
		"parameter_set",
	)
	return {k: s0.get(k, template_meta([])[k]) for k in keys}


def build_group_step(
	points: list[dict],
	*,
	name: str,
	power: float,
	meta: dict[str, Any],
	trigger_selection: str | None = None,
) -> dict:
	"""One PVMarkPointElement worth of state (single group, one power)."""
	pts = []
	for pt in points:
		row = copy.deepcopy(pt)
		row["is_active"] = True
		row["power"] = power
		row["duration"] = float(meta.get("duration", 10.0))
		pts.append(row)
	trig = (
		trigger_selection
		if trigger_selection is not None
		else str(meta.get("trigger_selection", "None"))
	)
	return {
		"id": "1",
		"name": name,
		"is_active": True,
		"laser_pwr": power,
		"duration": float(meta.get("duration", 10.0)),
		"repetitions": int(meta.get("repetitions", 1)),
		"points": pts,
		"raw_points": name,
		"indices": _indices_from_points(pts),
		"uncaging_laser": meta.get("uncaging_laser", "Uncaging"),
		"trigger_selection": trig,
		"trigger_frequency": meta.get("trigger_frequency", "None"),
		"trigger_count": meta.get("trigger_count", "1"),
		"async_sync_frequency": meta.get("async_sync_frequency", "None"),
		"voltage_output_category": meta.get("voltage_output_category", "None"),
		"voltage_rec_category": meta.get("voltage_rec_category", "None"),
		"parameter_set": meta.get("parameter_set", "CurrentSettings"),
		"spiral_revolutions": meta.get("spiral_revolutions", "5"),
		"initial_delay": meta.get("initial_delay", "0.00"),
		"inter_point_delay": meta.get("inter_point_delay", "0.01"),
		"use_3d": meta.get("use_3d", "True"),
		"all_points_at_once": meta.get("all_points_at_once", "True"),
		"calc_funct_map": meta.get("calc_funct_map", "False"),
		"iterations": "1",
		"iteration_delay": meta.get("iteration_delay", "0.00"),
	}


def groups_to_xml(groups: list[dict]) -> str:
	"""
	Write preferred MarkPoints.xml layout for -LoadMarkPoints:
	PVMarkPointSeriesElements with nested <Point X Y Z ...>.
	Inactive steps are omitted.
	"""
	root = ET.Element("PVMarkPointSeriesElements")
	meta = groups[0] if groups else {}
	root.set("Use3D", str(meta.get("use_3d", "True")))
	root.set("AllPointsAtOnce", str(meta.get("all_points_at_once", "True")))
	root.set("CalcFunctMap", str(meta.get("calc_funct_map", "False")))
	root.set("IterationDelay", str(meta.get("iteration_delay", "0.00")))
	root.set("Iterations", str(meta.get("iterations", "1")))

	for g in groups:
		if not g.get("is_active", True):
			continue
		active_pts = [pt for pt in g["points"] if pt.get("is_active", True)]
		if not active_pts:
			continue
		points_str = g.get("raw_points") or g.get("name") or "Group"
		el = ET.SubElement(root, "PVMarkPointElement")
		el.set("Repetitions", str(g["repetitions"]))
		el.set("UncagingLaser", str(g.get("uncaging_laser", "Uncaging")))
		el.set("UncagingLaserPower", str(g["laser_pwr"]))
		el.set("TriggerFrequency", str(g.get("trigger_frequency", "None")))
		el.set("TriggerSelection", str(g.get("trigger_selection", "None")))
		el.set("TriggerCount", str(g.get("trigger_count", "1")))
		el.set("AsyncSyncFrequency", str(g.get("async_sync_frequency", "None")))
		el.set("VoltageOutputCategoryName", str(g.get("voltage_output_category", "None")))
		el.set("VoltageRecCategoryName", str(g.get("voltage_rec_category", "None")))
		el.set("parameterSet", str(g.get("parameter_set", "CurrentSettings")))

		galvo = ET.SubElement(el, "PVGalvoPointElement")
		galvo.set("InitialDelay", str(g.get("initial_delay", "0.00")))
		galvo.set("InterPointDelay", str(g.get("inter_point_delay", "0.01")))
		galvo.set("Duration", str(g["duration"] or active_pts[0]["duration"]))
		galvo.set("SpiralRevolutions", str(g.get("spiral_revolutions", "0")))
		galvo.set("Points", points_str)
		galvo.set("Indices", str(g.get("indices") or _indices_from_points(active_pts)))

		for pt in active_pts:
			if pt["name"] == points_str and pt.get("x", 0) == 0 and pt.get("y", 0) == 0:
				if len(active_pts) == 1:
					continue
			pt_el = ET.SubElement(galvo, "Point")
			pt_el.set("Index", str(pt["id"]))
			pt_el.set("X", str(pt.get("x", 0)))
			pt_el.set("Y", str(pt.get("y", 0)))
			pt_el.set("Z", str(pt.get("z", 0)))
			pt_el.set("IsSpiral", str(pt.get("is_spiral", "True")))
			if pt.get("spiral_width") not in (None, ""):
				pt_el.set("SpiralWidth", str(pt["spiral_width"]))
			if pt.get("spiral_height") not in (None, ""):
				pt_el.set("SpiralHeight", str(pt["spiral_height"]))
			if pt.get("spiral_size_um") not in (None, ""):
				pt_el.set("SpiralSizeInMicrons", str(pt["spiral_size_um"]))
	return ET.tostring(root, encoding="unicode", xml_declaration=False)


def estimate_series_ms(groups: list[dict]) -> float:
	"""Rough upper bound for sleep after -MarkPoints (no Done-for-MP API)."""
	total = 0.0
	for g in groups:
		if not g.get("is_active", True):
			continue
		# AllPointsAtOnce: one duration * reps, not sum over points.
		if str(g.get("all_points_at_once", "False")).lower() == "true":
			total += float(g.get("duration", 0)) * max(int(g.get("repetitions", 1)), 1)
			continue
		for pt in g["points"]:
			if pt.get("is_active", True):
				total += float(pt.get("duration", 0)) * max(int(g.get("repetitions", 1)), 1)
	return total


def _parse_mark_point_elements(root: ET.Element, series_meta: dict) -> list[dict]:
	groups = []
	for i, el in enumerate(_findall_local(root, "PVMarkPointElement")):
		pwr = float(el.attrib.get("UncagingLaserPower", 0))
		reps = int(float(el.attrib.get("Repetitions", 1)))
		galvo = _findall_local(el, "PVGalvoPointElement")
		dur = float(el.attrib.get("Duration", 0))
		points_attr = el.attrib.get("Points", "")
		indices = el.attrib.get("Indices", "")
		spiral = el.attrib.get("SpiralRevolutions", "0")
		initial_delay = el.attrib.get("InitialDelay", "0.00")
		inter_delay = el.attrib.get("InterPointDelay", "0.01")
		point_els: list[ET.Element] = []
		if galvo:
			g0 = galvo[0]
			if "Duration" in g0.attrib:
				dur = float(g0.attrib["Duration"])
			if not points_attr:
				points_attr = g0.attrib.get("Points", "")
			indices = g0.attrib.get("Indices", indices)
			spiral = g0.attrib.get("SpiralRevolutions", spiral)
			initial_delay = g0.attrib.get("InitialDelay", initial_delay)
			inter_delay = g0.attrib.get("InterPointDelay", inter_delay)
			point_els = _findall_local(g0, "Point")

		gid = el.attrib.get("Index", el.attrib.get("Id", str(i)))
		gname = points_attr.strip() or f"Step {gid}"
		group = {
			"id": str(gid),
			"name": gname,
			"is_active": el.attrib.get("IsActive", "True") == "True",
			"laser_pwr": pwr,
			"duration": dur,
			"repetitions": reps,
			"points": [],
			"raw_points": points_attr,
			"uncaging_laser": el.attrib.get("UncagingLaser", "Uncaging"),
			"trigger_selection": el.attrib.get("TriggerSelection", "None"),
			"trigger_frequency": el.attrib.get("TriggerFrequency", "None"),
			"trigger_count": el.attrib.get("TriggerCount", "1"),
			"async_sync_frequency": el.attrib.get("AsyncSyncFrequency", "None"),
			"voltage_output_category": el.attrib.get("VoltageOutputCategoryName", "None"),
			"voltage_rec_category": el.attrib.get("VoltageRecCategoryName", "None"),
			"parameter_set": el.attrib.get("parameterSet", "CurrentSettings"),
			"indices": indices,
			"spiral_revolutions": spiral,
			"initial_delay": initial_delay,
			"inter_point_delay": inter_delay,
			**series_meta,
		}

		if point_els:
			for pt_el in point_els:
				idx = pt_el.attrib.get("Index", str(len(group["points"]) + 1))
				group["points"].append({
					"id": str(idx),
					"name": f"Point {idx}",
					"is_active": True,
					"x": float(pt_el.attrib.get("X", 0)),
					"y": float(pt_el.attrib.get("Y", 0)),
					"z": float(pt_el.attrib.get("Z", 0)),
					"duration": dur,
					"power": pwr,
					"is_spiral": pt_el.attrib.get("IsSpiral", "False"),
					"spiral_width": pt_el.attrib.get("SpiralWidth", ""),
					"spiral_height": pt_el.attrib.get("SpiralHeight", ""),
					"spiral_size_um": pt_el.attrib.get("SpiralSizeInMicrons", ""),
				})
		else:
			names = _split_points_attr(points_attr) or [f"Step {i}"]
			for j, pname in enumerate(names):
				group["points"].append({
					"id": str(j),
					"name": pname,
					"is_active": True,
					"x": 0.0,
					"y": 0.0,
					"z": 0.0,
					"duration": dur,
					"power": pwr,
				})
		groups.append(group)
	return groups


def _parse_group_elements(root: ET.Element) -> list[dict]:
	groups = []
	for group_el in _findall_local(root, "PVMarkPointGroupElement"):
		group_pwr = float(group_el.attrib.get("UncagingLaserPower", 0))
		gid = group_el.attrib.get("Id", str(len(groups)))
		group = {
			"id": gid,
			"name": group_el.attrib.get("Name", f"Group {gid}"),
			"is_active": group_el.attrib.get("IsActive", "True") == "True",
			"laser_pwr": group_pwr,
			"duration": float(group_el.attrib.get("Duration", 0)),
			"repetitions": int(float(group_el.attrib.get("Repetitions", 1))),
			"points": [],
			"raw_points": "",
		}
		for pt_el in _findall_local(group_el, "PVGalvoPointElement"):
			pt_pwr = float(pt_el.attrib.get("UncagingLaserPower", group_pwr))
			pid = pt_el.attrib.get("Id", str(len(group["points"])))
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


def _split_points_attr(points_attr: str) -> list[str]:
	if not points_attr or not points_attr.strip():
		return []
	return [p.strip() for p in points_attr.split(",") if p.strip()]


def _local(tag: str) -> str:
	if "}" in tag:
		return tag.rsplit("}", 1)[-1]
	return tag


def _findall_local(root: ET.Element, name: str) -> list[ET.Element]:
	return [el for el in root.iter() if _local(el.tag) == name and el is not root]


def _indices_from_points(points: list[dict]) -> str:
	ids = [str(pt["id"]) for pt in points]
	if not ids:
		return "1"
	if len(ids) == 1:
		return ids[0]
	return f"{ids[0]}-{ids[-1]}"


def _strip_xml_decl(text: str) -> str:
	# PV often writes XML 1.1; stdlib ET only accepts 1.0.
	if text.lstrip().startswith("<?xml"):
		end = text.find("?>")
		if end != -1:
			return text[end + 2 :].lstrip()
	return text
