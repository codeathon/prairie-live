"""Load mp-sync settings from experiment.json (CLI overrides file values)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# Keys that map 1:1 onto argparse dest names (hyphen → underscore).
_CLI_KEYS = (
	"series",
	"scope_xml",
	"via_relay",
	"relay",
	"log",
	"host",
	"port",
	"password",
	"iterations",
	"n_groups",
	"group_size",
	"powers",
	"seed",
	"trigger",
	"serial",
	"ttl_width",
	"inter_trial",
	"pad_ms",
	"elite_frac",
	"mock_scores",
	"f0_s",
	"f1_s",
	"frame_poll",
	"disk_radius",
	"dry_run",
	"inspect",
	"stim_mode",
	"slm_pack",
	"images_dir",
	"visual_enabled",
	"visual_screen",
	"visual_refresh_hz",
	"visual_isi_s",
	"visual_lead_ms",
	"visual_dtr_frames",
	"visual_grating_frames",
	"visual_orientations",
	"visual_contrasts",
	"visual_spatial_freq",
	"visual_temporal_freq",
	"visual_stim_size_deg",
	"visual_texture",
	"visual_random",
)

# Mark Points / -slm geometry (mostly config-only; stim_mode also has a CLI flag).
_STIM_KEYS = (
	"stim_mode",
	"laser",
	"use_3d",
	"all_points_at_once",
	"spiral",
	"spiral_size_um",
	"spiral_revolutions",
	"fov_width_um",
	"fov_height_um",
	"image_width_px",
	"image_height_px",
	"pixel_size_um",
	"duration_ms",
	"initial_delay_ms",
	"inter_point_delay_ms",
	"interval_ms",
	"trigger_selection",
)


def default_config_path() -> Path:
	return Path("experiment.json")


def load_experiment(path: str | Path | None) -> dict[str, Any]:
	"""
	Read experiment JSON. Missing file → {}. Unknown keys kept for forward use.
	"""
	if path is None:
		return {}
	p = Path(path)
	if not p.is_file():
		raise FileNotFoundError(f"experiment config not found: {p}")
	with open(p, encoding="utf-8") as f:
		raw = json.load(f)
	if not isinstance(raw, dict):
		raise ValueError(f"experiment config must be a JSON object: {p}")
	# Drop comment-only keys.
	return {k: v for k, v in raw.items() if not str(k).startswith("_")}


def powers_to_list(powers: Any) -> list[float]:
	"""Accept [0, 75, 140] or \"0,75,140\" — UI-scale %, not 0–1."""
	if powers is None:
		return [0.0, 75.0]
	if isinstance(powers, (list, tuple)):
		return [float(x) for x in powers]
	return [float(x) for x in str(powers).split(",") if str(x).strip()]


def powers_to_cli_default(powers: Any) -> str:
	return ",".join(str(x) for x in powers_to_list(powers))


def apply_stim_to_meta(meta: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
	"""
	Overlay experiment stim fields onto Mark Points template meta.

	Why: series XML may omit laser/spiral/2D; lab SLM defaults live in experiment.json.
	"""
	out = dict(meta)
	if cfg.get("laser") is not None:
		out["uncaging_laser"] = str(cfg["laser"])
	if cfg.get("use_3d") is not None:
		out["use_3d"] = "True" if bool(cfg["use_3d"]) else "False"
	if cfg.get("all_points_at_once") is not None:
		out["all_points_at_once"] = (
			"True" if bool(cfg["all_points_at_once"]) else "False"
		)
	if cfg.get("spiral_revolutions") is not None:
		out["spiral_revolutions"] = str(cfg["spiral_revolutions"])
	if cfg.get("duration_ms") is not None:
		out["duration"] = float(cfg["duration_ms"])
	# UI shows ms; series XML commonly stores InitialDelay/InterPointDelay in seconds.
	if cfg.get("initial_delay_ms") is not None:
		out["initial_delay_ms"] = float(cfg["initial_delay_ms"])
		out["initial_delay"] = str(float(cfg["initial_delay_ms"]) / 1000.0)
	if cfg.get("inter_point_delay_ms") is not None:
		out["inter_point_delay_ms"] = float(cfg["inter_point_delay_ms"])
		out["inter_point_delay"] = str(float(cfg["inter_point_delay_ms"]) / 1000.0)
	if cfg.get("trigger_selection") is not None:
		out["trigger_selection"] = str(cfg["trigger_selection"])
	# Spiral on/off + size µm are applied when building -slm args / point attrs.
	if cfg.get("spiral") is not None:
		out["is_spiral"] = "True" if bool(cfg["spiral"]) else "False"
	if cfg.get("spiral_size_um") is not None:
		out["spiral_size_um"] = str(cfg["spiral_size_um"])
	if cfg.get("fov_width_um") is not None:
		out["fov_width_um"] = float(cfg["fov_width_um"])
	if cfg.get("fov_height_um") is not None:
		out["fov_height_um"] = float(cfg["fov_height_um"])
	if cfg.get("image_width_px") is not None:
		out["image_width_px"] = int(cfg["image_width_px"])
	if cfg.get("image_height_px") is not None:
		out["image_height_px"] = int(cfg["image_height_px"])
	if cfg.get("pixel_size_um") is not None:
		out["pixel_size_um"] = float(cfg["pixel_size_um"])
	if cfg.get("stim_mode") is not None:
		out["stim_mode"] = str(cfg["stim_mode"])
	return out


def merge_cli_over_config(args_ns: Any, cfg: dict[str, Any]) -> dict[str, Any]:
	"""
	Build a flat settings dict: config first, then non-None CLI values win.

	Argparse should use default=None for keys listed in the config so we can
	tell \"user omitted flag\" from \"user set the default\".
	"""
	out: dict[str, Any] = {}
	for k in _CLI_KEYS + _STIM_KEYS:
		if k in cfg and cfg[k] is not None:
			out[k] = cfg[k]
	for k in _CLI_KEYS:
		val = getattr(args_ns, k, None)
		if val is not None:
			out[k] = val
	# Booleans from store_true: only True when flag present; keep cfg False.
	for k in ("mock_scores", "dry_run", "inspect"):
		if getattr(args_ns, k, False):
			out[k] = True
		elif k not in out:
			out[k] = False
	# slm_pack: --slm-pack / --no-slm-pack (default True when unset).
	slm_pack = getattr(args_ns, "slm_pack", None)
	if slm_pack is not None:
		out["slm_pack"] = bool(slm_pack)
	elif "slm_pack" not in out:
		out["slm_pack"] = True
	if getattr(args_ns, "visual_enabled", None) is not None:
		out["visual_enabled"] = bool(args_ns.visual_enabled)
	elif "visual_enabled" not in out:
		out["visual_enabled"] = False
	if "powers" in out:
		out["powers"] = powers_to_list(out["powers"])
	elif "powers" not in out:
		out["powers"] = [0.0, 75.0]
	# Stim keys: CLI has no flags yet; config-only for now.
	for k in _STIM_KEYS:
		if k in cfg and k not in out:
			out[k] = cfg[k]
	return out
