"""GPIO indicator (red / yellow / green LEDs).

Deferred to the lights phase; included now so the wiring can be dropped in
without touching anything else. Uses ``gpiozero`` (preinstalled on Raspberry
Pi OS). Pin numbers are BCM.

Not yet validated on hardware.
"""

from __future__ import annotations

import logging

from ..state import State
from .base import Indicator

logger = logging.getLogger(__name__)


class GpioIndicator(Indicator):
    def __init__(
        self,
        red: int,
        yellow: int,
        green: int,
        active_high: bool = True,
    ):
        try:
            from gpiozero import LED
        except ImportError as exc:  # pragma: no cover - hardware only
            raise RuntimeError(
                "gpiozero is required for the GPIO indicator. On the Pi run: "
                "sudo apt install python3-gpiozero"
            ) from exc

        self._leds = {
            "red": LED(red, active_high=active_high),
            "yellow": LED(yellow, active_high=active_high),
            "green": LED(green, active_high=active_high),
        }
        self.idle()

    def _all_off(self) -> None:
        for led in self._leds.values():
            led.off()

    def show(self, state: State, auid: str | None = None, message: str = "") -> None:
        self._all_off()
        if state == State.NOT_ENROLLED:
            self._leds["red"].on()
        elif state == State.NOT_DONE:
            self._leds["yellow"].on()
        elif state == State.DONE:
            self._leds["green"].on()
        elif state == State.ERROR:
            self._leds["red"].blink(on_time=0.2, off_time=0.2)

    def idle(self) -> None:
        self._all_off()

    def close(self) -> None:
        for led in self._leds.values():
            led.off()
            led.close()
