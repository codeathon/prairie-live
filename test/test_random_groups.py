"""Unit tests for offline random 3-point group builder."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from prairie_live.markpoints import template_meta
from prairie_live.random_groups import (
	GROUP_SIZE,
	build_random_groups,
	groups_xml_text,
	min_groups_for_coverage,
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


def test_select_preserves_three_point_membership():
	pts = _pool(8)
	groups = build_random_groups(pts, 4, seed=3)
	picked = select_groups_by_id(groups, [groups[2]["id"], groups[0]["id"]])
	assert [g["id"] for g in picked] == [groups[2]["id"], groups[0]["id"]]
	assert all(len(g["point_ids"]) == 3 for g in picked)
	xml = groups_xml_text(picked, power=100.0, meta=template_meta([]))
	assert xml.count("<PVMarkPointElement") == 2
