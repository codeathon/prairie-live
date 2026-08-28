# Prairie live viewer + T-series (Windows)

Live frames and PrairieLink commands for a Bruker PrairieView rig.

**Images only work on the scope PC.** PrairieLink `GetImage` is local-only.
To watch frames on a second machine, run the **relay** on the scope PC and
the **viewer** on the analysis PC. Commands (`abort`, `GetState`, …) go
through the same relay.

Substitute your scope IPv4 for `10.33.107.147`. Password is in PrairieView:
Tools → Scripts → Edit Scripts (default `0000`).

```
[analysis PC]                              [scope PC]
 python -m prairie_live view --relay …        PrairieView
        |  TCP 25100 frames / 25101 cmds         python -m prairie_live relay
        +---------------------------------------- PrairieLink COM (local)
```

## Install (both PCs)

```bat
cd prairie-live
git fetch origin
git checkout feat/relay-queries
pip install -r requirements.txt
set PYTHONPATH=%CD%\src
```

PowerShell: `$env:PYTHONPATH = "$PWD\src"` (must be set in every new window).

## Local live stream (scope PC)

Start **Live** in PrairieView first.

```bat
python -m prairie_live view --host 127.0.0.1 --password 0000
```

Keys: `t` T-series, `a` abort, `l` live scan, `q` quit.

PrairieView still writes its own TIFFs. Zoom, laser, frame count, and save
directory are not set here.

## Relay (scope PC → analysis PC)

### Scope PC — firewall (Administrator, once)

```bat
netsh advfirewall firewall add rule name="prairie-live relay" dir=in action=allow protocol=TCP localport=25100-25101
```

Or PowerShell:

```powershell
New-NetFirewallRule -DisplayName "prairie-live relay" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 25100-25101 -Profile Any
```

### Scope PC — start the relay

Leave this window open. Ctrl-C or close the window to stop it.

```bat
set PYTHONPATH=%CD%\src
python -m prairie_live relay --pv-host 127.0.0.1 --password 0000 --channel 1 --fps 12
```

Grab/streaming is **off by default** (COM GetImage can surface PV errors like
`Unexpected parameter <address>`). Add `--grab` only for the live viewer.

Success looks like:

```
frames 0.0.0.0:25100  ctrl 0.0.0.0:25101  PV=127.0.0.1
```

Confirm it is listening:

```bat
netstat -ano | findstr "25100 25101"
```

### Analysis PC — reachability

```bat
python -c "import socket;s=socket.socket();s.settimeout(5);print(s.connect_ex(('10.33.107.147',25100)))"
python -c "import socket;s=socket.socket();s.settimeout(5);print(s.connect_ex(('10.33.107.147',25101)))"
```

`0` means the port is open. Timeout means firewall or wrong IP. `10061`
means nothing is listening (relay not running).

### Analysis PC — frames

Headless check (no matplotlib):

```bat
set PYTHONPATH=%CD%\src
python -c "from prairie_live.relay_client import RelayClient; import time; c=RelayClient('10.33.107.147',25100); c.connect(); time.sleep(3); f=c.get_frame(); print(f.shape, f.dtype, f.min(), f.max()); c.disconnect()"
```

Viewer:

```bat
python -m prairie_live view --relay 10.33.107.147:25100
```

## Commands from the analysis PC

Relay must already be running. These talk to port 25101.

### Ping (does nothing to PrairieView)

```bat
python -c "from prairie_live.relay_client import RelayClient; c=RelayClient('10.33.107.147',25100); c.connect(); print(c.ping()); c.disconnect()"
```

Expect `{'ok': True, 'cmd': 'ping'}`.

### Read state (`GetState`)

Keys live in PrairieView's environment file under `PVStateShard`.

```bat
python -m prairie_live get-state --relay 10.33.107.147:25100 --key dwellTime
python -m prairie_live get-state --relay 10.33.107.147:25100 --key micronsPerPixel --index XAxis
python -m prairie_live get-state --relay 10.33.107.147:25100 --key micronsPerPixel --index YAxis
```

### Stage position (`GetMotorPosition`)

```bat
python -m prairie_live get-motor --relay 10.33.107.147:25100 --axis X
python -m prairie_live get-motor --relay 10.33.107.147:25100 --axis Y
python -m prairie_live get-motor --relay 10.33.107.147:25100 --axis Z
```

