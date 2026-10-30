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

Only needed when PsychoPy gratings are enabled in the web UI (`visual_enabled`).
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
the per-id lines will sit on the sample 8-point list. Optional `--config`
JSON can supply `series` and `disk_radius`. Later panels go on this grid, not
extra windows.

Title `max=` is the latest raw PMT peak. `max=0` on a 512² frame means grab is
empty (wrong `--channel`, or Live not running in PV).
`grab 1x1 (not an FOV)` means pull this branch and restart **relay first**, then view.

```powershell
python -m prairie_live view --relay 10.33.107.221:25100
python -m prairie_live view --relay 10.33.107.221:25100 --avg-s 0.5
python -m prairie_live view --relay 10.33.107.221:25100 --series samples/MarkPoints_8fov.xml --avg-s 0.5 --window-s 8
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

## Analysis PC — run (preferred: web UI)

```powershell
cd C:\Users\schollab\code\prairie-live
$env:PYTHONPATH = "$PWD\src"
python -m prairie_live gui
```

Open `http://127.0.0.1:8765/`. Pick your Mark Points `.xml` / `.gpl`, relay, COM,
powers, stim mode, and trigger. **For PV-saved series XML, Duration / SpiralRevs /
InterPointDelay / Repetitions / X,Y,SpiralSize come from that file** — no
`experiment.json` is required (and lab defaults will not rewrite those fields).

### CLI mp-sync (optional)

```powershell
python -m prairie_live mp-sync --series samples/MarkPoints_25_trig_in1.xml --stim-mode series --trigger none --via-relay 10.33.107.221:25100 --powers 160
```

Optional `--config path.json` still works for session logistics (relay, powers,
group size). Stim dwell/spiral for PV-saved Mark Points stay on the XML.

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

## Optional `--config` JSON

`experiment.json` is **not** required. The web UI uses lab defaults + your Mark
Points file. If you pass `--config path.json` to CLI `mp-sync` / `view`, keys
starting with `_` are comments. Useful keys: `series`, `via_relay`, `serial`,
`powers`, `stim_mode`, `trigger`, `n_groups`, `group_size`, `fov_width_um`
(SLM spiral µm→FOV). For **PV-saved** Mark Points XML, do **not** put
`duration_ms` / `spiral_revolutions` / `inter_point_delay_ms` in the config —
those stay on the XML.

## Mark Points coordinates (galvo vs FOV)

PrairieView writes two spaces for the same targets:

| When you save | Typical file | X/Y meaning |
|---------------|--------------|-------------|
| Point list / save before running | `.gpl` / `PVSavedMarkPointSeriesElements` | **Galvo volts** (can be &lt;0 or \|Y\|&gt;1) |
| Fat series after a Mark Points run | `PVMarkPointSeriesElements` + nested `<Point>` | **FOV fractions 0–1** of the Live image |

Conversion (matches PV; implemented in `scan_geometry.py`):

```
FOV  = 0.5 + (galvo − currentScanCenter) / currentScanAmplitude
galvo = currentScanCenter + (FOV − 0.5) · currentScanAmplitude
SpiralWidth_FOV = SpiralSize_galvo / |amp|
```

`currentScanAmplitude` from `-gts` is the scan **full width** (frame =
center ± amp/2). Using `2·amp` compressed the constellation toward center
(cyan rings too close vs PV’s Mark Points GUI). Classic `stim_mode=slm`
remaps galvo→FOV at fire time with LiveScan on.

**SpiralSize / power:** never-run PVSaved `SpiralSize` is galvo volts (do not
send ~0.185 as FOV fraction). For `-slm`, the GUI prefers lab spiral
**54.5 µm / FOV µm** (≈0.048).

**Monaco laser power (lab):** Out min/max **0–2.5**, Display max **1000**.
Mark Points GUI and `-slm` use **Display** (e.g. Powers `150`). Series XML
`UncagingLaserPower` stores **Out** (`150/1000×2.5 = 0.375`). Conversion
factor Display/Out = 400. Powers is the **total** for the group hologram;
simultaneous SLM splits that across spots (~150/4 if the group has 4).
