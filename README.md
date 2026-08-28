# prairie-live

Mark Points closed-loop on a Bruker PrairieView rig: **relay** on the scope PC,
**mp-sync** on the analysis PC. Substitute your scope IPv4 for `10.33.107.147`.
PrairieView script password: Tools → Scripts → Edit Scripts (default `0000`).

```
[analysis PC]                              [scope PC]
 python -m prairie_live mp-sync …           PrairieView (Live)
        |  TCP 25100 frames / 25101 cmds         python -m prairie_live relay --grab
        +---------------------------------------- PrairieLink COM (local)
```

Set `PYTHONPATH` in every new shell (PowerShell):

```powershell
cd C:\Users\schollab\code\prairie-live
$env:PYTHONPATH = "$PWD\src"
```

Cmd.exe: `set PYTHONPATH=%CD%\src`

## Install

### Both PCs

```powershell
cd C:\Users\schollab\code\prairie-live
git pull
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
```

### Analysis PC only — PsychoPy gratings (optional)

Only needed when `visual_enabled` is `true` in `experiment.json`.
Python 3.12: do **not** run plain `pip install psychopy` (pulls broken `pywinhook`).

```powershell
pip install -U pip
pip uninstall pywinhook psychopy pyglet -y
pip install "psychopy>=2024.1" --no-deps
pip install -r requirements-visual.txt
python -c "from psychopy import visual; print('PsychoPy OK')"
```

Ignore the `pywinhook` missing warning — mp-sync does not use keyboard hooks.

### Scope PC — firewall (Administrator, once)

```powershell
New-NetFirewallRule -DisplayName "prairie-live relay" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 25100-25101 -Profile Any
```

## Scope PC — start relay

Start **Live** in PrairieView first. Leave this window open.

`--grab` streams frames for ΔF/F scoring and `trial_images/` PNGs (required for
mp-sync with `images_dir` set).

```powershell
cd C:\Users\schollab\code\prairie-live
$env:PYTHONPATH = "$PWD\src"
python -m prairie_live relay --pv-host 127.0.0.1 --password 0000 --channel 1 --fps 12 --grab
```

Success: `frames 0.0.0.0:25100  ctrl 0.0.0.0:25101  PV=127.0.0.1`

## Analysis PC — run mp-sync

Edit `experiment.json` (series path, relay IP, COM port, powers, etc.), then:

```powershell
cd C:\Users\schollab\code\prairie-live
$env:PYTHONPATH = "$PWD\src"
python -m prairie_live mp-sync --config experiment.json
```

CLI flags override the file, e.g. `--iterations 2` or `--visual` to force
gratings on for one run.

### Outputs

| File / folder | Purpose |
|---------------|---------|
| `trials.jsonl` | Machine log (appends each run) |
| `trials.txt` | Human-readable trial blocks (open this first) |
| `trial_images/run_…/` | Per-trial `f0.png`, `f1.png`, `dff.png` |
| `trial_images/run_…/recommendation.txt` | Best group×power summary |
| `trial_images/run_…/summary.txt` | One line per trial |

## `experiment.json` reference

Keys starting with `_` in the file are comments only and are ignored at load time.
CLI flag names use hyphens (`--via-relay`, `--n-groups`, …).

