"""Unit tests for the :mod:`uclf.timeutil` centisecond helpers."""

from __future__ import annotations

import pytest

from uclf.timeutil import MAX_CS, InvalidTime, format_time, parse_time, try_parse_time


@pytest.mark.parametrize(
    ("text", "centiseconds"),
    [
        ("10.10", 1010),
        ("10.1", 1010),
        ("10", 1000),
        ("0.01", 1),
        (" 12.34 ", 1234),
        ("65.034", 6503),  # rounded to 10 ms
        ("65.035", 6504),  # half-up
        ("1:05.50", 6550),  # mm:ss.cc
        ("0:10.10", 1010),
        ("10.10s", 1010),
        ("10.10 sec", 1010),
        ("200", 20000),
        ("10,10", 1010),  # comma decimal separator
    ],
)
def test_parsing_ok(text, centiseconds):
    assert parse_time(text) == centiseconds


def test_parsing_numbers():
    assert parse_time(10.10) == 1010
    assert parse_time(12) == 1200


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        None,
        "abc",
        "10.10.10",
        "-5",
        "0",
        "0.00",
        "3601",  # over the 1 hour cap
        "1:2:3",
    ],
)
def test_parsing_rejects_garbage(text):
    with pytest.raises(InvalidTime):
        parse_time(text)


def test_rejects_too_large():
    with pytest.raises(InvalidTime):
        parse_time("99:00.00")


def test_blanks_and_errors_via_helper():
    assert try_parse_time("") == (None, None)
    assert try_parse_time("  ") == (None, None)
    value, error = try_parse_time("10.10")
    assert (value, error) == (1010, None)
    value, error = try_parse_time("nonsense")
    assert value is None and error


def test_formatting():
    assert format_time(1010) == "10.10"
    assert format_time(1000) == "10.00"
    assert format_time(5) == "0.05"
    assert format_time(None) == "\u2014"
    assert format_time(None, blank="") == ""


def test_round_trip_at_the_limit():
    assert parse_time(format_time(MAX_CS)) == MAX_CS
