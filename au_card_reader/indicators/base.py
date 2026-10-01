"""Indicator interface.

An indicator turns a :class:`~au_card_reader.state.State` into physical (or
console) feedback. The GPIO implementation lands in a later phase; everything
else depends only on this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..state import State


class Indicator(ABC):
    @abstractmethod
    def show(self, state: State, auid: str | None = None, message: str = "") -> None:
        """Display the outcome for a swipe."""

    def idle(self) -> None:
        """Return to the resting state (all lights off)."""

    def close(self) -> None:
        """Release any resources held by the indicator."""
