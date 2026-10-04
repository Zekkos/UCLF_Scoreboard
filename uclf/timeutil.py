"""Canonical race times.

The gates report elapsed time with a resolution of 10 ms.  Every time in this
program is therefore stored as an **integer number of centiseconds** (1 cs =
10 ms).  Keeping an integral canonical unit means sorting, comparing and
round-tripping through JSON is exact - no binary floating point drift such as
``0.1 + 0.2 != 0.3``.

Displayed and entered as plain seconds with two decimals (``10.10``), which is
literally the number the firmware sends.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CS_PER_SECOND = 100
#: Anything at or above this is a typo, not a line-follower run.
MAX_CS = 60 * 60 * CS_PER_SECOND  # 1 hour
#: 0.00 s cannot be a real run and is nearly always a wiring/test artefact.
MIN_CS = 1
BLANK = "\u2014"  # em dash

__all__ = [
    "CS_PER_SECOND",
    "MAX_CS",
    "MIN_CS",
    "BLANK",
    "InvalidTime",
    "format_time",
    "parse_time",
    "try_parse_time",
    "cs_to_seconds",
    "seconds_to_cs",
]


class InvalidTime(ValueError):
    """Raised for text that cannot be read as a race time."""


_TRAILING_UNIT = re.compile(r"\s*(?:seconds?|secs?|ms|cs|s)\s*$", re.IGNORECASE)
_ALLOWED = re.compile(r"^\s*\+?\d{0,4}(?::\d{1,2})?(?:\.\d{1,4})?\s*$")


def seconds_to_cs(seconds: float) -> int:
    """Convert seconds (float) to centiseconds, rounded to 10 ms."""
    value = int((Decimal(repr(float(seconds))) * CS_PER_SECOND).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return value


def cs_to_seconds(centiseconds: int) -> float:
    """Inverse of :func:`seconds_to_cs` (only for reporting/plotting)."""
    return centiseconds / CS_PER_SECOND


def parse_time(text: str | int | float | None) -> int:
    """Parse human/firmware input into centiseconds.

    Accepts ``10.10``, ``10``, ``0:10.10`` (mm:ss.cc), an optional trailing
    unit (``s``/``sec``/``seconds``) and ``.`` or ``:`` as the minutes
    separator.  Values are rounded to the nearest 10 ms.

    Raises:
        InvalidTime: the text is empty, malformed, or outside the plausible
            range for a run.
    """
    if text is None:
        raise InvalidTime("no time entered")
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        text = repr(text)
    raw = str(text)
    cleaned = _TRAILING_UNIT.sub("", raw).strip().replace(",", ".")
    if not cleaned:
        raise InvalidTime("no time entered")
    if not _ALLOWED.match(cleaned):
        raise InvalidTime(f"'{raw.strip()}' is not a time (expected seconds, e.g. 10.10)")

    minutes = 0
    body = cleaned.strip()
    if ":" in body:
        minutes_text, body = body.split(":", 1)
        if not minutes_text.strip().isdigit():
            raise InvalidTime(f"'{raw.strip()}' has no whole minutes")
        minutes = int(minutes_text.strip())

    try:
        value = (Decimal(minutes) * 60 + Decimal(body.strip())) * CS_PER_SECOND
    except InvalidOperation as exc:  # pragma: no cover - guarded by regex
        raise InvalidTime(f"'{raw.strip()}' is not a time") from exc

    centiseconds = int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if centiseconds < MIN_CS:
        raise InvalidTime("time must be greater than 0.00 s")
    if centiseconds > MAX_CS:
        raise InvalidTime("time must be 3600.00 s or less")
    return centiseconds


def try_parse_time(text: str | None) -> tuple[int | None, str | None]:
    """``(centiseconds, error)`` helper so callers can show messages inline."""
    text = "" if text is None else str(text)
    if text.strip() == "":
        return None, None  # blank is legal: it means "no time recorded"
    try:
        return parse_time(text), None
    except InvalidTime as exc:
        return None, str(exc)


def format_time(centiseconds: int | None, blank: str = BLANK) -> str:
    """``1010 -> '10.10'``; ``None`` becomes ``blank``."""
    if centiseconds is None:
        return blank
    whole, frac = divmod(int(centiseconds), CS_PER_SECOND)
    return f"{whole}.{frac:02d}"
