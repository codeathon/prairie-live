"""List serial / COM devices for the web UI."""

from __future__ import annotations

from prairie_live.lab_defaults import DEFAULT_SERIAL


def list_com_ports(*, default: str = DEFAULT_SERIAL) -> list[dict[str, str]]:
	"""Hardware ports from pyserial, plus the lab default if it is missing."""
	ports: list[dict[str, str]] = []
	seen: set[str] = set()
	try:
		from serial.tools import list_ports

		for info in list_ports.comports():
			dev = str(info.device)
			key = dev.upper()
			if key in seen:
				continue
			seen.add(key)
			ports.append(
				{
					"device": dev,
					"description": str(info.description or dev),
				}
			)
	except Exception:
		pass
	if default and default.upper() not in seen:
		ports.insert(
			0,
			{"device": default, "description": f"{default} (lab default)"},
		)
	return ports