### Abort (stops Live or T-series)

```bat
python -m prairie_live abort --relay 10.33.107.147:25100
```

### Start T-series (writes files)

Starts whatever T-series is already configured in PrairieView.

```bat
python -m prairie_live tseries --relay 10.33.107.147:25100
```

Same commands on the scope PC itself, talking to PrairieView over loopback:

```bat
python -m prairie_live abort --host 127.0.0.1 --password 0000
python -m prairie_live tseries --host 127.0.0.1 --password 0000
python -m prairie_live get-state --host 127.0.0.1 --password 0000 --key dwellTime
python -m prairie_live get-motor --host 127.0.0.1 --password 0000 --axis X
```

## Restart the relay

On the scope PC: close the relay window (or Ctrl-C), then:

```bat
cd prairie-live
set PYTHONPATH=%CD%\src
python -m prairie_live relay --pv-host 127.0.0.1 --password 0000 --channel 1 --fps 12
```

## Dry run (no microscope)

```bat
python -m prairie_live view --mock
```

## Mark Points sync loop (analysis PC + scope relay)

Maps FOV points into groups, fires one group×power trial at a time, authors
`trial_index` to JSONL, and optionally pulses serial **DTR** on the analysis
PC (PackIO→PFI). **No Windows file share is required** when using `--via-relay`:
the analysis PC pushes the trial XML over the relay; the scope writes it
locally and runs `-LoadMarkPoints` / `-MarkPoints`.

Prefer a fat `MarkPoints.xml` **or** a `.gpl` point list (`PVGalvoPointList`)
for `--series`. With `stim_mode=slm`, points are randomly grouped (≥3 per
group), packed into one `-MarkAllPoints` command per power, and each DTR
pulse advances to the next packed set (`trigger_index` in the JSONL).

Paste one line at a time in **PowerShell**. (Cmd.exe: use `set PYTHONPATH=%CD%\src` instead of `$env:PYTHONPATH`.)

### Scope PC — start the relay

```powershell
cd C:\Users\schollab\code\prairie-live
git fetch origin
git checkout feat/markpoints-sync-loop
git pull
$env:PYTHONPATH = "$PWD\src"
python -m prairie_live relay --pv-host 127.0.0.1 --password 0000 --channel 1 --fps 12
```

Leave that window open. Success looks like: `frames 0.0.0.0:25100  ctrl 0.0.0.0:25101`.

### Analysis PC — run the loop (TTL stays here)

```powershell
cd C:\Users\schollab\code\prairie-live
git fetch origin
git checkout feat/markpoints-sync-loop
git pull
$env:PYTHONPATH = "$PWD\src"
```

Software trigger (no serial) — one paste:

```powershell
python -m prairie_live mp-sync --series D:\MarkPoints.xml --via-relay 10.33.107.147:25100 --iterations 1 --n-groups 2 --group-size 9 --powers 0,75 --trigger none --mock-scores --log trials.jsonl
```

DTR photostim on COM3 (wire DTR to PFI1) — one paste:

```powershell
python -m prairie_live mp-sync --series D:\MarkPoints.xml --via-relay 10.33.107.147:25100 --iterations 1 --n-groups 2 --group-size 9 --powers 0,75 --trigger serial --serial COM3 --mock-scores --log trials.jsonl
```

### Embedded PsychoPy gratings (analysis PC)

mp-sync can drive the animal monitor and own **COM3** in one process (no
standalone PsychoPy script). **RTS** marks the visual grating epoch; **DTR**
still pulses PFI1 for photostim.

- Second monitor: set `visual_screen` (default `1`) in `experiment.json`
- Install PsychoPy on the analysis PC only:
  `pip install -r requirements-visual.txt`
- Enable via config (`"visual_enabled": true`) or CLI `--visual`

```powershell
python -m prairie_live mp-sync --config experiment.json --visual --trigger serial --serial COM3
```

Key `visual_*` fields in `experiment.json`: `visual_lead_ms` (DTR delay after
grating onset, legacy `stimOnTime=12` @ 120 Hz), `visual_dtr_frames` (hold
width), `visual_grating_frames`, `visual_orientations`, `visual_contrasts`,
`visual_isi_s`. Trial JSONL / `trials.txt` log `visual_ori_deg`,
`visual_contrast_pct`, `t_rts_mono`, and `t_ttl_mono`.

