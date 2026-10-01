"""Evaluator interface plus a deterministic mock used for development and
testing before Canvas credentials are available.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .cache import TTLCache
from .state import Evaluation, State


class Evaluator(ABC):
    @abstractmethod
    def evaluate(self, auid: str) -> Evaluation:
        """Look up an AUID and return an :class:`Evaluation`."""


class MockEvaluator(Evaluator):
    """Deterministic stand-in for Canvas.

    The sum of the AUID digits decides the outcome so a given card always
    produces the same state:

    * sum == 0            -> ERROR (simulated failure)
    * sum % 3 == 0        -> DONE
    * sum % 3 == 1        -> ENROLLED, NOT DONE
    * sum % 3 == 2        -> NOT ENROLLED
    """

    def evaluate(self, auid: str) -> Evaluation:
        total = sum(int(ch) for ch in auid if ch.isdigit())
        if total == 0:
            return Evaluation(auid, State.ERROR, "simulated API error (mock)")
        if total % 3 == 0:
            return Evaluation(
                auid, State.DONE, "graded (mock)", enrolled=True, graded=True
            )
        if total % 3 == 1:
            return Evaluation(
                auid,
                State.NOT_DONE,
                "enrolled, not graded (mock)",
                enrolled=True,
                graded=False,
            )
        return Evaluation(
            auid, State.NOT_ENROLLED, "not enrolled (mock)", enrolled=False
        )


class CachedEvaluator(Evaluator):
    """Wrap an evaluator with a TTL cache keyed by AUID."""

    def __init__(self, inner: Evaluator, ttl_seconds: float):
        self._inner = inner
        self._cache = TTLCache(ttl_seconds)

    def evaluate(self, auid: str) -> Evaluation:
        cached = self._cache.get(auid)
        if cached is not None:
            return cached
        result = self._inner.evaluate(auid)
        self._cache.set(auid, result)
        return result
