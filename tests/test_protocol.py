"""Unit tests for the gate ASCII protocol parser."""

from __future__ import annotations

import pytest

from uclf.protocol import (
    RESET_FRAME,
    START_FRAME,
    encode,
    feed,
    FinishEvent,
    GarbageEvent,
    parse_line,
    ResetEvent,
    StartEvent,
)


def test_start_frame():
    event = parse_line("s\n")
    assert isinstance(event, StartEvent)
    assert event.line == "s"
    assert isinstance(parse_line("S"), StartEvent)
    assert isinstance(parse_line("  s  "), StartEvent)


def test_reset_frame():
    assert isinstance(parse_line("r"), ResetEvent)
    assert isinstance(parse_line("R\n"), ResetEvent)


@pytest.mark.parametrize(
    ("line", "centiseconds"),
    [
        ("e,10.10", 1010),
        ("E,10.10", 1010),
        ("e, 10.10", 1010),
        ("e:10.10", 1010),
        ("e=10.10", 1010),
        ("e;10.10", 1010),
        ("e,10.10\r", 1010),
        ("e,200.00", 20000),
        ("e,0.05", 5),
        ("e,65.034", 6503),  # rounded down to 10 ms
    ],
)
def test_finish_frames(line, centiseconds):
    event = parse_line(line)
    assert isinstance(event, FinishEvent)
    assert event.centiseconds == centiseconds
    assert event.text == format(centiseconds / 100, ".2f")


def test_blank_lines_are_ignored():
    assert parse_line("") is None
    assert parse_line("   ") is None
    assert parse_line("\r\n") is None


@pytest.mark.parametrize("line", ["hello", "e,", "e,abc", "e,0", "s s", "3.4.5", "10.10"])
def test_junk_becomes_a_garbage_event_with_a_reason(line):
    event = parse_line(line)
    assert isinstance(event, GarbageEvent)
    assert event.reason


def test_encoding_appends_exactly_one_newline():
    assert encode("r") == b"r\n"
    assert encode("r\n") == b"r\n"
    assert encode("  s  ") == b"s\n"
    assert RESET_FRAME == b"r\n"
    assert START_FRAME == b"s\n"


def test_feed_splits_partial_frames():
    events, buffer = feed(b"", b"e,1")
    assert events == [] and buffer == b"e,1"
    events, buffer = feed(buffer, b"0.10\ns\n")
    assert [type(e) for e in events] == [FinishEvent, StartEvent]
    assert events[0].centiseconds == 1010
    assert buffer == b""


def test_feed_handles_many_frames_in_one_chunk():
    events, buffer = feed(b"", b"s\ne,9.99\nr\n\n")
    assert [type(e) for e in events] == [StartEvent, FinishEvent, ResetEvent]
    assert buffer == b""


def test_feed_drops_a_run_of_junk_instead_of_growing_forever():
    events, buffer = feed(b"", b"x" * 400)
    assert buffer == b""
    assert len(events) == 1
    assert isinstance(events[0], GarbageEvent)
    assert "too long" in events[0].reason


def test_feed_keeps_unterminated_tail_after_junk_line():
    events, buffer = feed(b"", b"y" * 300 + b"\ns\n")
    assert isinstance(events[0], GarbageEvent)
    assert isinstance(events[1], StartEvent)
    assert buffer == b""


def test_undecodable_bytes_never_become_a_result():
    events, buffer = feed(b"", b"e,\xff10.10\n")  # byte the firmware should not send
    assert len(events) == 1
    assert isinstance(events[0], GarbageEvent)
    assert buffer == b""
