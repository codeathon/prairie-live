# prairie-live

Mark Points closed-loop on a Bruker PrairieView rig: **relay** on the scope PC,
**mp-sync** on the analysis PC. Substitute your scope IPv4 for `10.33.107.221`.
PrairieView script password: Tools → Scripts → Edit Scripts (default `0000`).

```
[analysis PC]                              [scope PC]
 python -m prairie_live mp-sync …           PrairieView (Live)
        |  TCP 25100 frames / 25101 cmds         python -m prairie_live relay --grab
        +---------------------------------------- PV script port TCP 1236 (local)
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

The relay talks to PrairieView only on its script port (TCP 1236, one login):
frames via `-gi` into the relay's own buffer, Mark Points, `-gts` state, Live,
T-series and Abort as script commands. PrairieLink COM is not used — the
PrairieLink 6.0 build registered on the scope PC is test-only.
`--grab` streams frames for ΔF/F scoring and `trial_images/` PNGs.

```powershell
cd C:\Users\schollab\code\prairie-live
$env:PYTHONPATH = "$PWD\src"
python -m prairie_live relay --pv-host 127.0.0.1 --password UAKF --channel 1 --fps 12 --grab
```

Success: `frames 0.0.0.0:25100  ctrl 0.0.0.0:25101  PV=127.0.0.1`

### Heartbeat says `NO FRAME raw=None`

`-gi` asks PrairieView to write pixels into the relay's memory (process ID plus
buffer address), so the relay must run on the scope PC. Run the report on the
scope PC with Live Scan on, and watch PrairieView for an error popup:

```powershell
python -m prairie_live grab-doctor --password UAKF --channel 1 --rrd
```

It writes `grab_doctor.txt` (send it to Bruker). The `direct script -gi` lines
should show `wrote=` with a real `max=`.

## Analysis PC — live FOV

**Live Scan alone should already stream the FOV.** Mark Points are not required
for the left image (or the `fov` / `p99` traces). Hitting Live in PrairieView
refreshes the PV window; the analysis PC only moves if `relay --grab` is running.
Relay sends `-LiveScan` when grab starts; if the figure stays `STALE`, click the
analysis window and press `l`. If the heartbeat shows `max=0` after Live, check
`--channel`, take one **Snapshot** in PV, leave Live on, and watch `max=`. Status is in three places: the **window caption**,
a **yellow overlay** on the FOV, and stdout (`HUD v4`). `LIVE` + `max=` > 0
means frames are new. `no frame from relay` means `--grab` is off or the TCP
link is not delivering packets. Relay prints `grab heartbeat:` every 2s.

Rolling mean of the last `--avg-s` seconds (same `mean_stack` as
`trial_images/f0.png`). Keys: `t` T-series, `a` abort, `l` live scan, `q` quit.

The **same window** always has a right panel: whole-FOV mean (`fov`) and 99.5th
percentile (`p99`). Photostim of points you just created in PrairieView shows
up there (and as a red flash on the FOV) even without an XML of those points.
PrairieView has no GetMarkPoints — colored disk traces and cyan rings come
only from `--series` / `--config` (same `disk_mean` as mp-sync ΔF/F). Export
the fat MarkPoints XML of the points you actually fire and pass that file, or
the per-id lines will sit on the sample 8-point list. `--config experiment.json`
reads `series` and `disk_radius`. Later panels go on this grid, not extra windows.

Title `max=` is the latest raw PMT peak. `max=0` on a 512² frame means grab is
empty (wrong `--channel`, or Live not running in PV).
`grab 1x1 (not an FOV)` means pull this branch and restart **relay first**, then view.

```powershell
python -m prairie_live view --relay 10.33.107.221:25100
python -m prairie_live view --relay 10.33.107.221:25100 --avg-s 0.5
python -m prairie_live view --relay 10.33.107.221:25100 --config experiment.json --avg-s 0.5 --window-s 8
python -m prairie_live view --mock --series samples/MarkPoints_8fov.xml
```

## Analysis PC — mapping web UI

Opens `http://127.0.0.1:8765/`. No `experiment.json`. On start it
connects to `10.33.107.221:25100`, sends `-LiveScan` if PrairieView is
up, and shows a live FOV tile. Upload a Fat XML to open one tile per
power in a grid (filled as trials arrive). COM list from hardware
(default COM3). Stim mode default `slm`. XML fills duration, spiral,
inter-point delay, and estimated FOV µm.

```powershell
cd C:\Users\schollab\code\prairie-live
$env:PYTHONPATH = "$PWD\src"
python -m prairie_live gui --relay 10.33.107.221:25100
```

Groups have at least 3 points and cover every point once (8 → 4+4).
Each group runs at every power. After the last trial, pick candidates
and **Save XML**.

## Analysis PC — live ROI intensity

In the `view` window, drag a box or click a disk on the FOV to add a gold
`roi` trace (mean raw counts in that region) next to `fov` / `p99`. `r` clears
it. `--roi-radius` sets the click-disk radius (default 8 px).

```powershell
python -m prairie_live view --relay 10.33.107.221:25100 --avg-s 0.5 --roi-radius 8
```

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
| `trial_images/run_…/candidates.txt` | Ranked points (lowest stimulating power first) |
| `trial_images/run_…/candidates_top.xml` | Top-third fat XML for the next `--series` |
| `trial_images/run_…/next_series.xml` | User-picked candidates from the GUI Save XML button |
| `trial_images/run_…/summary.txt` | One line per trial |

## `experiment.json` reference

Keys starting with `_` in the file are comments only and are ignored at load time.
CLI flag names use hyphens (`--via-relay`, `--n-groups`, …).

| Key | Type | Description |
|-----|------|-------------|
| `series` | string | Mark Points input: `.xml` or `.gpl` path (FOV-normalized X/Y 0–1 for `stim_mode=slm`) |
| `scope_xml` | string \| null | Local path on scope PC when not using relay push (usually `null`; use `via_relay` instead) |
| `via_relay` | string | Relay address for Mark Points + frames, e.g. `10.33.107.221:25100` |
| `relay` | string \| null | Alternate relay host for frame scoring only (usually `null`; `via_relay` covers both) |
| `log` | string | JSONL trial log path (default `trials.jsonl`) |
| `host` | string | PrairieView script host when not using relay (scope loopback `127.0.0.1`) |
| `port` | int | PrairieView script TCP port (default `1236`) |
| `password` | string | PrairieView script password |
| `iterations` | int | How many times to reshuffle groups and run the full power sweep |
| `n_groups` | int | Number of random point groups per iteration |
| `group_size` | int | Target points per group (minimum 3 enforced) |
| `powers` | list[float] | Uncaging laser powers — same scale as PV UI `UncagingLaserPower` (not 0–1) |
| `repetitions` | int | Fixed Mark Points / SLM repetitions on every generated trial (≥1) |
| `stim_dff_min` | float | Disk ΔF/F threshold to count a point as stimulated (f0/f1) |
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
