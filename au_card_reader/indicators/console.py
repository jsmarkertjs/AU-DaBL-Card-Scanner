"""Console/log indicator used for development and for the pre-lights Pi."""

from __future__ import annotations

from datetime import datetime

from ..state import LABELS, State
from .base import Indicator


class ConsoleIndicator(Indicator):
    def show(self, state: State, auid: str | None = None, message: str = "") -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        who = auid if auid else "-"
        line = f"[{timestamp}] {who:>12} -> {LABELS.get(state, state.value)}"
        if message:
            line += f"  ({message})"
        print(line, flush=True)
