import unittest

from au_card_reader.cache import TTLCache
from au_card_reader.evaluator import CachedEvaluator, Evaluator, MockEvaluator
from au_card_reader.main import Debouncer
from au_card_reader.state import Evaluation, State


class MockEvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.evaluator = MockEvaluator()

    def test_done(self):
        # 5+5+5+0+3+6+3 = 27, 27 % 3 == 0
        self.assertEqual(self.evaluator.evaluate("5550363").state, State.DONE)

    def test_deterministic(self):
        first = self.evaluator.evaluate("1234567")
        second = self.evaluator.evaluate("1234567")
        self.assertEqual(first.state, second.state)

    def test_error_for_all_zero(self):
        self.assertEqual(self.evaluator.evaluate("0000000").state, State.ERROR)

    def test_not_done(self):
        # 1+2+3+4+5+6+7 = 28, 28 % 3 == 1
        self.assertEqual(self.evaluator.evaluate("1234567").state, State.NOT_DONE)

    def test_not_enrolled(self):
        # 1+0+0+0+0+0+1 = 2, 2 % 3 == 2
        self.assertEqual(self.evaluator.evaluate("1000001").state, State.NOT_ENROLLED)


class _CountingEvaluator(Evaluator):
    def __init__(self):
        self.calls = 0

    def evaluate(self, auid: str) -> Evaluation:
        self.calls += 1
        return Evaluation(auid, State.DONE)


class CachedEvaluatorTests(unittest.TestCase):
    def test_caches_results(self):
        inner = _CountingEvaluator()
        cached = CachedEvaluator(inner, ttl_seconds=60)
        cached.evaluate("5550363")
        cached.evaluate("5550363")
        self.assertEqual(inner.calls, 1)


class TTLCacheTests(unittest.TestCase):
    def test_expiry(self):
        cache = TTLCache(ttl_seconds=0)
        cache.set("k", "v")
        self.assertIsNone(cache.get("k"))

    def test_hit(self):
        cache = TTLCache(ttl_seconds=60)
        cache.set("k", "v")
        self.assertEqual(cache.get("k"), "v")


class DebouncerTests(unittest.TestCase):
    def test_duplicate_within_window(self):
        debouncer = Debouncer(window_seconds=60)
        self.assertFalse(debouncer.is_duplicate("5550363"))
        self.assertTrue(debouncer.is_duplicate("5550363"))

    def test_disabled_window(self):
        debouncer = Debouncer(window_seconds=0)
        self.assertFalse(debouncer.is_duplicate("5550363"))
        self.assertFalse(debouncer.is_duplicate("5550363"))

    def test_different_cards_not_duplicates(self):
        debouncer = Debouncer(window_seconds=60)
        self.assertFalse(debouncer.is_duplicate("5550363"))
        self.assertFalse(debouncer.is_duplicate("1234567"))


if __name__ == "__main__":
    unittest.main()
