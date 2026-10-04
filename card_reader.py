#!/usr/bin/env python3
"""AU card reader -> Canvas -> Adafruit USB tower light.

A student swipes; the reader acts like a keyboard and types the card data then
Enter. We pull the AU ID out of that, ask Canvas whether the configured
assignment has been graded, and set the tower light:

    yellow   idle / waiting
    green    assignment submitted and graded
    red      anything else (not graded, or student not found)
    red blink  Canvas/network error

The buzzer is never driven (it browns out the device).
"""

from __future__ import annotations

import argparse
import configparser
import logging
import os
import re
import sys
import time

import requests

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s"
)
log = logging.getLogger("card_reader")

# --- Tower light (Adafruit #5125): one raw byte per command -----------------
RED_ON, RED_OFF, RED_BLINK = 0x11, 0x21, 0x41
YELLOW_ON, YELLOW_OFF = 0x12, 0x22
GREEN_ON, GREEN_OFF = 0x14, 0x24
TOWER_VID = 0x1A86  # CH340


class TowerLight:
    """Tri-color tower over USB serial. Missing pyserial/device is non-fatal."""

    def __init__(self, port: str = "auto", baud: int = 9600):
        self._port = port
        self._baud = baud
        self._serial = None
        self._warned: set[str] = set()

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(message)

    def _open(self):
        try:
            import serial
            import serial.tools.list_ports as list_ports
        except ImportError:
            self._warn_once("no_pyserial", "pyserial not installed; tower light disabled")
            return None
        if self._port != "auto":
            return serial.Serial(self._port, self._baud, timeout=1, write_timeout=1)
        for info in list_ports.comports():
            if info.vid == TOWER_VID:
                return serial.Serial(info.device, self._baud, timeout=1, write_timeout=1)
        self._warn_once("no_device", "tower light not found (USB VID 0x%04X)" % TOWER_VID)
        return None

    def _send(self, *codes: int) -> None:
        if self._serial is None:
            self._serial = self._open()
        if self._serial is None:
            return
        try:
            self._serial.write(bytes(codes))
        except Exception as exc:  # device unplugged / brownout
            log.warning("tower write failed (%s); will retry", exc)
            self._serial = None

    def show(self, state: str) -> None:
        self._send(RED_OFF, YELLOW_OFF, GREEN_OFF)
        if state == "green":
            self._send(GREEN_ON)
        elif state == "red":
            self._send(RED_ON)
        elif state == "error":
            self._send(RED_BLINK)
        else:  # yellow / idle
            self._send(YELLOW_ON)

    def close(self) -> None:
        self._send(RED_OFF, YELLOW_OFF, GREEN_OFF)
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None


