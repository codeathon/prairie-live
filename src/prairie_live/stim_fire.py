"""Fire Mark Points / -slm while pulsing DTR (no -NoWait).

-NoWait made DONE return before PV was waiting on Trig In 1, so later DTR
pulses never released the hologram/series — no burns.

Order that works (exactly the planned edges — typically one per group):
  1. Finish -LoadMarkPoints / -Wait *before* calling this helper.
  2. Start blocking -MarkPoints / -slm on a worker thread.
  3. After ``arm_delay_s``, pulse DTR (Trig In 1) on the main thread.
  4. For multi-pulse (packed SLM): sleep ``gap_s``, then next planned pulse.
  5. Optional short *arm-phase* rescue only if the first edge was early.
  6. Join until PV returns DONE with **no** further DTR.

Rescue used to fire for the whole stim dwell (thread still alive while the
hologram/series runs). That added extra Trig In 1 edges and broke
\"one TTL per group\". Rescues now stop once the arming window ends.
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
	rescue_window_s: float = 0.75,
	join_timeout_s: float | None = None,
	row: dict[str, Any] | None = None,
	on_pulse: Callable[[int], None] | None = None,
) -> Any:
	"""
	Run blocking ``fn()`` and emit exactly ``n_pulses`` planned DTR edges.

	``arm_delay_s``: pause after starting ``fn`` so PV can enter Wait-for-Trigger
	before the first rising edge. Finish load/Wait before calling this helper.

	``rescue_window_s``: after the *first* planned pulse, allow a few re-pokes
	only until ``arm_delay_s + rescue_window_s`` from thread start — for a
	missed first arm. Set 0 to disable (required for packed multi-group SLM,
	where an extra edge would release the next group early).
	"""
	n_pulses = max(int(n_pulses), 1)
	# Extra edges between packed sets would steal the next group's trigger.
	if n_pulses > 1:
		rescue_window_s = 0.0
	errors: list[BaseException] = []
	result: list[Any] = []

	def _run_fn() -> None:
		try:
			result.append(fn())
		except BaseException as exc:  # noqa: BLE001 — surface on join
			errors.append(exc)

	pulse_budget_s = (
		max(float(arm_delay_s), 0.0)
		+ n_pulses * max(float(ttl_width_s), 0.0)
		+ max(n_pulses - 1, 0) * max(float(gap_s), 0.0)
		+ max(float(rescue_window_s), 0.0)
		+ 5.0
	)
	join_s = (
		float(join_timeout_s)
		if join_timeout_s is not None
		else max(180.0, pulse_budget_s)
	)

	t0 = time.monotonic()
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
		# Arm-phase rescue only (not during hologram/series dwell).
		arm_deadline = t0 + max(float(arm_delay_s), 0.0) + max(
			float(rescue_window_s), 0.0
		)
		rescues = 0
		while (
			th.is_alive()
			and not errors
			and rescues < 4
			and time.monotonic() < arm_deadline
		):
			time.sleep(0.2)
			if not th.is_alive() or errors or time.monotonic() >= arm_deadline:
				break
			ttl.pulse_dtr(float(ttl_width_s))
			rescues += 1
			print(
				f"  DTR rescue pulse {rescues} "
				"(still waiting on Trig In 1 in arm window)"
			)
	finally:
		th.join(timeout=join_s)

	if errors:
		raise RuntimeError(f"Mark Points during DTR failed: {errors[0]}") from errors[0]
	if th.is_alive():
		# Usually E:\Markpoints "file already present" modal or missed Trig In 1.
		raise RuntimeError(
			"Mark Points still running after DTR (timeout). "
			"Dismiss any PrairieView Markpoints folder dialog, ensure "
			"MarkPointsMetadata is off, then retry."
		)
	return result[0] if result else None
