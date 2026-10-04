#!/usr/bin/env python3
"""Fake line-follower gates on a virtual serial port (no hardware needed).

Opens a pty, prints a device path you can point the scoreboard at, and lets you
send protocol frames by hand::

    python tools/mock_gates.py                 # interactive, device /tmp/UCLF-GATES
    python tools/mock_gates.py --name /tmp/gates --gap 3
    python tools/mock_gates.py --auto 5        # five random runs then exit

    python -m uclf --port /tmp/UCLF-GATES      # the scoreboard, other terminal

Interactive commands: ``s`` (start), ``e 10.10`` (finish, random if omitted),
``r`` (reset at the gates), ``auto N`` (N random runs), ``q`` (quit).

POSIX only (``pty``).  To test with real hardware, wire the MCU to the machine
and pick its port in the input window instead.
"""

from __future__ import annotations

import argparse
import os
import pty
import random
import time
from pathlib import Path

HELP = """commands:  s = start | e [SECONDS] = finish | r = reset | auto [N] | q = quit"""


class VirtualGates:
    """The 'MCU' end of a pty pair; the scoreboard opens ``device``."""

    def __init__(self, link: Path) -> None:
        self.master, self.slave = pty.openpty()
        self.device = os.ttyname(self.slave)
        self.link = link
        try:
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(self.device)
        except OSError as exc:
            print(f"warning: could not create {link} ({exc}); use {self.device} directly")

    def send(self, frame: str) -> None:
        os.write(self.master, frame.encode("ascii"))
        print(f"  tx  {frame!r}", flush=True)

    def close(self) -> None:
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            if self.link.is_symlink():
                self.link.unlink()
        except OSError:
            pass


def random_run(gates: VirtualGates, gap: float, start_first: bool = True) -> None:
    if start_first:
        gates.send("s")
    time.sleep(max(0.05, gap * random.uniform(0.3, 0.8)))
    gates.send(f"e,{random.uniform(8.0, 14.0):.2f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="simulate the gate MCU over a virtual serial port")
    parser.add_argument("--name", default="/tmp/UCLF-GATES", help="symlink to the fake device")
    parser.add_argument("--auto", type=int, default=0, metavar="N", help="send N random runs and exit")
    parser.add_argument("--gap", type=float, default=2.0, help="seconds between auto runs (default 2)")
    args = parser.parse_args()

    gates = VirtualGates(Path(args.name))
    print(f"gates device: {gates.link} -> {gates.device}")
    print(HELP)

    try:
        if args.auto:
            for _ in range(args.auto):
                random_run(gates, args.gap)
                time.sleep(max(0.05, args.gap * 0.2))
            return 0
        while True:
            try:
                line = input("gates> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            command, *rest = line.split()
            command = command.lower()
            if command in ("q", "quit", "exit"):
                break
            elif command == "s":
                gates.send("s")
            elif command == "r":
                gates.send("r")
            elif command == "e":
                seconds = rest[0] if rest else f"{random.uniform(8.0, 14.0):.2f}"
                gates.send(f"e,{seconds}")
            elif command == "auto":
                for _ in range(int(rest[0]) if rest else 3):
                    random_run(gates, args.gap)
                    time.sleep(max(0.05, args.gap * 0.2))
            elif command in ("?", "h", "help"):
                print(HELP)
            else:
                print("unknown command -", HELP)
    finally:
        gates.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
