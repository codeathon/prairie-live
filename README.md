# Prairie live viewer + T-series (Windows)

Python and PrairieView both run on Windows. The viewer uses PrairieLink COM:
`Connect(scope_ip, password)` then `GetImage_2` in a loop. Press `t` to start
the T-series that is already configured in PrairieView.

```
[Windows analysis PC]                    [Windows scope PC]
 python -m prairie_live view             PrairieView (Live or T-series)
        |  COM Connect(ip, password)
        +-------------------------------- PrairieLink
```

Same box: `--host 127.0.0.1`. Two boxes: PrairieLink must be installed on
**both** (the COM object lives on the Python PC; it talks to PrairieView over
the network). Password is in PrairieView: Tools → Scripts → Edit Scripts
(default `0000`). Allow TCP 1236 through Windows Firewall on the scope PC.

## Install (Python PC)

```bat
cd prairie-live
pip install -r requirements.txt
set PYTHONPATH=src
```

## Live stream

Start **Live** in PrairieView, or hit `l` after the window opens.

```bat
python -m prairie_live view --host 127.0.0.1 --password 0000
python -m prairie_live view --host 192.168.1.50 --password 0000
```

Keys: `t` T-series, `a` abort, `l` live scan, `q` quit.

The display keeps updating during a T-series. PV still writes its own TIFFs
to the path set in PrairieView. Zoom, laser, frame count, and save directory
are not set here.

## T-series without the viewer

```bat
python -m prairie_live tseries --host 192.168.1.50 --password 0000
python -m prairie_live abort --host 192.168.1.50 --password 0000
```

## If the Python PC does not have PrairieLink

Run the COM grabber on the scope PC and point the viewer at it:

```bat
REM scope PC
python -m prairie_live relay --pv-host 127.0.0.1 --password 0000

REM analysis PC
python -m prairie_live view --relay 192.168.1.50:25100
```

## Relay queries (analysis PC)

Read-only. These call PrairieLink COM on the scope PC and return the value
over the control socket (port 25101). `SendScriptCommands` cannot do this —
it only reports success/fail.

```bat
REM analysis PC, relay already running on the scope PC
python -m prairie_live get-state --relay 10.33.107.147:25100 --key dwellTime
python -m prairie_live get-state --relay 10.33.107.147:25100 --key micronsPerPixel --index XAxis
python -m prairie_live get-motor --relay 10.33.107.147:25100 --axis X
```

`GetState` keys live in PrairieView's environment file under `PVStateShard`.
Common ones: `dwellTime`, `micronsPerPixel` (needs `--index XAxis` or `YAxis`).
`GetMotorPosition` axis is `X`, `Y`, or `Z`.


```bat
python -m prairie_live view --mock
```
