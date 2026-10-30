# Series mode: one load, one run, hardware-timed groups

Status: **plan — pending rig tests A–E** (`RUN_ORDER.txt`, Oct 29).

## Why

Bruker (Oct 29): software cannot know when SLM masks are generated or when PV is
ready for a trigger after `-MarkAllPoints`. Per-group software/DTR triggering is not
supported. On SLM rigs PV loads a *sequence* of masks and switches between them on
a hardware trigger from the **SLM out** port of the IO Core.

So every approach that fired groups one by one from Python (per-group `-slm`, packed
`-slm` with one DTR per group, Live on/off toggling, arm delays, rescue pulses,
early-DONE handling) relied on timing PV does not expose. Replace it with PV's own
sequencing, as in Bruker's benchmark (`Spots_.gpl` + `Spots_.xml`, 400 masks ≈ 7 s).

## Script sequence (one run)

| # | Command | Notes |
|---|---|---|
| 1 | login on 1236 | password |
| 2 | `-LoadMarkPoints <run.gpl> True` | all points (galvo V) + `PVGalvoPointGroup` Group 1…N |
| 3 | `-LoadMarkPoints <run.xml>` | `PVSavedMarkPointSeriesElements`, one element per group×power, `Points="Group k"` + `Indices` |
| 4 | `-SetFileName MarkPoints <run_id> addDateTime` | fresh folder → no "Markpoints already exists" dialog |
| 5 | `-LiveScan on` (or T-series) | imaging for ΔF/F |
| 6 | start Voltage Recording | via series link (test E) or `-VoltageRecording …` |
| 7 | `-MarkPoints` | PV builds masks, then steps groups by hardware |
| 8 | optional single DTR (Trig In 1, FirstRepetition on element 1) | only if test C shows it starts the run |
| 9 | wait DONE / planned end; `-Abort` only on Stop; `-Exit` | |

Group spacing = per-element `InitialDelay` (e.g. 3000 ms). Power is per element, so
one series can hold every group × every power.

## Timing source of truth: SLM out → Voltage Recording

- T-connect IO Core **SLM out** into a spare VR analog input (high-Z, keep the SLM cable).
- Optional: uncaging laser gate (Pockels/AOM) on a second input = actual light-on.
- VR shares the imaging timebase, so edges align to frames without PC clocks.
- Edge k = group k fired. Missing edges = missed groups (log + don't score).
- `vr_edges.py` (scratch tool) prints edges, widths, gaps, laser lag.

Fallback if VR is unavailable: microcontroller timestamps SLM-out edges on the analysis
PC (few-ms error vs relay frame stamps), or schedule-only times checked against the
`p99` red flash.

## Code changes (after tests pass)

1. `markpoints.py`: writer for Bruker-format pair — `.gpl` with groups + series XML
   referencing them (galvo coords, no FOV conversion, no nested points).
2. `pv_script.py` / relay: push + load a `.gpl` (`-lmp <gpl> True`), `-SetFileName`,
   optional VR start; `run_series(gpl, xml)` as one stim-hold.
3. `sync_loop.py`: new `stim_mode="series_slm"` — build one pair per run (or per power),
   one `-MarkPoints`, then read VR edges (or schedule) → per-group `t_burn`.
4. Scoring: F0/F1 per group from timestamped frames around `t_burn`
   (`slice_group_frames`, already in `sync_loop.py`).
5. GUI: mode selector; show planned vs measured group times; flag missing edges.
6. Retire for SLM: per-group `-slm`, packed `-slm` + DTR train, Live toggling,
   `allow_early_done`, rescue pulses (keep code behind `stim_mode=slm` until 3 works).

## Open questions (rig / Bruker)

- Does a series element fire its group simultaneously via SLM (no `AllPointsAtOnce` attr)?
- Mask build time on our rig vs Bruker's 7 s / 400.
- Is the group gap exactly `InitialDelay + Duration`, or + variable mask-switch time?
- Does a Trig In 1 wait on element 1 begin only after masks are built?
- Does `-MarkPoints` return DONE early while Live runs?
- Series power units: Out (0.4 ≈ 160 UI) — confirm on a bleach slide.
- SLM out signal: level, polarity, width, one edge per mask switch? Active for script-run series?
- How PV stores a VR link in a saved series (test E).
