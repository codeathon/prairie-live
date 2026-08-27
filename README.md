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
python -m prairie_live mp-sync --series D:\MarkPoints.xml --via-relay 10.33.107.147:25100 --iterations 1 --n-groups 2 --group-size 9 --powers 0,0.75 --trigger none --mock-scores --log trials.jsonl
```

DTR photostim on COM3 (wire DTR to PFI1) — one paste:

```powershell
python -m prairie_live mp-sync --series D:\MarkPoints.xml --via-relay 10.33.107.147:25100 --iterations 1 --n-groups 2 --group-size 9 --powers 0,0.75 --trigger serial --serial COM3 --mock-scores --log trials.jsonl
```

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

One JSON object per line. Each trial produces two rows:

| `phase` | When |
|---------|------|
| `armed` | Identity written; series `-lmp`/`-mp` or SLM `-slm` sent |
| `done` | Trial finished — **use these rows** for results |

Key fields on `done` rows:

| Field | Meaning |
|-------|---------|
| `trial_index` | Trial number (0, 1, 2, …). **Same as TTL edge index** when using `--trigger serial`. |
| `group_name` | Group label in the trial XML (e.g. `PL_t0002_g2`) |
| `group_index` | 0-based group slot this iteration |
| `point_ids` | FOV point indices stimulated this trial |
| `power` | `UncagingLaserPower` for this trial (e.g. `0.0`, `0.75`) |
| `trigger` | `none`, `serial`, or `wait` |
| `trigger_selection` | `None` or `PFI1` (external trigger line in XML) |
| `t_cmd` | Unix time when Mark Points commands were sent |
| `t_ttl` | Stim time: equals `t_cmd` for `none`; **DTR pulse time** for `serial` |
| `score` | Group-level mean ΔF/F (one number per trial) |
| `score_kind` | `mock`, `relay_disk_dff`, or `none` |

**What is stored:** trial identity, TTL timing, and **one group-mean score**
per trial. Per-point ΔF/F is used in-memory to regroup on the next iteration
but is **not** written to a separate file. Relay frames are not saved.

Read completed trials in PowerShell:

```powershell
Get-Content trials.jsonl |
  ForEach-Object { $_ | ConvertFrom-Json } |
  Where-Object { $_.phase -eq "done" } |
  Select-Object trial_index, group_name, power, trigger_selection, score, score_kind, t_ttl
```

Without `--mock-scores`, scoring uses live relay frames (`score_kind`:
`relay_disk_dff`). Drop `--mock-scores` and keep the relay running with
Live/T-series frames available.
