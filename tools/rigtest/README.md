# Rig timing tests (scope PC)

Run from this folder on the scope PC (PrairieView + Trig In 1 cable here):

```powershell
cd C:\Users\User\code\prairie-live
git pull
cd tools\rigtest
py -m pip install pyserial          # once
```

| Script | Use |
|---|---|
| `line_toggle.py --com COM5` | 0.5 Hz square wave on RTS/DTR to find the TTL on Voltage Recording |
| `pv_series_test.py run --pulses N --first S --gap S --com COM5 [--line rts]` | run the series loaded in Mark Points; N TTL pulses |
| `dtr_train.py --com COM5 --n N --first S --gap S [--line rts]` | TTL pulses only |
| `vr_edges.py <csv> --ttl "<col>" --slm "<col>"` | pair each TTL with its SLM-out edge; MISS = no response |

Serial ports open with RTS and DTR low (no edge on open). Never use COM4 (MAMC).
