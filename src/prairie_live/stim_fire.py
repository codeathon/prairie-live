"""Fire Mark Points while pulsing DTR (no -NoWait).

-NoWait made DONE return before PV was waiting on Trig In 1, so later DTR
pulses never released the hologram/series — no burns. Instead: start a DTR
thread, then block on -MarkPoints / -MarkAllPoints until DONE.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable


def run_while_pulsing_dtr(
	fn: Callable[[], Any],
	*,
	ttl: Any,
	n_pulses: int = 1,
	ttl_width_s: float = 0.01,
	gap_s: float = 0.0,
	arm_delay_s: float = 0.12,
	row: dict[str, Any] | None = None,
	on_pulse: Callable[[int], None] | None = None,
) -> Any:
	"""
	Call ``fn()`` (blocking PV Mark Points / -slm) while pulsing DTR.

	``arm_delay_s``: brief pause so PV can enter Wait-for-Trigger before the
	first rising edge.
	"""
	n_pulses = max(int(n_pulses), 1)
	errors: list[BaseException] = []

	def _pulses() -> None:
		try:
			time.sleep(max(float(arm_delay_s), 0.0))
			for i in range(n_pulses):
				t_mono = ttl.pulse_dtr(float(ttl_width_s))
				if row is not None:
					row["t_ttl"] = time.time()
					if i == 0:
						row["t_ttl_mono"] = t_mono
				if on_pulse is not None:
					on_pulse(i)
				if i < n_pulses - 1 and gap_s > 0:
					time.sleep(float(gap_s))
		except BaseException as exc:  # noqa: BLE001 — surface on join
			errors.append(exc)

	# Headroom for arm delay + all pulses + gaps (packed SLM can be long).
	pulse_budget_s = (
		max(float(arm_delay_s), 0.0)
		+ n_pulses * max(float(ttl_width_s), 0.0)
		+ max(n_pulses - 1, 0) * max(float(gap_s), 0.0)
		+ 5.0
	)
	join_s = max(30.0, pulse_budget_s)

	th = threading.Thread(target=_pulses, name="dtr-during-mp", daemon=True)
	th.start()
	try:
		result = fn()
	finally:
		th.join(timeout=join_s)
	if errors:
		raise RuntimeError(f"DTR during Mark Points failed: {errors[0]}") from errors[0]
	if th.is_alive():
		raise RuntimeError("DTR thread still running after Mark Points returned")
	return result
