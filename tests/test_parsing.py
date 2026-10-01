import unittest

from au_card_reader.config import DEFAULT_SWIPE_PATTERN
from au_card_reader.parsing import (
    SwipeParser,
    auid_candidates,
    extract_digit_run,
    normalize_raw,
    trim_digit_run,
)

# Real swipes and the AUID each represents. The 2-digit suffix varies (21, 22),
# so it is not a constant; the AUID is the middle 7 digits of the digit run.
KNOWN_SAMPLES = [
    (";00555036321?", "5550363"),
    (";00539046522?", "5390465"),
]


class ParseSwipeTests(unittest.TestCase):
    def setUp(self):
        self.parser = SwipeParser([DEFAULT_SWIPE_PATTERN])

    def test_known_samples_via_regex(self):
        parser = SwipeParser([DEFAULT_SWIPE_PATTERN])
        for raw, expected in KNOWN_SAMPLES:
            with self.subTest(raw=raw):
                self.assertEqual(parser.parse(raw), expected)

    def test_known_samples_via_digit_run_fallback(self):
        parser = SwipeParser([], trim_prefix=2, trim_suffix=2)
        for raw, expected in KNOWN_SAMPLES:
            with self.subTest(raw=raw):
                self.assertEqual(parser.parse(raw), expected)

    def test_trailing_carriage_return_and_newline(self):
        self.assertEqual(self.parser.parse(";00555036321?\r\n"), "5550363")

    def test_surrounding_whitespace(self):
        self.assertEqual(self.parser.parse("  ;00555036321?  "), "5550363")

    def test_unrecognized_returns_none(self):
        self.assertIsNone(self.parser.parse("hello world"))
        self.assertIsNone(self.parser.parse(";123?"))

    def test_multiple_patterns(self):
        parser = SwipeParser([r"^nope$", r"^;(?P<auid>\d+)\?$"])
        self.assertEqual(parser.parse(";12345?"), "12345")

    def test_positional_group_fallback(self):
        parser = SwipeParser([r"^;(?P<auid>\d{7})\d{2}\?$"])
        self.assertEqual(parser.parse(";123456789?"), "1234567")

    def test_missing_named_group_but_positional(self):
        parser = SwipeParser([r"^;(\d{4})?"])
        self.assertEqual(parser.parse(";9876?"), "9876")

    def test_empty_patterns_allowed(self):
        # No patterns -> digit-run fallback is used.
        parser = SwipeParser([])
        self.assertEqual(parser.parse(";00555036321?"), "5550363")

    def test_digit_run_fallback_ignores_prefix_suffix(self):
        parser = SwipeParser([], trim_prefix=2, trim_suffix=2)
        self.assertEqual(parser.parse("junk;00555036321?junk"), "5550363")

    def test_digit_run_fallback_custom_trim(self):
        parser = SwipeParser([], trim_prefix=0, trim_suffix=3)
        self.assertEqual(parser.parse(";00555036321?"), "00555036")

    def test_no_digits_returns_none(self):
        self.assertIsNone(SwipeParser([]).parse("no digits here"))

    def test_regex_preferred_over_fallback(self):
        parser = SwipeParser([r"^;00(?P<auid>\d{7})\d{2}\?$"])
        self.assertEqual(parser.parse(";00555036321?"), "5550363")

    def test_extract_digit_run(self):
        self.assertEqual(extract_digit_run(";00abc123?"), "00")
        self.assertEqual(extract_digit_run("no digits"), "")

    def test_trim_digit_run(self):
        self.assertEqual(trim_digit_run("00555036321", 2, 2), "5550363")
        self.assertEqual(trim_digit_run("123", 3, 0), "")
        self.assertEqual(trim_digit_run("", 2, 2), "")

    def test_auid_candidates(self):
        self.assertEqual(
            auid_candidates("5550363", [7, 8]),
            ["5550363", "05550363"],
        )
        self.assertEqual(auid_candidates("", [7]), [])

    def test_normalize_strips_controls(self):
        self.assertEqual(normalize_raw(";123?\x00\r\n"), ";123?")


if __name__ == "__main__":
    unittest.main()
