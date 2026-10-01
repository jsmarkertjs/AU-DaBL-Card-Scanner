"""Development input source: read swipe strings from stdin.

The card reader emulates a keyboard, so typing a raw swipe string here (or
piping a file of them) exercises the full pipeline without the Pi.
"""

from __future__ import annotations

import sys
from typing import Iterator, TextIO

from .base import InputSource


class StdinInput(InputSource):
    def __init__(self, stream: TextIO | None = None):
        self._stream = stream if stream is not None else sys.stdin

    def swipes(self) -> Iterator[str]:
        for line in self._stream:
            line = line.rstrip("\n")
            if line:
                yield line
