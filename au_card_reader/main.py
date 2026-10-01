"""Entry point: wire input -> parser -> evaluator -> indicator."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

from . import __version__
from .config import Config, load_config
from .evaluator import CachedEvaluator, Evaluator, MockEvaluator
from .indicators.base import Indicator
from .indicators.console import ConsoleIndicator
from .inputs.base import InputSource
from .inputs.stdin_input import StdinInput
from .parsing import SwipeParser
from .state import Evaluation, LABELS, State

logger = logging.getLogger("au_card_reader")


class Debouncer:
    """Suppress repeated reads of the same card within a time window."""

    def __init__(self, window_seconds: float):
        self._window = window_seconds
        self._last: dict[str, float] = {}

    def is_duplicate(self, auid: str) -> bool:
        if self._window <= 0:
            return False
        now = time.monotonic()
        last = self._last.get(auid)
        self._last[auid] = now
        return last is not None and (now - last) < self._window


class Presenter:
    """Show a result and schedule a return to idle."""

    def __init__(self, indicator: Indicator, clear_after_seconds: float):
        self._indicator = indicator
        self._clear_after = clear_after_seconds
        self._timer: threading.Timer | None = None

    def show(self, evaluation: Evaluation) -> None:
        self._indicator.show(evaluation.state, evaluation.auid, evaluation.message)
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._clear_after > 0:
            self._timer = threading.Timer(self._clear_after, self._indicator.idle)
            self._timer.daemon = True
            self._timer.start()

    def close(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


def build_indicator(config: Config) -> Indicator:
    driver = config.indicator.driver.lower()
    if driver == "console":
        return ConsoleIndicator()
    if driver == "gpio":
        from .indicators.gpio import GpioIndicator

        gpio = config.indicator.gpio
        return GpioIndicator(gpio.red, gpio.yellow, gpio.green, gpio.active_high)
    raise ValueError(f"Unknown indicator driver: {config.indicator.driver!r}")


def build_input(config: Config) -> InputSource:
    source = config.input.source.lower()
    if source == "stdin":
        return StdinInput()
    if source == "evdev":
        from .inputs.evdev_input import EvdevInput

        return EvdevInput(config.input.device_name_regex)
    raise ValueError(f"Unknown input source: {config.input.source!r}")


def build_evaluator(config: Config, mock: bool) -> Evaluator:
    if mock:
        evaluator: Evaluator = MockEvaluator()
    else:
        from .canvas_client import CanvasEvaluator

        evaluator = CanvasEvaluator(config)
    if config.behavior.cache_ttl_seconds > 0:
        evaluator = CachedEvaluator(evaluator, config.behavior.cache_ttl_seconds)
    return evaluator


def _apply_overrides(config: Config, args: argparse.Namespace) -> None:
    if args.source:
        config.input.source = args.source
    if args.indicator:
        config.indicator.driver = args.indicator


def _cmd_list_devices() -> int:
    try:
        from .inputs.evdev_input import list_input_devices

        devices = list_input_devices()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not devices:
        print("No input devices found.")
        return 0
    for device in devices:
        print(f"{device['path']}\t{device['name']}\t{device['phys']}")
    return 0


def _cmd_discover(config: Config, auid: str) -> int:
    from .canvas_client import CanvasClient, CanvasError
    from .mapping import UserResolver

    client = CanvasClient(
        config.canvas.base_url,
        config.canvas.token,
        config.canvas.request_timeout_seconds,
    )
    resolver = UserResolver(
        client,
        mode=config.mapping.mode,
        csv_path=config.mapping.csv_path,
        candidate_widths=config.parsing.auid_candidate_widths,
        course_id=config.canvas.course_id,
        roster_cache_ttl=config.mapping.roster_cache_ttl_seconds,
    )
    print(f"AUID: {auid}")
    print(f"Candidates: {', '.join(resolver.candidates(auid))}")
    try:
        user_id, method = resolver.resolve(auid)
        print(f"Resolved: id={user_id} via {method}")
        if user_id is None:
            print("  -> no match; check mapping.mode or the roster SIS ids")
    except CanvasError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_dump_roster(config: Config) -> int:
    from .canvas_client import CanvasClient, CanvasError

    client = CanvasClient(
        config.canvas.base_url,
        config.canvas.token,
        config.canvas.request_timeout_seconds,
    )
    try:
        users = client.course_users(config.canvas.course_id)
    except CanvasError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Course {config.canvas.course_id}: {len(users)} users")
    for user in users:
        print(
            f"  id={user.get('id')}\tsis={user.get('sis_user_id')}\t"
            f"login={user.get('login_id')}\tname={user.get('name')!r}"
        )
    return 0


def _build_parser(config: Config) -> SwipeParser:
    return SwipeParser(
        config.parsing.patterns,
        trim_prefix=config.parsing.trim_prefix,
        trim_suffix=config.parsing.trim_suffix,
    )


def run(config: Config, args: argparse.Namespace) -> int:
    parser = _build_parser(config)
    indicator = build_indicator(config)
    evaluator = build_evaluator(config, args.mock)
    source = build_input(config)
    debouncer = Debouncer(config.behavior.debounce_seconds)
    presenter = Presenter(indicator, config.behavior.clear_after_seconds)

    logger.info(
        "Started (source=%s, indicator=%s, evaluator=%s)",
        config.input.source,
        config.indicator.driver,
        "mock" if args.mock else "canvas",
    )

    try:
        for raw in source.swipes():
            auid = parser.parse(raw)
            if auid is None:
                logger.warning("Unrecognized swipe ignored: %r", raw)
                continue
            if debouncer.is_duplicate(auid):
                logger.info("Duplicate swipe for %s ignored", auid)
                continue
            try:
                evaluation = evaluator.evaluate(auid)
            except Exception as exc:  # pragma: no cover - depends on network
                logger.exception("Lookup failed for %s", auid)
                evaluation = Evaluation(auid, State.ERROR, str(exc))
            logger.info(
                "AUID %s -> %s (%s)",
                evaluation.auid,
                LABELS.get(evaluation.state, evaluation.state.value),
                evaluation.message or "no detail",
            )
            presenter.show(evaluation)
            if args.once:
                break
    except KeyboardInterrupt:
        logger.info("Interrupted, shutting down")
    finally:
        presenter.close()
        indicator.close()
        source.close()
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="au-card-reader",
        description="Read AU IDs from a card reader and check Canvas.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", default="config.yaml",
                        help="Path to YAML config file (default: config.yaml)")
    parser.add_argument("--source", choices=["stdin", "evdev"],
                        help="Override input source")
    parser.add_argument("--indicator", choices=["console", "gpio"],
                        help="Override indicator driver")
    parser.add_argument("--mock", action="store_true",
                        help="Use the mock evaluator instead of Canvas")
    parser.add_argument("--once", action="store_true",
                        help="Process a single swipe then exit")
    parser.add_argument("--list-devices", action="store_true",
                        help="List input devices and exit")
    parser.add_argument("--discover", metavar="AUID",
                        help="Show how Canvas resolves an AUID, then exit")
    parser.add_argument("--dump-roster", action="store_true",
                        help="List the course roster (ids/sis/login) and exit")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.list_devices:
        return _cmd_list_devices()

    try:
        config = load_config(args.config)
    except FileNotFoundError:
        # Fall back to defaults so --mock / stdin development works with no file.
        logger.warning("Config %s not found; using defaults", args.config)
        config = load_config(None)

    _apply_overrides(config, args)

    if args.dump_roster:
        return _cmd_dump_roster(config)

    if args.discover:
        return _cmd_discover(config, args.discover)

    return run(config, args)


if __name__ == "__main__":
    raise SystemExit(main())
