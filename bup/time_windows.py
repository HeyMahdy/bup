"""Deterministic whole-hour window extraction from operator-note text."""

from __future__ import annotations

import re
from typing import Optional

_CLOCK = re.compile(
    r"""
    (?:
        (?P<noon>noon|midnight)
        |
        (?P<hour24>\d{1,2})\s*:\s*(?P<minute24>\d{2})
        (?!\s*[ap]\.?m\.?)
        |
        (?P<hour>\d{1,2})
        (?:
            \s*:\s*(?P<minute>\d{2})
        )?
        \s*(?P<meridiem>a\.?m\.?|p\.?m\.?)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

_WINDOW = re.compile(
    r"""
    (?:from|between)\s+
    (?P<start>.+?)\s+
    (?:until|to|and)\s+
    (?P<end>.+?)
    (?=$|[.,;]|because|while|for|during|due|as\b)
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_clock_token(token: str) -> Optional[int]:
    """Parse a clock phrase into an hour-of-day integer in [0, 24]."""

    text = token.strip().lower()
    match = _CLOCK.search(text)
    if match is None:
        return None

    if match.group("noon"):
        word = match.group("noon").lower()
        return 12 if word == "noon" else 0

    if match.group("hour24") is not None:
        hour = int(match.group("hour24"))
        minute = int(match.group("minute24"))
        if minute != 0 or hour < 0 or hour > 24:
            return None
        return hour

    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    meridiem = match.group("meridiem").lower().replace(".", "")

    if hour < 1 or hour > 12 or minute != 0:
        return None

    if meridiem.startswith("p") and hour != 12:
        hour += 12
    elif meridiem.startswith("a") and hour == 12:
        hour = 0
    return hour


def hours_from_note(note: str) -> Optional[list[int]]:
    """
    Extract a start-inclusive / end-exclusive hour list from a note.

    Returns None when no unambiguous window is present.
    """

    match = _WINDOW.search(note)
    if match is None:
        return None

    start = parse_clock_token(match.group("start"))
    end = parse_clock_token(match.group("end"))
    if start is None or end is None:
        return None

    if end == 0 and "midnight" in match.group("end").lower():
        end = 24

    if not 0 <= start < end <= 24:
        return None

    return list(range(start, end))
