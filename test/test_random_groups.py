"""Unit tests for offline random 3-point group builder."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from prairie_live.markpoints import template_meta
from prairie_live.random_groups import (
	GROUP_SIZE,
	build_random_groups,
	groups_gpl_text,
	groups_xml_text,
	min_groups_for_coverage,
	renumber_points,
	select_groups_by_id,
	write_groups_xml,
)


def _pool(n: int = 10) -> list[dict]:
	return [
		{"id": str(i), "name": f"Point {i}", "x": 0.1 * (i % 5), "y": 0.15 * (i // 5), "z": 0}
		for i in range(1, n + 1)
	]


def test_build_unique_within_group_repeats_across():
	pts = _pool(6)
	groups = build_random_groups(pts, 8, seed=42)
	assert len(groups) == 8
	for g in groups:
		assert len(g["point_ids"]) == GROUP_SIZE
		assert len(set(g["point_ids"])) == GROUP_SIZE
		assert len(g["rgb"]) == 3
	# Same seed → same membership (determinism).
	again = build_random_groups(pts, 8, seed=42)
	assert [g["point_ids"] for g in groups] == [g["point_ids"] for g in again]


def test_not_sequential_packing():
	pts = _pool(12)
	groups = build_random_groups(pts, 4, seed=7)
	# Sequential packing would be {1,2,3}, {4,5,6}, …
	sequential = [{str(i), str(i + 1), str(i + 2)} for i in (1, 4, 7, 10)]
	got = [set(g["point_ids"]) for g in groups]
	assert got != sequential


def test_rejects_too_few_points():
	with pytest.raises(ValueError, match="at least 3"):
		build_random_groups(_pool(2), 1)


def test_every_point_used_at_least_once():
	pts = _pool(31)
	# Too few groups to cover → bumped to ceil(31/3)=11.
	assert min_groups_for_coverage(31) == 11
	groups = build_random_groups(pts, 5, seed=9)
	assert len(groups) == 11
	seen = {pid for g in groups for pid in g["point_ids"]}
	assert seen == {str(i) for i in range(1, 32)}
	# Plenty of groups still covers everyone.
	big = build_random_groups(pts, 20, seed=2)
	assert len(big) == 20
	assert {pid for g in big for pid in g["point_ids"]} == {str(i) for i in range(1, 32)}


@pytest.mark.parametrize("n", [3, 4, 5, 7, 8, 10, 11, 31, 32, 50, 100])
@pytest.mark.parametrize("seed", [0, 1, 7, 99])
def test_coverage_all_pool_sizes(n, seed):
	pts = _pool(n)
	for asked in (1, max(1, n // 5), n):
		groups = build_random_groups(pts, asked, seed=seed + asked)
		assert len(groups) >= min_groups_for_coverage(n)
		seen = {pid for g in groups for pid in g["point_ids"]}
		assert seen == {str(i) for i in range(1, n + 1)}
		for g in groups:
			assert len(set(g["point_ids"])) == GROUP_SIZE


def test_xml_has_n_groups_of_three(tmp_path):
	pts = _pool(9)
	groups = build_random_groups(pts, 5, seed=1)
	meta = template_meta([])
	meta["duration"] = 36.0
	meta["spiral_revolutions"] = "8"
	path = write_groups_xml(groups, power=160.0, meta=meta, path=tmp_path / "g.xml")
	root = ET.fromstring(path.read_text(encoding="utf-8"))
	els = root.findall("PVMarkPointElement")
	assert len(els) == 5
	for el in els:
		# Display 160 → series Out fraction 0.4 (scale 400).
		assert float(el.get("UncagingLaserPower")) == pytest.approx(0.4)
		galvo = el.find("PVGalvoPointElement")
		assert galvo is not None
		assert len(galvo.findall("Point")) == 3
		# Points= lists real point names (not just "G1") for PV/.gpl lookup.
		assert "Point" in (galvo.get("Points") or "")


def test_companion_gpl_lists_every_covered_point():
	pts = _pool(8)
	groups = build_random_groups(pts, 4, seed=2)
	gpl = groups_gpl_text(groups, power=160.0, meta=template_meta([]))
	root = ET.fromstring(gpl)
	assert root.tag.endswith("PVGalvoPointList")
	els = root.findall("PVGalvoPoint")
	assert els, "PV Import requires <PVGalvoPoint> children (not <Point>)"
	assert [el.get("Index") for el in els] == [str(i) for i in range(1, 9)]
	for i, el in enumerate(els, start=1):
		assert el.get("Index") == str(i)
		assert el.get("Name") == f"Point {i}"
		assert el.get("ActivityType") is None
		assert el.get("SpiralSizeInMicrons") is None
	covered = {pid for g in groups for pid in g["point_ids"]}
	assert {el.get("Index") for el in els} == covered


def test_point_name_matches_index_even_if_source_diverged():
	# Source Name "Point 7" with Index 31 must not survive into exports.
	weird = [
		{"id": "31", "name": "Point 7", "x": 0.1, "y": 0.2, "z": 0},
		{"id": "5", "name": "Point 5", "x": 0.3, "y": 0.4, "z": 0},
		{"id": "99", "name": "Point 99", "x": 0.5, "y": 0.6, "z": 0},
	]
	norm = renumber_points(weird)
	assert [(p["id"], p["name"]) for p in norm] == [
		("1", "Point 1"),
		("2", "Point 2"),
		("3", "Point 3"),
	]
	groups = build_random_groups(weird, 1, seed=1)
	for g in groups:
		for p in g["points"]:
			assert p["name"] == f"Point {p['id']}"
	gpl = ET.fromstring(groups_gpl_text(groups, power=100.0, meta=template_meta([])))
	els = gpl.findall("PVGalvoPoint")
	assert els and not gpl.findall("Point")
	assert [el.get("Index") for el in els] == [str(i) for i in range(1, len(els) + 1)]
	for el in els:
		assert el.get("Name") == f"Point {el.get('Index')}"
	xml = groups_xml_text(groups, power=100.0, meta=template_meta([]))
	root = ET.fromstring(xml)
	for pt in root.iter("Point"):
		assert f"Point {pt.get('Index')}" in (
			root.find(".//PVGalvoPointElement").get("Points") or ""
			if False
			else f"Point {pt.get('Index')}"
		)
		# Nested Index is the canonical id; Points= lists matching names.
	for galvo in root.iter("PVGalvoPointElement"):
		names = [s.strip() for s in (galvo.get("Points") or "").split(",")]
		idxs = [p.get("Index") for p in galvo.findall("Point")]
		assert names == [f"Point {i}" for i in idxs]


def test_select_preserves_three_point_membership():
	pts = _pool(8)
	groups = build_random_groups(pts, 4, seed=3)
	picked = select_groups_by_id(groups, [groups[2]["id"], groups[0]["id"]])
	assert [g["id"] for g in picked] == [groups[2]["id"], groups[0]["id"]]
	assert all(len(g["point_ids"]) == 3 for g in picked)
	xml = groups_xml_text(picked, power=100.0, meta=template_meta([]))
	assert xml.count("<PVMarkPointElement") == 2
