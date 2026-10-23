"""Fire Mark Points while pulsing DTR (no -NoWait).

-NoWait made DONE return before PV was waiting on Trig In 1, so later DTR
pulses never released the hologram/series — no burns.

Order that works:
  1. Finish -LoadMarkPoints / -Wait *before* calling this helper.
  2. Start blocking -MarkPoints / -slm on a worker thread.
  3. After ``arm_delay_s``, pulse DTR (Trig In 1) on the main thread.
  4. Join until PV returns DONE.

Starting the DTR countdown before load/Wait made the rising edge arrive while
PV was still loading — serial looked dead while trigger=none still burned.
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
	arm_delay_s: float = 0.25,
	row: dict[str, Any] | None = None,
	on_pulse: Callable[[int], None] | None = None,
) -> Any:
	"""
	Run blocking ``fn()`` (Mark Points / -slm) and pulse DTR after it has started.

	``arm_delay_s``: pause after starting ``fn`` so PV can enter Wait-for-Trigger
	before the first rising edge. Do not include load/Wait time here — finish
	those before calling this helper.
	"""
	n_pulses = max(int(n_pulses), 1)
	errors: list[BaseException] = []
	result: list[Any] = []

	def _run_fn() -> None:
		try:
			result.append(fn())
		except BaseException as exc:  # noqa: BLE001 — surface on join
			errors.append(exc)

	# Headroom for arm delay + all pulses + gaps + long series/SLM.
	pulse_budget_s = (
		max(float(arm_delay_s), 0.0)
		+ n_pulses * max(float(ttl_width_s), 0.0)
		+ max(n_pulses - 1, 0) * max(float(gap_s), 0.0)
		+ 5.0
	)
	join_s = max(180.0, pulse_budget_s)

	th = threading.Thread(target=_run_fn, name="mp-blocking", daemon=True)
	th.start()
	try:
		time.sleep(max(float(arm_delay_s), 0.0))
		for i in range(n_pulses):
			if errors:
				break
			t_mono = ttl.pulse_dtr(float(ttl_width_s))
			if row is not None:
				row["t_ttl"] = time.time()
				if i == 0:
					row["t_ttl_mono"] = t_mono
			if on_pulse is not None:
				on_pulse(i)
			if i < n_pulses - 1 and gap_s > 0:
				time.sleep(float(gap_s))
		# If the first edge was early (PV not armed yet), keep poking Trig In 1.
		rescues = 0
		while th.is_alive() and not errors and rescues < 15:
			time.sleep(0.35)
			if not th.is_alive() or errors:
				break
			ttl.pulse_dtr(float(ttl_width_s))
			rescues += 1
			print(f"  DTR rescue pulse {rescues} (Mark Points still waiting on Trig In 1)")
	finally:
		th.join(timeout=join_s)

	if errors:
		raise RuntimeError(f"Mark Points during DTR failed: {errors[0]}") from errors[0]
	if th.is_alive():
		raise RuntimeError("Mark Points still running after DTR pulses finished")
	return result[0] if result else None
