"""Command line: vesper {watch,state,copilot,logbook,dashboard} [--dir PATH]"""

from __future__ import annotations

import argparse
import json
import sys
import time

from .describe import describe, is_noise
from .watcher import Event, Watcher

def cmd_state(watcher: Watcher, args) -> int:
    """`state`: read the journals once and print the current game state as JSON."""
    if not watcher.directory.is_dir():
        print(f"Journal folder not found: {watcher.directory}", file=sys.stderr)
        return 1
    watcher.catch_up()
    print(json.dumps(watcher.snapshot(), indent=2, default=str))
    return 0


def cmd_watch(watcher: Watcher, args) -> int:
    """`watch`: print a one-line description of each event as it happens (Ctrl+C to stop).

    With --replay, today's earlier events are printed too (marked ~); with --all, noisy ones as well.
    """
    if not watcher.directory.is_dir():
        print(f"Journal folder not found: {watcher.directory}", file=sys.stderr)
        return 1

    def show(ev: Event) -> None:
        """Print one event, unless it's noise and --all wasn't given."""
        if is_noise(ev) and not args.all:
            return
        stamp = (ev.timestamp or "")[11:19] or time.strftime("%H:%M:%S")
        print(f"{stamp}  {'~' if ev.replay else ' '} {describe(ev)}", flush=True)

    watcher.on("*", show)
    caught = watcher.catch_up(dispatch=args.replay)
    s = watcher.state
    print(
        f"-- {len(caught)} earlier events read from {watcher.tail.path.name if watcher.tail.path else 'no journal'}. "
        f"CMDR {s.commander}, {s.ship.type}, {s.location.system}. Watching (Ctrl+C to stop) --",
        flush=True,
    )
    try:
        watcher.run(catch_up=False)
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point. `copilot`, `dashboard` and `logbook` have their own options and are handed over
    whole; `watch` and `state` are parsed here.
    """
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["dashboard"]:
        from .dashboard import main as dashboard_main
        return dashboard_main(argv[1:])
    if argv[:1] == ["logbook"]:
        from .logbook import main as logbook_main
        return logbook_main(argv[1:])
    if argv[:1] == ["copilot"]:  # has its own options; needs the voice extras installed
        from .copilot import main as copilot_main
        return copilot_main(argv[1:])

    parser = argparse.ArgumentParser(prog="vesper", description="Elite Dangerous journal watcher")
    parser.add_argument("--dir", help="journal folder (default: Saved Games\\Frontier Developments\\Elite Dangerous)")
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("watch", help="print events live as you play")
    w.add_argument("--all", action="store_true", help="include noisy events (music, chat, signals, status)")
    w.add_argument("--replay", action="store_true", help="also print today's earlier events (marked ~)")
    sub.add_parser("state", help="print the current game state as JSON and exit")
    sub.add_parser("copilot", help="voice copilot: callouts and push-to-talk questions (see copilot --help)")
    args = parser.parse_args(argv)

    watcher = Watcher(args.dir)
    return {"watch": cmd_watch, "state": cmd_state}[args.command](watcher, args)


if __name__ == "__main__":
    sys.exit(main())
