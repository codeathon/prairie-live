"""Launch the mapping web UI (replaces the tkinter form)."""

from __future__ import annotations

import argparse
import os


def main(argv: list[str] | None = None) -> None:
	p = argparse.ArgumentParser(description="prairie-live mapping web UI")
	p.add_argument("--host", default="127.0.0.1")
	p.add_argument("--port", type=int, default=8765)
	p.add_argument("--relay", help="scope relay host:port, e.g. 10.33.107.221:25100")
	p.add_argument(
		"--config",
		"-c",
		default=None,
		help="Ignored — lab defaults + Fat XML; experiment.json is not used",
	)
	p.add_argument(
		"--galvo-mode",
		choices=("slm", "galvo"),
		default=None,
		help="Mark Points calibration: slm = Use SLM ticked (default), galvo = off",
	)
	args = p.parse_args(argv)
	from prairie_live.galvo_calibration import activate

	print("galvo→image:", activate(args.galvo_mode), flush=True)
	if args.relay:
		addr = args.relay if ":" in args.relay else f"{args.relay}:25100"
		os.environ["PRAIRIE_LIVE_RELAY"] = addr
	from prairie_live.web_app import serve

	serve(args.host, args.port)


if __name__ == "__main__":
	main()