The scope PC still runs `relay --grab`; PsychoPy runs on the analysis PC only.

`--via-relay` replaces `--scope-xml` + direct port 1236 for Mark Points. The
relay must already be running on the scope. PrairieView script password is
still `0000` on the relay process (`--password`), not a Windows share login.

### Experiment config (`experiment.json`)

All `mp-sync` knobs can live in a JSON file (SLM lab defaults shipped in
repo-root `experiment.json`). CLI flags override the file. If
`./experiment.json` exists, it is loaded automatically; or pass `--config PATH`.

```powershell
# Edit series / COM / relay in experiment.json, then:
python -m prairie_live mp-sync --config experiment.json
# CLI overrides file values:
python -m prairie_live mp-sync --config experiment.json --iterations 2
```

SLM-oriented fields: `stim_mode` (`series` | `slm`), `laser` (`Monaco`),
`use_3d`, `spiral`, `spiral_size_um` (54.5), `spiral_revolutions` (8),
`trigger_selection` (`PFI1`), `fov_width_um` (required for spiral when
`stim_mode=slm` — converts µm → FOV fraction for `-MarkAllPoints` / `-slm`).

- `stim_mode=series` (default): `-LoadMarkPoints` + `-MarkPoints` per trial
- `stim_mode=slm`: pack all groups (per power) into one `-MarkAllPoints` /
  `-slm` string; each set uses `Trigger=PFI1` with Delay between sets. One
  DTR pulse per group; JSONL `trigger_index` / `group_trigger_map` records
  which pulse fired which group (PV does not report this). Spiral needs
  `fov_width_um` set to the optical FOV
  width in µm.

### Trial log (`trials.jsonl`)

Written on the **analysis PC** in the directory where you run `mp-sync`
(default: `--log trials.jsonl` → `.\trials.jsonl` under that folder).
Each run **appends** lines; delete or rename the file between experiments.

**How to read results:** open **`trials.txt`** (plain text next to `trials.jsonl`).
That file is written automatically — Notepad-friendly blocks, not JSON.

```
trials.jsonl   ← machine (keep for analysis)
trials.txt     ← human (open this)
```

Example `trials.txt` block:

```
============================================================
TRIAL 4
============================================================
  Fired points:  1, 4, 8
  Group name:    PL_t0004_g5
  Laser power:   140.0  (Prairie UI UncagingLaserPower)
  Stim mode:     slm   trigger=serial  line=PFI1
  TTL / pulse:   4 of 4  (0-based pulse index among 5 packed groups)
  ΔF/F score:    0.009619  (relay_disk_dff)
  Images:        ...\trial_images\run_...\t0004
```

Also: `python -m prairie_live show-log` prints a table from the JSONL.

**JSONL fields (for scripts):**

| Field | Meaning |
|-------|---------|
| `summary` | One-line human digest |
| `trial_index` | Trial number (0, 1, 2, …). Same as TTL edge index for `--trigger serial` |
| `trigger_index` / `n_triggers` | Which packed SLM pulse this was (0-based) / how many pulses in the batch |
| `point_ids` | **The FOV indices that actually fired** this pulse (e.g. `1,4,8`) |
| `power` | Prairie UI `UncagingLaserPower` (e.g. `140`) |
| `score` / `score_kind` | Group mean ΔF/F; `relay_disk_dff`, `mock`, or `none` |
| `image_paths` | Folder with `f0.png` / `f1.png` / `dff.png` |
| `record_paths` | Pretty `trial.json` + `readable.txt` beside those PNGs |

Phases:

| `phase` | When |
|---------|------|
| `slm_packed` | Once per power batch: full `group_trigger_map` + raw `slm_parts` (argv dump) |
| `armed` | Trial identity recorded before stim / TTL |
| `done` | Trial finished — **use these rows** for results |

The giant `slm_parts` array is **only** on `slm_packed` (not on every trial).
Per-trial folders also get:

```
trial_images/run_…/t0004/
  f0.png  f1.png  dff.png
  trial.json      ← pretty JSON
  readable.txt    ← same block as in trials.txt
trial_images/run_…/summary.txt   ← one line per trial
trial_images/run_…/pulse_map.txt ← pulse → points for the packed batch
```

Without `--mock-scores`, scoring uses live relay frames (`score_kind`:
`relay_disk_dff`). Drop `--mock-scores` and keep the relay running with
Live/T-series frames available.
