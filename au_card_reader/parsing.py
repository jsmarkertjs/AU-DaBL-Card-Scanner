"""Turn raw card-reader output into an AU ID.

The reader emulates a keyboard. A sample swipe looks like::

    ;00555036321?

where the embedded AUID is ``5550363``. Readers often append a carriage
return / newline and sometimes a trailing LRC character, so we strip control
characters before matching.

Two extraction strategies are supported:

1. **Regex patterns** (``parsing.patterns``) for a known, stable format. These
   are tried first.
2. **Digit-run trimming** as a fallback: take the longest run of digits and
   drop a configurable number of leading/trailing digits
   (``parsing.trim_prefix`` / ``parsing.trim_suffix``). For the sample above
   the digit run is ``00555036321``; dropping 2 leading and 2 trailing digits
   yields ``5550363``.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_DIGITS = re.compile(r"\d+")


def normalize_raw(raw: str) -> str:
    """Remove control characters and surrounding whitespace from a swipe."""
    return _CONTROL_CHARS.sub("", raw).strip()


def extract_digit_run(text: str) -> str:
    """Return the first contiguous run of digits in ``text`` (or ``""``)."""
    match = _DIGITS.search(text)
    return match.group(0) if match else ""


def trim_digit_run(digit_run: str, prefix: int, suffix: int) -> str:
    """Drop ``prefix`` leading and ``suffix`` trailing digits."""
    if not digit_run:
        return ""
    end = len(digit_run) - suffix if suffix > 0 else len(digit_run)
    if end <= prefix:
        return ""
    return digit_run[prefix:end]


def auid_candidates(auid: str, widths: Iterable[int]) -> list[str]:
    """All plausible Canvas identifiers for an AUID.

    Adds zero-padded variants (e.g. ``5550363`` -> ``05550363``) as well as the
    raw and leading-zero-stripped forms, de-duplicated and order-preserving.
    """
    candidates: list[str] = []
    if not auid:
        return candidates
    for width in widths:
        padded = auid.zfill(width)
        if padded not in candidates:
            candidates.append(padded)
    for value in (auid, auid.lstrip("0")):
        if value and value not in candidates:
            candidates.append(value)
    return candidates


class SwipeParser:
    """Parses raw swipe strings into AUIDs."""

    def __init__(
        self,
        patterns: Iterable[str] | None = None,
        trim_prefix: int = 2,
        trim_suffix: int = 2,
    ):
        self._patterns = [re.compile(p) for p in (patterns or [])]
        self._trim_prefix = trim_prefix
        self._trim_suffix = trim_suffix

    def _match_pattern(self, cleaned: str) -> Optional[str]:
        for pattern in self._patterns:
            match = pattern.search(cleaned)
            if not match:
                continue
            groups = match.groupdict()
            auid = groups.get("auid")
            if not auid and match.groups():
                auid = match.group(1)
            if auid:
                return auid.strip()
        return None

    def parse(self, raw: str) -> Optional[str]:
        """Return the AUID from a swipe, or ``None`` if nothing usable."""
        cleaned = normalize_raw(raw)
        matched = self._match_pattern(cleaned)
        if matched:
            return matched

        digit_run = extract_digit_run(cleaned)
        trimmed = trim_digit_run(digit_run, self._trim_prefix, self._trim_suffix)
        return trimmed or None
