"""Production input source: read the card reader via Linux evdev.

On the Pi the reader shows up as a keyboard-like event device. Reading
``/dev/input/eventX`` captures its output regardless of window focus, which is
what makes a headless setup possible.

NOTE: the key-decoding logic below is written for a US layout and has not yet
been validated against physical hardware (that happens in the evdev phase).
"""

from __future__ import annotations

import logging
import re
from typing import Iterator

from .base import InputSource

logger = logging.getLogger(__name__)


def _require_evdev():
    try:
        import evdev  # type: ignore
        from evdev import ecodes  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "python-evdev is required for the evdev input source. On the Pi "
            "run: sudo apt install python3-evdev"
        ) from exc
    return evdev, ecodes


def list_input_devices() -> list[dict]:
    """Return metadata for all readable input devices."""
    evdev, _ = _require_evdev()
    devices = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError as exc:  # pragma: no cover - permission/hardware only
            logger.debug("Skipping %s: %s", path, exc)
            continue
        devices.append({"path": dev.path, "name": dev.name, "phys": dev.phys})
        dev.close()
    return devices


def _build_keymaps(ecodes) -> tuple[dict, dict, dict]:
    """Build (normal, shifted, always-normal) keycode maps for a US layout."""
    normal = {
        ecodes.KEY_1: "1",
        ecodes.KEY_2: "2",
        ecodes.KEY_3: "3",
        ecodes.KEY_4: "4",
        ecodes.KEY_5: "5",
        ecodes.KEY_6: "6",
        ecodes.KEY_7: "7",
        ecodes.KEY_8: "8",
        ecodes.KEY_9: "9",
        ecodes.KEY_0: "0",
        ecodes.KEY_SEMICOLON: ";",
        ecodes.KEY_SLASH: "/",
        ecodes.KEY_MINUS: "-",
        ecodes.KEY_EQUAL: "=",
        ecodes.KEY_DOT: ".",
        ecodes.KEY_COMMA: ",",
        ecodes.KEY_APOSTROPHE: "'",
        ecodes.KEY_SPACE: " ",
    }
    shifted = {
        ecodes.KEY_1: "!",
        ecodes.KEY_SEMICOLON: ":",
        ecodes.KEY_SLASH: "?",
    }
    return normal, shifted, {}


class EvdevInput(InputSource):
    def __init__(self, name_regex: str):
        self._evdev, self._ecodes = _require_evdev()
        self._name_regex = re.compile(name_regex)
        self._normal, self._shifted, self._extra = _build_keymaps(self._ecodes)
        self._device = None

    def _find_device(self):
        for device in list_input_devices():
            if self._name_regex.search(device["name"] or ""):
                logger.info("Using input device: %s (%s)", device["name"], device["path"])
                return self._evdev.InputDevice(device["path"])
        raise RuntimeError(
            "No input device matched pattern "
            f"{self._name_regex.pattern!r}. Run with --list-devices to see options."
        )

    def swipes(self) -> Iterator[str]:
        if self._device is None:
            self._device = self._find_device()
        ecodes = self._ecodes
        shift = False
        buffer: list[str] = []
        for event in self._device.read_loop():
            if event.type != ecodes.EV_KEY:
                continue
            code = event.code
            if code in (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT):
                shift = event.value in (1, 2)
                continue
            if event.value != 1:  # only key presses
                continue
            if code in (ecodes.KEY_ENTER, ecodes.KEY_KPENTER):
                swipe = "".join(buffer)
                buffer.clear()
                if swipe:
                    yield swipe
                continue
            char = self._shifted.get(code) if shift else None
            if char is None:
                char = self._normal.get(code)
            if char is not None:
                buffer.append(char)

    def close(self) -> None:
        if self._device is not None:
            self._device.close()
            self._device = None
