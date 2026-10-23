"""Abort in-flight PV stim and clear leftover Mark Points state.

Stop used to only set a Python flag, so a waiting -MarkAllPoints / -MarkPoints
(Trig In 1) or a -LoadMarkPoints series could still fire from the PV GUI.
"""

from __future__ import annotations

from typing import Any

# Minimal series so -LoadMarkPoints replaces whatever our session last pushed.
EMPTY_MARK_POINTS_XML = """<?xml version="1.0" encoding="utf-8"?>
<PVMarkPointSeriesElements Iterations="1" IterationDelay="0.00" CalcFunctMap="False" AllPointsAtOnce="False" Use3D="False">
</PVMarkPointSeriesElements>
"""


def abort_and_clear_stim(client: Any) -> dict[str, Any]:
	"""
	``client``: RelayClient or anything with abort / load_mark_points / start_live.

	1. -Abort — cancel scripts, mark-point experiments, and acquisitions in flight
	2. -LoadMarkPoints empty series — drop leftover groups from the PV Mark Points UI
	3. -LiveScan — Abort also kills Live; restore FOV for the relay grabber
	"""
	notes: list[str] = []
	ok = True

	try:
		out = client.abort()
		notes.append("abort")
		if isinstance(out, dict) and out.get("ok") is False:
			ok = False
			notes.append(f"abort_fail:{out.get('error', out)}")
	except Exception as exc:
		ok = False
		notes.append(f"abort_err:{exc}")

	try:
		out = client.load_mark_points(EMPTY_MARK_POINTS_XML)
		notes.append("clear_series")
		if isinstance(out, dict) and out.get("ok") is False:
			ok = False
			notes.append(f"clear_fail:{out.get('error', out)}")
	except Exception as exc:
		ok = False
		notes.append(f"clear_err:{exc}")

	try:
		out = client.start_live()
		notes.append("live")
		if isinstance(out, dict) and out.get("ok") is False:
			# Non-fatal: user can click Live in PV.
			notes.append(f"live_fail:{out.get('error', out)}")
	except Exception as exc:
		notes.append(f"live_err:{exc}")

	return {"ok": ok, "notes": notes}


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
