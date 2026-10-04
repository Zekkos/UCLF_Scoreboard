"""ASCII protocol spoken by the gate MCU.

Frames (one per line, ``\\n`` terminated, ``\\r`` tolerated)::

    s           start timing
    e,10.10     run finished, 10.10 s elapsed (10 ms resolution)
    r           reset - sent by the gates *and* accepted from the scoreboard

The parser is deliberately forgiving about spacing, case and stray carriage
returns, but never guesses a team number: the gates do not report one, so the
operator assigns pending times to teams in the input window.
"""

from __future__ import annotations

from dataclasses import dataclass

from .timeutil import InvalidTime, format_time, parse_time

__all__ = [
    "Event",
    "StartEvent",
    "FinishEvent",
    "ResetEvent",
    "GarbageEvent",
    "parse_line",
    "feed",
    "encode",
    "START_FRAME",
    "RESET_FRAME",
]

START_FRAME = b"s\n"
RESET_FRAME = b"r\n"


@dataclass(frozen=True)
class Event:
    line: str

    @property
    def name(self) -> str:
        return type(self).__name__


@dataclass(frozen=True)
class StartEvent(Event):
    """``s`` - a car left the start gate."""


@dataclass(frozen=True)
class FinishEvent(Event):
    """``e,<seconds>`` - the run ended after the given elapsed time."""

    centiseconds: int

    @property
    def text(self) -> str:
        return format_time(self.centiseconds)


@dataclass(frozen=True)
class ResetEvent(Event):
    """``r`` - the gates (or the operator) reset the timing system."""


@dataclass(frozen=True)
class GarbageEvent(Event):
    """Anything we could not interpret.  Shown in the log, never applied."""

    reason: str


def parse_line(raw: str) -> Event | None:
    """Turn one line of text into an :class:`Event` (``None`` if blank)."""
    line = raw.strip()
    if not line:
        return None
    lowered = line.lower()

    if lowered == "s":
        return StartEvent(line)
    if lowered == "r":
        return ResetEvent(line)

    if lowered.startswith("e"):
        body = lowered[1:].lstrip(" \t,;:=")
        try:
            return FinishEvent(line, parse_time(body))
        except InvalidTime as exc:
            return GarbageEvent(line, str(exc))

    return GarbageEvent(line, "unknown frame (expected 's', 'r' or 'e,<time>')")


def encode(text: str) -> bytes:
    """Encode an outgoing command, guaranteeing exactly one trailing newline."""
    return f"{text.strip()}\n".encode("ascii", errors="ignore")


def feed(buffer: bytes, chunk: bytes, limit: int = 256) -> tuple[list[Event], bytes]:
    """Append ``chunk`` to ``buffer`` and return ``(events, remaining_buffer)``.

    Complete lines only; an over-long run of junk is dropped so a chatty or
    mis-bauded device cannot grow the buffer without bound.
    """
    buffer += chunk
    events: list[Event] = []
    while b"\n" in buffer:
        line, buffer = buffer.split(b"\n", 1)
        text = line.decode("utf-8", errors="replace").rstrip("\r")
        event = parse_line(text)
        if event is not None:
            events.append(event)
    if len(buffer) > limit:
        events.append(
            GarbageEvent(buffer[:limit].decode("utf-8", errors="replace"), "frame too long - discarded")
        )
        buffer = b""
    return events, buffer