| Key | Type | Description |
|-----|------|-------------|
| `series` | string | Mark Points input: `.xml` or `.gpl` path (FOV-normalized X/Y 0–1 for `stim_mode=slm`) |
| `scope_xml` | string \| null | Local path on scope PC when not using relay push (usually `null`; use `via_relay` instead) |
| `via_relay` | string | Relay address for Mark Points + frames, e.g. `10.33.107.147:25100` |
| `relay` | string \| null | Alternate relay host for frame scoring only (usually `null`; `via_relay` covers both) |
| `log` | string | JSONL trial log path (default `trials.jsonl`) |
| `host` | string | Direct PrairieLink host when not using relay (scope loopback `127.0.0.1`) |
| `port` | int | Direct PrairieLink TCP port (default `1236`) |
| `password` | string | PrairieView script password |
| `iterations` | int | How many times to reshuffle groups and run the full power sweep |
| `n_groups` | int | Number of random point groups per iteration |
| `group_size` | int | Target points per group (minimum 3 enforced) |
| `powers` | list[float] | Uncaging laser powers — same scale as PV UI `UncagingLaserPower` (not 0–1) |
| `seed` | int | RNG seed for group formation (`0` = deterministic) |
| `trigger` | string | TTL mode: `serial` (DTR on `serial` port), `wait` (PFI wait), or `none` |
| `serial` | string | COM port for DTR photostim pulse, e.g. `COM3` |
| `ttl_width` | float | DTR pulse width in seconds |
| `inter_trial` | float | Pause between trials in seconds |
| `pad_ms` | float | Extra settle time after PrairieView ack before TTL (ms) |
| `elite_frac` | float | Fraction of top-scoring points biased into later group draws (0–1) |
| `mock_scores` | bool | Fake ΔF/F scores (dry testing without relay frames) |
| `f0_s` | float | Pre-TTL baseline window for scoring (seconds) |
| `f1_s` | float | Post-TTL response window for scoring (seconds) |
| `frame_poll` | float | Relay frame poll interval during F0/F1 capture (seconds) |
| `disk_radius` | int | Pixel radius around each point for disk-mean ΔF/F |
| `dry_run` | bool | Plan trials and log without firing PrairieView or TTL |
| `inspect` | bool | Print group/plan details and exit without running |
| `images_dir` | string | Root for trial PNG folders; each run → `images_dir/run_YYYYMMDD_HHMMSS/` (needs relay `--grab`) |
| `stim_mode` | string | `slm` (`-MarkAllPoints` / `-slm`) or `series` (`-LoadMarkPoints` / `-MarkPoints`) |
| `slm_pack` | bool | `true`: one packed `-MarkAllPoints` per power; `false`: one per group |
| `laser` | string | Uncaging laser name in Mark Points XML, e.g. `Monaco` |
| `use_3d` | bool | Match series `Use3D`; set `true` when points have nonzero Z |
| `all_points_at_once` | bool | PrairieView `AllPointsAtOnce` flag |
| `spiral` | bool | Spiral scan on each point |
| `spiral_size_um` | float | Spiral diameter in µm |
| `spiral_revolutions` | float | Number of spiral revolutions |
| `image_width_px` | int | Frame width in pixels (relay `--width`) |
| `image_height_px` | int | Frame height in pixels (relay `--height`) |
| `fov_width_um` | float | Optical FOV width in µm (converts spiral size for `-slm`) |
| `fov_height_um` | float | Optical FOV height in µm |
| `pixel_size_um` | float | µm per pixel at current zoom |
| `duration_ms` | float | Stim duration per point (ms); match `.gpl` / UI |
| `initial_delay_ms` | float | Delay before first point fires (ms) |
| `inter_point_delay_ms` | float | Delay between points in a group (ms) |
| `interval_ms` | float | Reference interval ≈ duration + inter-point delay (documentation) |
| `trigger_selection` | string | PrairieView trigger line, e.g. `PFI1` |
| `visual_enabled` | bool | Run embedded PsychoPy gratings on analysis PC (owns COM3 RTS+DTR) |
| `visual_screen` | int | Monitor index (`0` = primary, `1` = second display) |
| `visual_refresh_hz` | float | Display refresh rate used for frame timing |
| `visual_isi_s` | float | Inter-stimulus interval between gratings (seconds) |
| `visual_lead_ms` | float | Delay from grating onset (RTS) to DTR photostim pulse (ms) |
| `visual_dtr_frames` | int | DTR hold length in display frames |
| `visual_grating_frames` | int | Grating epoch length in display frames |
| `visual_orientations` | list[float] | Grating orientations in degrees |
| `visual_contrasts` | list[float] | Michelson contrasts in percent (e.g. `16` = 16%) |
| `visual_spatial_freq` | float | Spatial frequency (cycles/degree) |
| `visual_temporal_freq` | float | Temporal frequency (Hz) |
| `visual_stim_size_deg` | float | Grating aperture diameter (degrees) |
| `visual_texture` | string | PsychoPy texture, e.g. `sqr` |
| `visual_random` | bool | Randomize orientation/contrast each trial |
