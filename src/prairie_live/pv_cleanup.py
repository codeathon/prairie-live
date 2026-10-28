"""Abort in-flight PV stim (session start + Stop — not between trials).

A prior -slm / -MarkPoints with -NoWait can still be \"in progress\" when the
next Run starts (e.g. SLM then series). Clear once at session start.
"""

from __future__ import annotations

import time
from typing import Any

# Kept for tests / optional manual clear; Stop no longer loads this by default.
EMPTY_MARK_POINTS_XML = """<?xml version="1.0" encoding="utf-8"?>
<PVMarkPointSeriesElements Iterations="1" IterationDelay="0.00" CalcFunctMap="False" AllPointsAtOnce="False" Use3D="False">
</PVMarkPointSeriesElements>
"""


def abort_and_clear_stim(
	client: Any,
	*,
	settle_s: float = 0.6,
	restore_live: bool = True,
) -> dict[str, Any]:
	"""
	``client``: RelayClient or anything with abort / start_live.

	1. force_interrupt (if available) — close hung -slm socket so Abort is not
	   queued behind DONE/timeout (PV "won't Abort" hang)
	2. -Silent -Abort — cancel armed -slm / -MarkPoints (and suppress boxes)
	3. settle — let PV drop SLM spiral waveforms before series -lmp
	   (too short a settle after -slm → Critical Error Int32 in spiral DAC)
	4. -LiveScan on — only when ``restore_live`` (Abort kills Live). Skip for
	   SLM session start: Live on→off right before -slm misses Trig In 1.

	Call at **session start** and on **Stop**, never between every trial.
	"""
	notes: list[str] = []
	ok = True

	# abort() waits ~0.75s for -gi then drops a hung -slm socket itself.
	try:
		out = client.abort()
		notes.append("abort")
		if isinstance(out, dict) and out.get("ok") is False:
			ok = False
			notes.append(f"abort_fail:{out.get('error', out)}")
	except Exception as exc:
		ok = False
		notes.append(f"abort_err:{exc}")

	if settle_s and settle_s > 0:
		time.sleep(float(settle_s))
		notes.append(f"settle_{settle_s:g}s")

	# Second Abort after settle — clears leftover -MarkAllPoints more reliably.
	try:
		out = client.abort()
		notes.append("abort2")
		if isinstance(out, dict) and out.get("ok") is False:
			notes.append(f"abort2_fail:{out.get('error', out)}")
	except Exception as exc:
		notes.append(f"abort2_err:{exc}")
	time.sleep(0.2)

	if restore_live:
		try:
			out = client.start_live()
			notes.append("live")
			if isinstance(out, dict) and out.get("ok") is False:
				notes.append(f"live_fail:{out.get('error', out)}")
		except Exception as exc:
			notes.append(f"live_err:{exc}")
	else:
		# SLM needs Live off before -slm; leave it off after Abort.
		if hasattr(client, "stop_live"):
			try:
				client.stop_live()
				notes.append("live_off")
			except Exception as exc:
				notes.append(f"live_off_err:{exc}")
		else:
			notes.append("live_skipped")

	return {"ok": ok, "notes": notes}


def clear_prior_stim_on_client(
	relay: Any | None,
	pl: Any | None,
	*,
	restore_live: bool = True,
) -> dict[str, Any]:
	"""Best-effort session-start clear; no-op if client has no abort()."""
	if relay is not None and hasattr(relay, "abort"):
		return abort_and_clear_stim(relay, restore_live=restore_live)
	if pl is not None and hasattr(pl, "send_command"):
		# Direct PrairieLink (no RelayClient wrapper).
		try:
			pl.send_command("-Silent", "-Abort")
			time.sleep(0.35)
			if restore_live:
				pl.send_command("-LiveScan", "on")
				return {"ok": True, "notes": ["abort", "settle_0.35s", "live"]}
			pl.send_command("-LiveScan", "off")
			return {"ok": True, "notes": ["abort", "settle_0.35s", "live_off"]}
		except Exception as exc:
			return {"ok": False, "notes": [f"pl_err:{exc}"]}
	return {"ok": True, "notes": ["skipped"]}


def abort_and_clear_at(addr: str) -> dict[str, Any]:
	"""Connect to relay HOST:PORT, run abort_clear_stim (or local fallback)."""
	from prairie_live.relay_client import RelayClient

	host, _, port = str(addr or "").partition(":")
	if not host:
		raise ValueError("relay address is empty")
	client = RelayClient(host, int(port or 25100))
	client.connect()
	try:
		# Prefer one relay round-trip so Abort/clear/Live stay ordered on the scope.
		if hasattr(client, "abort_clear_stim"):
			out = client.abort_clear_stim()
			if isinstance(out, dict) and out.get("ok") is not False:
				return out
		return abort_and_clear_stim(client)
	finally:
		client.disconnect()
