"""Fire Mark Points / -slm while pulsing DTR (no -NoWait).

-NoWait made DONE return before PV was waiting on Trig In 1, so later DTR
pulses never released the hologram/series — no burns.

Order that works (exactly the planned edges — typically one per group):
  1. Finish -LoadMarkPoints / -Wait *before* calling this helper.
  2. Start blocking -MarkPoints / -slm on a worker thread.
  3. After ``arm_delay_s``, pulse DTR (Trig In 1) on the main thread.
  4. For multi-pulse (packed SLM): sleep ``gap_s``, then next planned pulse.
  5. Single-pulse: re-poke DTR while still armed until DONE or arm window ends
	 (mask gen can outlast the first edge).
  6. Join until PV returns DONE with **no** further DTR after the window.

Packed multi-group (n_pulses>1) disables rescues — an extra edge would release
the next hologram early.
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
	rescue_gap_s: float = 0.4,
	join_timeout_s: float | None = None,
	row: dict[str, Any] | None = None,
	on_pulse: Callable[[int], None] | None = None,
	on_stuck: Callable[[], None] | None = None,
) -> Any:
	"""
	Run blocking ``fn()`` and emit exactly ``n_pulses`` planned DTR edges.

	``arm_delay_s``: pause after starting ``fn`` so PV can enter Wait-for-Trigger
	before the first rising edge. Finish load/Wait before calling this helper.

	``rescue_window_s``: after the *first* planned pulse, re-poke DTR every
	``rescue_gap_s`` until ``arm_delay_s + rescue_window_s`` from start or until
	``fn`` returns (DONE). Covers slow SLM mask gen that misses the first edge.
	Forced to 0 when ``n_pulses > 1`` (packed multi-group).
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
		# If -slm/-mp returned before the first edge, PV never armed Trig In 1.
		# Common cause on PV 6.1: LiveScan still on (-slm does not stop Live).
		if not th.is_alive() and not errors:
			raise RuntimeError(
				"Mark Points / -slm finished before the first DTR — "
				"PV was not waiting on Trig In 1. Ensure -LiveScan off before "
				"arm (PV 6.1), and increase slm_arm_delay_s if the mask is slow."
			)
		for i in range(n_pulses):
			if errors:
				break
			if not th.is_alive():
				raise RuntimeError(
					f"Mark Points / -slm finished before DTR pulse {i + 1}/"
					f"{n_pulses} — remaining groups would miss their TTL"
				)
			t_mono = ttl.pulse_dtr(float(ttl_width_s))
			if row is not None:
				row["t_ttl"] = time.time()
				if i == 0:
					row["t_ttl_mono"] = t_mono
			if on_pulse is not None:
				on_pulse(i)
			if i < n_pulses - 1 and gap_s > 0:
				time.sleep(float(gap_s))
		# Re-poke while still armed: first edge often lands during mask gen.
		arm_deadline = t0 + max(float(arm_delay_s), 0.0) + max(
			float(rescue_window_s), 0.0
		)
		gap = max(float(rescue_gap_s), 0.1)
		rescues = 0
		max_rescues = 30 if float(rescue_window_s) > 0 else 0
		while (
			th.is_alive()
			and not errors
			and rescues < max_rescues
			and time.monotonic() < arm_deadline
		):
			time.sleep(gap)
			if not th.is_alive() or errors or time.monotonic() >= arm_deadline:
				break
			ttl.pulse_dtr(float(ttl_width_s))
			rescues += 1
			print(
				f"  DTR rescue pulse {rescues} "
				"(still waiting on Trig In 1 — mask/arm)"
			)
	finally:
		th.join(timeout=join_s)

	if errors:
		# Worker already dropped the script session / Abort'd on timeout.
		raise RuntimeError(f"Mark Points during DTR failed: {errors[0]}") from errors[0]
	if th.is_alive():
		# Worker still blocked — Abort from here so leftover -mp cannot fire
		# into a restarted PV after the operator kills the hang.
		if on_stuck is not None:
			try:
				on_stuck()
			except Exception as exc:
				print(f"  on_stuck cleanup failed: {exc}")
		raise RuntimeError(
			"Mark Points still running after DTR (timeout). "
			"Dismiss any PrairieView Markpoints folder dialog, Stop the Run "
			"(do not keep groups queued after restarting PV), then retry."
		)
	return result[0] if result else None
