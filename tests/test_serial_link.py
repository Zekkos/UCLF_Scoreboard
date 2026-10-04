"""Tests for the serial worker: framing, hot-unplug recovery and re-connection.

A real MCU is replaced by a pty pair (Linux/macOS), which behaves like a USB
serial device from the scoreboard's point of view: closing the far end makes
reads fail, exactly as unplugging the cable does.
"""

from __future__ import annotations

import os
import pty
import time
from pathlib import Path

import pytest

from helpers import pump, signal_args

from uclf.links import SerialLink, available_ports, port_present
from uclf.protocol import FinishEvent, ResetEvent, StartEvent

needs_pty = pytest.mark.skipif(not hasattr(os, "openpty"), reason="pty is POSIX only")


class Gates:
    """A virtual serial device: ``slave`` is what the scoreboard opens."""

    def __init__(self) -> None:
        self.master, self.slave = pty.openpty()
        self.slave_name = os.ttyname(self.slave)

    def send(self, text: str) -> None:
        os.write(self.master, text.encode("ascii"))

    def pull_out(self) -> None:
        """Close the far end: the device vanishes."""
        os.close(self.master)
        self.master = -1

    def close(self) -> None:
        for fd in (self.master, self.slave):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass


@pytest.fixture()
def gates():
    device = Gates()
    yield device
    device.close()


@needs_pty
def test_frames_arrive_as_events(gates, qapp):
    link = SerialLink(port=gates.slave_name, baudrate=115200)
    received: list = []
    link.event.connect(lambda event: received.append(event))
    try:
        assert wait_until(lambda: link.connected, 4.0), "pty never opened"
        frames = [(StartEvent, "s"), (FinishEvent, "e,10.10"), (ResetEvent, "r")]
        for _kind, text in frames:
            gates.send(text + "\n")
            wait_until(lambda: len(received) >= len(frames), 3.0)
        kinds = [type(event) for event in received]
        assert kinds[:3] == [StartEvent, FinishEvent, ResetEvent], kinds
        assert [event.line for event in received[:3]] == ["s", "e,10.10", "r"]
        assert received[1].centiseconds == 1010
    finally:
        link.stop()


@needs_pty
def test_partial_frame_is_buffered_until_the_newline(gates, qapp):
    link = SerialLink(port=gates.slave_name)
    try:
        assert wait_until(lambda: link.connected, 4.0)
        received: list = []
        link.event.connect(received.append)
        gates.send("e,1")
        pump(250)
        assert received == []  # no newline yet, so no frame
        gates.send("2.34\n")
        assert wait_until(lambda: len(received) == 1, 3.0), "the completed frame never arrived"
        event = received[0]
        assert isinstance(event, FinishEvent) and event.centiseconds == 1234
    finally:
        link.stop()


@needs_pty
def test_reset_is_transmitted_and_logged(gates, qapp):
    link = SerialLink(port=gates.slave_name)
    try:
        assert wait_until(lambda: link.connected, 4.0)
        assert link.reset_gates() is True
        assert signal_args(link.tx_line, 1000) == ("r",)
        assert os.read(gates.master, 16) == b"r\n"
    finally:
        link.stop()


@needs_pty
def test_unplug_then_replug_recovers_without_restarting_the_app(gates, qapp):
    link = SerialLink(port=gates.slave_name, baudrate=115200)
    try:
        assert wait_until(lambda: link.connected, 4.0), "link never came up"
        gates.pull_out()
        assert wait_until(lambda: not link.connected, 6.0), "unplug not noticed"

        replacement = Gates()
        try:
            link.configure(port=replacement.slave_name)
            assert wait_until(lambda: link.connected, 6.0), "did not reopen the new device"
            received: list = []
            link.event.connect(received.append)
            replacement.send("s\n")
            assert wait_until(lambda: any(isinstance(e, StartEvent) for e in received), 4.0), \
                "the reconnected device never delivered a frame"
        finally:
            replacement.close()
    finally:
        link.stop()


@needs_pty
def test_opening_a_port_that_is_not_there_retries_quietly(qapp, tmp_path):
    missing = tmp_path / "definitely-not-a-port"
    link = SerialLink(port=str(missing))
    try:
        pump(700)
        assert link.connected is False
        assert "not-a-port" in link.last_status, "the UI was never told why there is no link"
        assert link.alive, "worker thread died on a missing port"
    finally:
        link.stop()


@needs_pty
def test_commands_are_dropped_while_offline(qapp):
    link = SerialLink(port=None)
    try:
        pump(200)
        assert link.send("r") is False
    finally:
        link.stop()


def test_port_helpers(qapp):
    assert isinstance(available_ports(), list)
    assert port_present("") is False
    assert port_present("/definitely/not/here") is False
    assert port_present("/dev/null") is True or not Path("/dev/null").exists()


# ------------------------------------------------------------------- utilities


def wait_until(predicate, seconds: float, poll_s: float = 0.05) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        pump(20)
        if predicate():
            return True
    return predicate()


def wait_for_event(link, kind, timeout_ms: int = 2000):
    """Drain the queue until an event of ``kind`` shows up."""
    seen: list = []
    link.event.connect(seen.append)
    deadline = time.monotonic() + timeout_ms / 1000
    event = None
    while time.monotonic() < deadline:
        pump(20)
        matches = [item for item in seen if isinstance(item, kind)]
        if matches:
            event = matches[-1]
            break
    link.event.disconnect(seen.append)
    return event


@needs_pty
def test_choosing_another_port_while_connected_follows_it(gates, qapp):
    """The operator repoints the desk at a new device: the link moves without a restart."""
    link = SerialLink(port=gates.slave_name)
    try:
        assert wait_until(lambda: link.connected, 4.0)
        other = Gates()
        try:
            events: list = []
            flags: list[bool] = []
            link.event.connect(events.append)
            link.connected_changed.connect(flags.append)
            link.configure(port=other.slave_name)
            # hang up on the old device first, then come back connected to the new one
            assert wait_until(lambda: False in flags and True in flags and link.connected, 6.0), "did not move"
            assert link.describe().startswith(other.slave_name)
            other.send("s\n")
            assert wait_until(lambda: any(isinstance(e, StartEvent) for e in events), 3.0)
            gates.send("r\n")
            pump(250)
            assert not any(isinstance(e, ResetEvent) for e in events), "still reading the old port"
        finally:
            other.close()
    finally:
        link.stop()
