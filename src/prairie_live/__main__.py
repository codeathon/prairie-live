"""CLI: python -m prairie_live view|relay|mp-sync|tseries|abort|…"""

from __future__ import annotations

import argparse
import sys


_COMMANDS = (
	"view",
	"relay",
	"mp-sync",
	"tseries",
	"abort",
	"get-state",
	"get-motor",
)


def main(argv=None) -> None:
	argv = list(sys.argv[1:] if argv is None else argv)
	# Dispatch before parent --help so `mp-sync --help` reaches the subcommand.
	if argv and argv[0] in _COMMANDS:
		command, rest = argv[0], argv[1:]
	else:
		p = argparse.ArgumentParser(prog="prairie_live")
		p.add_argument("command", choices=_COMMANDS)
		args, rest = p.parse_known_args(argv)
		command = args.command

	if command == "view":
		from prairie_live.viewer import main as view_main

		view_main(rest)
		return
	if command == "relay":
		from prairie_live.relay import main as relay_main

		relay_main(rest)
		return
	if command == "mp-sync":
		from prairie_live.sync_loop import main as sync_main

		sync_main(rest)
		return
	_one_shot(command, rest)


def _one_shot(command: str, rest: list[str]) -> None:
	p = argparse.ArgumentParser()
	p.add_argument("--host", default="127.0.0.1")
	p.add_argument("--password", default="0000")
	p.add_argument("--relay", help="host[:port] of prairie_live.relay")
	p.add_argument("--tcp", action="store_true", help="commands only, port 1236")
	p.add_argument("--key", help="GetState key, e.g. dwellTime")
	p.add_argument("--index", help="optional GetState index, e.g. XAxis")
	p.add_argument("--subindex", help="optional GetState subindex")
	p.add_argument("--axis", default="X", help="GetMotorPosition axis: X, Y, or Z")
	p.add_argument("--device", help="optional GetMotorPosition device name")
	args = p.parse_args(rest)
	client = _connect(args)
	try:
		print(_run_one_shot(command, args, client))
	finally:
		client.disconnect()


def _run_one_shot(command: str, args, client) -> dict:
	if command == "tseries":
		return client.start_tseries()
	if command == "abort":
		return client.abort()
	if command == "get-state":
		if not args.key:
			raise SystemExit("get-state requires --key")
		return client.get_state(args.key, args.index, args.subindex)
	return client.get_motor_position(args.axis, args.device)


def _connect(args):
	if args.relay:
		from prairie_live.relay_client import RelayClient

		host, _, port = args.relay.partition(":")
		c = RelayClient(host, int(port or 25100))
		c.connect()
		return c
	if args.tcp:
		from prairie_live.tcp_backend import PrairieTcp

		c = PrairieTcp(args.host, args.password)
		c.connect()
		return c
	from prairie_live.com_backend import PrairieCom

	c = PrairieCom(args.host, args.password)
	c.connect()
	return c


if __name__ == "__main__":
	main()
