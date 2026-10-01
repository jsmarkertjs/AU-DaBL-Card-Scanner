"""Input source interface: something that yields raw swipe strings."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator


class InputSource(ABC):
    @abstractmethod
    def swipes(self) -> Iterator[str]:
        """Yield raw swipe strings as they arrive (blocking)."""

    def close(self) -> None:
        """Release the input source."""