# --- Canvas ----------------------------------------------------------------
class Canvas:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0):
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"
        self._session.headers["Accept"] = "application/json"

    def _get(self, path: str, params: dict | None = None):
        resp = self._session.get(
            self._base + path, params=params, timeout=self._timeout
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def roster(self, course_id: int) -> dict[str, int]:
        """Map sis_user_id / login_id -> Canvas user id (one page = a class)."""
        users = self._get(
            f"/api/v1/courses/{course_id}/users", {"per_page": 100}
        ) or []
        mapping: dict[str, int] = {}
        for user in users:
            user_id = user.get("id")
            for key in (user.get("sis_user_id"), user.get("login_id")):
                if not key:
                    continue
                key = str(key)
                for form in {key, key.lstrip("0")}:
                    mapping.setdefault(form, user_id)
        return mapping

    def find_user(self, roster: dict[str, int], auid: str) -> int | None:
        for form in (auid, auid.zfill(8), auid.lstrip("0")):
            if form in roster:
                return roster[form]
        return None

    def is_graded(self, course_id: int, assignment_id: int, user_id: int) -> bool:
        sub = self._get(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
            f"/submissions/{user_id}"
        )
        if not sub:
            return False
        return bool(sub.get("graded_at")) or sub.get("workflow_state") == "graded"


# --- Card input ------------------------------------------------------------
def parse_auid(raw: str) -> str:
    """`;00555036321?` -> `5550363` (middle 7 digits of the digit run)."""
    digits = re.sub(r"\D", "", raw)
    return digits[2:-2] if len(digits) > 4 else ""


def read_swipes_evdev(name_regex: str):
    """Yield raw swipes from the reader's Linux evdev device (headless)."""
    import evdev
    from evdev import ecodes

    for path in evdev.list_devices():
        device = evdev.InputDevice(path)
        keys = device.capabilities().get(ecodes.EV_KEY, [])
        if ecodes.KEY_1 not in keys:
            device.close()
            continue
        if name_regex and not re.search(name_regex, device.name, re.I):
            device.close()
            continue
        log.info("Reading from input device: %s (%s)", device.name, device.path)
        break
    else:
        raise RuntimeError(
            "card reader not found; set reader.device_name or check it is plugged in"
        )

    digit_keys = {getattr(ecodes, f"KEY_{n}"): n for n in "0123456789"}
    buffer: list[str] = []
    for event in device.read_loop():
        if event.type != ecodes.EV_KEY or event.value != 1:
            continue
        if event.code in (ecodes.KEY_ENTER, ecodes.KEY_KPENTER):
            if buffer:
                yield "".join(buffer)
                buffer.clear()
        elif event.code in digit_keys:
            buffer.append(digit_keys[event.code])


# --- Config + main ---------------------------------------------------------
def load_config(path: str) -> dict:
    cfg = configparser.ConfigParser()
    if path and os.path.exists(path):
        cfg.read(path)

    def text(section: str, option: str, fallback: str = "") -> str:
        return cfg.get(section, option, fallback=fallback).strip()

    def number(section: str, option: str, fallback, cast):
        raw = text(section, option)
        return cast(raw) if raw else fallback

    token = os.environ.get("CANVAS_TOKEN") or text("canvas", "token")
    return {
        "url": text("canvas", "url", "https://au.instructure.com"),
        "token": token,
        "course_id": number("canvas", "course_id", 0, int),
        "assignment_id": number("canvas", "assignment_id", 0, int),
        "port": text("tower", "port", "auto"),
        "device_name": text("reader", "device_name"),
        "result_seconds": number("reader", "result_seconds", 6.0, float),
    }


def run(config: dict, use_stdin: bool) -> int:
    tower = TowerLight(config["port"])
    tower.show("yellow")
    canvas = Canvas(config["url"], config["token"])
    roster: dict[str, int] | None = None

    swipes = (line for line in sys.stdin) if use_stdin else read_swipes_evdev(
        config["device_name"]
    )

    try:
        for raw in swipes:
            auid = parse_auid(raw)
            if not auid:
                continue
            try:
                if roster is None:
                    roster = canvas.roster(config["course_id"])
                user_id = canvas.find_user(roster, auid)
                if user_id is None:
                    tower.show("red")
                    log.info("AUID %s -> not found -> RED", auid)
                elif canvas.is_graded(
                    config["course_id"], config["assignment_id"], user_id
                ):
                    tower.show("green")
                    log.info("AUID %s -> graded -> GREEN", auid)
                else:
                    tower.show("red")
                    log.info("AUID %s -> not graded -> RED", auid)
            except requests.RequestException as exc:
                tower.show("error")
                log.error("Canvas error: %s", exc)
            time.sleep(config["result_seconds"])
            tower.show("yellow")
    except KeyboardInterrupt:
        log.info("Stopping")
    finally:
        tower.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AU card reader -> Canvas tower light")
    parser.add_argument("--config", default="config.ini")
    parser.add_argument("--stdin", action="store_true", help="read swipes from stdin (dev)")
    parser.add_argument("--test-lamp", action="store_true", help="cycle the tower colors")
    args = parser.parse_args(argv)

    config = load_config(args.config)

    if args.test_lamp:
        tower = TowerLight(config["port"])
        for state, wait in (("yellow", 1), ("green", 2), ("red", 2),
                            ("error", 2), ("yellow", 1)):
            tower.show(state)
            time.sleep(wait)
        tower.close()
        return 0

    return run(config, args.stdin)


if __name__ == "__main__":
    raise SystemExit(main())
