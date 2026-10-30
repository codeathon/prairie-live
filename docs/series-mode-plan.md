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

## Bruker answers (Oct 30)

- **SLM out**: always a short 5 V pulse when a mask changes; the mask is loaded
  **2 ms** after the edge. (Alternative: the laser artifact in the imaging.)
- Two supported workflows, both a saved series (Mark Points grid: *Wait for Trigger*
  = `TriggerFrequency`, *Trigger Selection*, *Voltage Recording Experiment* per line):
  1. **Trigger the first line only.** Group 1 `First Repetition` / `Trig In 1`, the rest
     `Never`; PV then runs the lines back to back (`InitialDelay` + `Interval`). All masks
     *and* waveforms are generated/uploaded first, so allow a long delay before the
     trigger. (Bruker screenshot: 22.1 ms then 2 ms initial delays, 100.26 ms interval.)
  2. **Trigger every line.** Each line `First Repetition` / `Trig In 1`. Masks are made up
     front; each line's waveform uploads only after the previous line's trigger runs
     (~ms). Only the first trigger needs the long wait. **Target workflow** — one DTR
     per group, software/visual-stim controls the spacing.

Rig tests: C = option 1, F = option 2 (`dtr_train.py` for the pulses).

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
| 8 | DTR pulses: one per group (option 2) or one to start (option 1) | first pulse after mask-gen wait; later ones ≥ burn + few ms apart |
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
- Does `-MarkPoints` return DONE early while Live runs?
- Series power units: Out (0.4 ≈ 160 UI) — confirm on a bleach slide.
- SLM out pulse width (sample VR fast enough); edges present for script-run series?
- Option 2: shortest safe first-pulse wait and minimum group spacing on our rig (test F).
- How PV stores the per-line *Voltage Recording Experiment* in the saved XML (test E).
