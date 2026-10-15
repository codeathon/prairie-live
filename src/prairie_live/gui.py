"""Launch the mapping web UI (replaces the tkinter form)."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> None:
	p = argparse.ArgumentParser(description="prairie-live mapping web UI")
	p.add_argument("--host", default="127.0.0.1")
	p.add_argument("--port", type=int, default=8765)
	p.add_argument(
		"--config",
		"-c",
		default=None,
		help="Ignored — lab defaults + Fat XML; experiment.json is not used",
	)
	args = p.parse_args(argv)
	from prairie_live.web_app import serve

	serve(args.host, args.port)


if __name__ == "__main__":
	main()
