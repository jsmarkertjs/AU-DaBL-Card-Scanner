#!/usr/bin/env python3
"""AU card reader -> Canvas -> Adafruit USB tower light.

A student swipes; the reader acts as a keyboard and types the card data then
Enter. We look the student up in Canvas and check two things — the Safety
Waiver (graded) and the Safety Quiz (score >= passing) — then set the light:

    solid green              fully cleared (waiver + quiz)
    alternating green/yellow waiver done, quiz missing
    alternating yellow/red   quiz passed, waiver missing
    solid red               neither
    blinking red            not in the course (also Canvas/network error)

The buzzer is never driven (it browns out the device).
"""

from __future__ import annotations

import argparse
import colorsys
import configparser
import logging
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

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
        if state == "idle":
            return  # tower lamp is off while idle
        if state == "green":
            self._send(GREEN_ON)
        elif state == "red":
            self._send(RED_ON)
        elif state in ("blink_red", "error"):
            self._send(RED_BLINK)
        else:  # yellow
            self._send(YELLOW_ON)

    def present(self, pattern: tuple, seconds: float) -> None:
        """Show a pattern for a duration (see module-level _present())."""
        _present(self.show, pattern, seconds)

    def close(self) -> None:
        self._send(RED_OFF, YELLOW_OFF, GREEN_OFF)
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None


def _present(show, pattern: tuple, seconds: float) -> None:
    """Drive a show() callback through one pattern for a duration.

    pattern is one of ("solid", color), ("blink", color),
    or ("alt", color_a, color_b). Colors: "green", "yellow", "red".
    """
    kind = pattern[0]
    if kind == "alt":
        first, second = pattern[1], pattern[2]
        end = time.monotonic() + seconds
        i = 0
        while True:
            show(first if i % 2 == 0 else second)
            i += 1
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.6, remaining))
    else:
        color = pattern[1]
        show(color if kind == "solid" else "blink_" + color)
        time.sleep(seconds)


class TowerSet:
    """Fan show()/present()/close() out to several towers at once.

    Lets the USB tower and the NeoPixel tower show the same state together.
    """

    def __init__(self, *towers):
        self._towers = [tower for tower in towers if tower is not None]

    def show(self, state: str) -> None:
        for tower in self._towers:
            tower.show(state)

    def present(self, pattern: tuple, seconds: float) -> None:
        _present(self.show, pattern, seconds)

    def close(self) -> None:
        for tower in self._towers:
            tower.close()


# --- NeoPixel tower (4 x 9 = 36 WS2812 LEDs, driven from the Pi) ------------
GREEN_RGB = (0, 255, 0)
YELLOW_RGB = (255, 255, 0)
RED_RGB = (255, 0, 0)
OFF_RGB = (0, 0, 0)
NEOTOWER_FPS = 50


def _hsv_rgb(hue: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(hue % 1.0, 1.0, 1.0)
    return (int(r * 255), int(g * 255), int(b * 255))


class NeoTower:
    """NeoPixel tower mirroring TowerLight's states, plus a rainbow idle.

    Driven directly from the Pi with rpi_ws281x (so the service runs as root).
    The strip is refreshed on a background thread at ~50 Hz; a missing library
    or un-wired strip is non-fatal (warn once, keep running).
    """

    def __init__(self, enabled: bool = True, pin: str = "D18", count: int = 36,
                 leds_per_strip: int = 9, brightness: float = 80.0,
                 order: str = "GRB", idle_seconds: float = 8.0):
        self._enabled = enabled
        self._pin_name = pin
        self._count = count
        self._brightness = brightness
        self._order = order
        self._idle_seconds = max(0.5, idle_seconds)
        self._green, self._yellow, self._red = self._sections(count, leds_per_strip)
        self._state = "idle"
        self._lock = threading.Lock()
        self._pixels = None
        self._failed = False
        self._warned: set[str] = set()
        self._thread: threading.Thread | None = None

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(message)

    @staticmethod
    def _sections(count: int, leds_per_strip: int):
        """Split the chain into the green/yellow/red thirds of each strip."""
        third = max(1, leds_per_strip // 3)
        green: list[int] = []
        yellow: list[int] = []
        red: list[int] = []
        for i in range(count):
            pos = i % leds_per_strip
            if pos < third:
                green.append(i)
            elif pos < 2 * third:
                yellow.append(i)
            else:
                red.append(i)
        return green, yellow, red

    def _ensure(self):
        if self._pixels is not None or self._failed:
            return self._pixels
        try:
            import board
            import neopixel

            name = (self._pin_name or "").strip()
            if name.isdigit():
                name = "D" + name
            order = getattr(neopixel, (self._order or "GRB").strip().upper(), neopixel.GRB)
            self._pixels = neopixel.NeoPixel(
                getattr(board, name),
                self._count,
                brightness=max(0.0, min(1.0, self._brightness / 255.0)),
                pixel_order=order,
                auto_write=False,
            )
            self._pixels.fill(OFF_RGB)
            self._pixels.show()
            log.info("NeoPixel tower ready (%d LEDs on %s)", self._count, name)
        except Exception as exc:  # missing lib / not wired
            self._failed = True
            self._warn_once(
                "no_neotower",
                f"NeoPixel tower unavailable ({exc}); continuing without it",
            )
        return self._pixels

    def _fill(self, pixels, color) -> None:
        for i in range(self._count):
            pixels[i] = color

    def _draw(self, pixels, now: float) -> None:
        with self._lock:
            state = self._state
        if state == "idle":
            # Rainbow: the three sections each show a hue, 1/3 of the wheel apart.
            self._fill(pixels, OFF_RGB)
            phase = (now / self._idle_seconds) % 1.0
            for section, offset in (
                (self._green, 0.0), (self._yellow, 1 / 3), (self._red, 2 / 3),
            ):
                color = _hsv_rgb(phase + offset)
                for i in section:
                    pixels[i] = color
        else:
            # Traffic light: only ONE section lights — top green, middle yellow,
            # bottom red. "blink_*" toggles just that section on/off.
            on = True
            if state == "green":
                section, color = self._green, GREEN_RGB
            elif state == "yellow":
                section, color = self._yellow, YELLOW_RGB
            elif state == "red":
                section, color = self._red, RED_RGB
            elif state in ("error", "blink_red"):
                section, color, on = self._red, RED_RGB, int(now * 2) % 2 == 0
            elif state == "blink_green":
                section, color, on = self._green, GREEN_RGB, int(now * 2) % 2 == 0
            elif state == "blink_yellow":
                section, color, on = self._yellow, YELLOW_RGB, int(now * 2) % 2 == 0
            else:
                section, color, on = None, None, False
            self._fill(pixels, OFF_RGB)
            if section and on:
                for i in section:
                    pixels[i] = color
        pixels.show()

    def _run(self) -> None:
        while True:
            if self._pixels is not None:
                try:
                    self._draw(self._pixels, time.monotonic())
                except Exception as exc:
                    log.warning("NeoPixel update failed: %s", exc)
            time.sleep(1.0 / NEOTOWER_FPS)

    def show(self, state: str) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._state = state
        if self._ensure() is None:
            return
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def close(self) -> None:
        if self._pixels is not None:
            try:
                self._pixels.fill(OFF_RGB)
                self._pixels.show()
            except Exception:
                pass


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

    def _get_all(self, path: str, params: dict | None = None) -> list:
        """GET a paginated endpoint, following the Link: rel=next header."""
        url = self._base + path
        results: list = []
        while url:
            resp = self._session.get(url, params=params, timeout=self._timeout)
            if resp.status_code == 404:
                break
            resp.raise_for_status()
            data = resp.json()
            results.extend(data if isinstance(data, list) else [data])
            url = (resp.links.get("next") or {}).get("url")
            params = None  # the next link already carries its query string
        return results

    def roster(self, course_id: int) -> tuple[dict[str, int], dict[int, str]]:
        """Map sis_user_id / login_id -> user id, plus user id -> first name.

        Paginated; the first name comes from the roster's `name` field, so no
        extra API call is needed.
        """
        users = self._get_all(
            f"/api/v1/courses/{course_id}/users", {"per_page": 100}
        )
        mapping: dict[str, int] = {}
        names: dict[int, str] = {}
        for user in users:
            user_id = user.get("id")
            if user_id is None:
                continue
            full = (user.get("name") or "").strip()
            names[user_id] = full.split()[0] if full else ""
            for key in (user.get("sis_user_id"), user.get("login_id")):
                if not key:
                    continue
                key = str(key)
                for form in {key, key.lstrip("0")}:
                    mapping.setdefault(form, user_id)
        return mapping, names

    def find_user(self, roster: dict[str, int], auid: str) -> int | None:
        for form in (auid, auid.zfill(8), auid.lstrip("0")):
            if form in roster:
                return roster[form]
        return None

    def submission(self, course_id: int, assignment_id: int, user_id: int) -> dict | None:
        return self._get(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
            f"/submissions/{user_id}"
        )


def _graded(submission: dict | None) -> bool:
    if not submission:
        return False
    return bool(submission.get("graded_at")) or submission.get("workflow_state") == "graded"


def _quiz_passed(submission: dict | None, passing: float) -> bool:
    if not submission or submission.get("score") is None:
        return False
    return float(submission["score"]) >= passing


def classify(waiver: dict | None, quiz: dict | None, passing: float) -> tuple[str, tuple]:
    """Return (log factor, light pattern) for a student's two submissions."""
    waiver_ok = _graded(waiver)
    quiz_ok = _quiz_passed(quiz, passing)
    if waiver_ok and quiz_ok:
        return "Fully Cleared", ("solid", "green")
    if waiver_ok:
        return "Waiver Signed – Missing Quiz", ("alt", "green", "yellow")
    if quiz_ok:
        return "Quiz Passed – Missing Waiver", ("alt", "yellow", "red")
    return "Incomplete – No Progress", ("solid", "red")


class MockCanvas:
    """Offline stand-in so the reader + tower can be tested without a token.

    Deterministic per (user, assignment) so different cards show different
    outcomes.
    """

    def roster(self, course_id: int):
        return {}, {}

    def find_user(self, roster: dict, auid: str):
        return int(auid) if auid.isdigit() else None

    def submission(self, course_id: int, assignment_id: int, user_id: int):
        h = (sum(int(c) for c in str(user_id)) + int(assignment_id)) % 4
        graded = "2026-01-01T00:00:00Z"
        if h == 0:
            return {"score": 10.0, "graded_at": graded, "workflow_state": "graded"}
        if h == 1:
            return {"score": 5.0, "graded_at": graded, "workflow_state": "graded"}
        if h == 2:
            return {"score": None, "graded_at": None, "workflow_state": "unsubmitted"}
        return None


# --- Swipe logging to a Google Sheet (via Apps Script web app) --------------
def _post_log(config: dict, auid: str, status: str) -> None:
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    resp = requests.post(
        config["log_url"],
        json={
            "token": config["log_token"],
            "timestamp": timestamp,
            "auid": auid,
            "status": status,
        },
        timeout=config["log_timeout"],
        allow_redirects=False,  # the row is appended during the POST itself
    )
    resp.raise_for_status()


def log_swipe(config: dict, auid: str, status: str) -> None:
    """Fire-and-forget: log one swipe without blocking the swipe loop.

    No retry / no local buffer; if offline the POST just fails and warns.
    """
    if not config["log_enabled"] or not config["log_url"]:
        return

    def _worker() -> None:
        try:
            _post_log(config, auid, status)
        except requests.RequestException as exc:
            log.warning("log POST failed: %s", exc)

    threading.Thread(target=_worker, daemon=True).start()


def log_test(config: dict) -> int:
    if not config["log_url"]:
        print("No [log] url configured in the config file.")
        return 1
    try:
        _post_log(config, "TEST", "Fully Cleared")
    except requests.RequestException as exc:
        print(f"error: {exc}")
        return 1
    print("Sent a test row. Check the spreadsheet.")
    return 0


# --- Sound effects (per-AUID audio via the headphone jack) ------------------
class SoundPlayer:
    """Plays <dir>/<AUID>.<ext> through a decoder, one at a time (queued)."""

    def __init__(self, directory: str = "Sounds", player: str = "mpg123",
                 volume: float = 80.0, player_args: str = "", enabled: bool = True,
                 device: str = "auto"):
        self._dir = directory
        self._player = player
        self._volume = volume
        self._player_args = shlex.split(player_args) if player_args else []
        self._enabled = enabled
        self._device = device
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._warned: set[str] = set()

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(message)

    def _find(self, auid: str) -> str | None:
        for ext in (".mp3", ".wav", ".ogg"):
            path = os.path.join(self._dir, auid + ext)
            if os.path.exists(path):
                return path
        return None

    def _detect_device(self) -> str | None:
        """Prefer a USB audio card; else the first (built-in) card."""
        try:
            with open("/proc/asound/cards", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            return None  # not Linux / no ALSA
        cards: list[tuple[int, str]] = []
        for line in content.splitlines():
            m = re.match(r"\s*(\d+)\s+\[([^\]]*)\]:\s*(\S+)\s*-\s*(.*)", line)
            if m:
                cards.append((int(m.group(1)), (m.group(2) + m.group(3) + m.group(4)).lower()))
        if not cards:
            return None
        for card_id, key in cards:          # 1) USB speaker if present
            if "usb" in key:
                return f"plughw:{card_id},0"
        for card_id, key in cards:          # 2) the analog headphone jack
            if "headphone" in key:
                return f"plughw:{card_id},0"
        return f"plughw:{cards[0][0]},0"    # 3) otherwise the first card

    def _resolve_device(self) -> str | None:
        """Return an ALSA device, or None to use the player's default.

        device = auto -> prefer USB, else built-in; "none" -> don't force;
        anything else is used verbatim (e.g. plughw:1,0 or default).
        """
        if self._device and self._device.lower() == "none":
            return None
        if self._device and self._device.lower() != "auto":
            return self._device
        return self._detect_device()

    def enqueue(self, auid: str) -> None:
        """Queue a card's sound if a matching file exists (never blocks)."""
        if not self._enabled:
            return
        path = self._find(auid)
        if path is None:
            return
        self._queue.put(path)
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._work, daemon=True)
            self._thread.start()

    def _work(self) -> None:
        while True:
            path = self._queue.get()
            try:
                self.play_file(path)
            finally:
                self._queue.task_done()

    def play_file(self, path: str) -> None:
        exe = shutil.which(self._player)
        if not exe:
            self._warn_once(
                "no_player",
                f"audio player '{self._player}' not found; install it "
                "(e.g. sudo apt install mpg123)",
            )
            return
        cmd = [exe, "-q", *self._player_args]
        if os.path.basename(exe) == "mpg123":
            if "-a" not in self._player_args:
                device = self._resolve_device()
                if device:
                    cmd += ["-o", "alsa", "-a", device]
            if self._volume is not None:
                # mpg123 -f takes an integer scalefactor (32768 == 100%).
                scale = int(max(0.0, min(1.5, self._volume / 100.0)) * 32768)
                cmd += ["-f", str(scale)]
        cmd.append(path)
        try:
            subprocess.run(cmd, timeout=120, check=False)
        except Exception as exc:
            log.warning("audio playback failed: %s", exc)

    def play_auid(self, auid: str) -> bool:
        """Play a card's sound now (blocking); returns False if no file."""
        path = self._find(auid)
        if path is None:
            return False
        self.play_file(path)
        return True


# --- eInk status display (Adafruit 2.13" mono FeatherWing #4195, SSD1680) ---
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


class EInkDisplay:
    """2.13" mono eInk status screen on SPI (SSD1680).

    Everything (Blinka/board, adafruit_epd, PIL, and the panel itself) is set up
    lazily, so a missing library or an un-wired display is non-fatal: the app
    keeps running and warns once. Rendering happens on a background thread so
    the multi-second eInk refresh never blocks the swipe loop; rapid requests
    collapse to the most recent screen.
    """

    def __init__(self, enabled: bool = True, cs: str = "CE0", dc: str = "D22",
                 rst: str = "D13", busy: str = "",
                 idle_text: str = "Swipe your AU card",
                 idle_subtitle: str = "Design & Build Lab"):
        self._enabled = enabled
        self._cs_name = cs
        self._dc_name = dc
        self._rst_name = rst
        self._busy_name = busy
        self._idle_text = idle_text
        self._idle_subtitle = idle_subtitle
        self._display = None
        self._failed = False
        self._warned: set[str] = set()
        self._cv = threading.Condition()
        self._pending: tuple[str, str] | None = None
        self._thread: threading.Thread | None = None

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(message)

    @staticmethod
    def _pin(name: str):
        """Blinka pin object for a name like CE0/D22, or a plain BCM number."""
        import board
        name = (name or "").strip()
        if name.lower() in ("", "none"):
            return None
        if name.isdigit():
            name = "D" + name
        return getattr(board, name)

    def _ensure(self):
        if self._display is not None or self._failed:
            return self._display
        try:
            import board
            import busio
            import digitalio
            from adafruit_epd.ssd1680 import Adafruit_SSD1680

            spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
            cs = digitalio.DigitalInOut(self._pin(self._cs_name))
            dc = digitalio.DigitalInOut(self._pin(self._dc_name))
            rst = digitalio.DigitalInOut(self._pin(self._rst_name))
            busy_pin = self._pin(self._busy_name)
            busy = digitalio.DigitalInOut(busy_pin) if busy_pin is not None else None
            self._display = Adafruit_SSD1680(
                122, 250, spi, cs_pin=cs, dc_pin=dc, sramcs_pin=None,
                rst_pin=rst, busy_pin=busy,
            )
            self._display.rotation = 3  # landscape -> 250 x 122
            log.info(
                "eInk display ready (%dx%d)", self._display.width, self._display.height
            )
        except Exception as exc:  # missing lib / SPI off / not wired
            self._failed = True
            self._warn_once(
                "no_display", f"eInk display unavailable ({exc}); continuing without it"
            )
        return self._display

    @staticmethod
    def _wrap(text: str, font, max_width: float) -> list[str]:
        lines: list[str] = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if current and font.getlength(candidate) > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines or [text]

    def _render(self, title: str, subtitle: str):
        from PIL import Image, ImageDraw, ImageFont

        width, height = self._display.width, self._display.height
        image = Image.new("L", (width, height), 255)
        draw = ImageDraw.Draw(image)

        def load(size: int, bold: bool):
            return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)

        title_font = load(13, True)
        title_lines = self._wrap(title, title_font, width - 10)
        sub_font = load(11, False)
        sub_lines = self._wrap(subtitle, sub_font, width - 10) if subtitle else []
        block = len(title_lines) * (title_font.size + 6)
        if sub_lines:
            block += 6 + len(sub_lines) * (sub_font.size + 3)

        # Shrink the title until the whole block fits the panel.
        for size in (30, 26, 22, 18, 15, 13):
            font = load(size, True)
            lines = self._wrap(title, font, width - 10)
            small = load(max(11, size - 9), False)
            small_lines = self._wrap(subtitle, small, width - 10) if subtitle else []
            candidate = len(lines) * (size + 6)
            if small_lines:
                candidate += 6 + len(small_lines) * (small.size + 3)
            if candidate <= height - 4:
                title_font, title_lines, sub_font, sub_lines, block = (
                    font, lines, small, small_lines, candidate
                )
                break

        y = max(2, (height - block) // 2)
        for line in title_lines:
            draw.text(
                ((width - title_font.getlength(line)) / 2, y),
                line, font=title_font, fill=0,
            )
            y += title_font.size + 6
        if sub_lines:
            y += 6
            for line in sub_lines:
                draw.text(
                    ((width - sub_font.getlength(line)) / 2, y),
                    line, font=sub_font, fill=0,
                )
                y += sub_font.size + 3
        return image

    def _flush(self, title: str, subtitle: str) -> bool:
        display = self._ensure()
        if display is None:
            return False
        try:
            display.image(self._render(title, subtitle))
            display.display()
            return True
        except Exception as exc:
            log.warning("eInk update failed: %s", exc)
            return False

    def _run(self) -> None:
        while True:
            with self._cv:
                while self._pending is None:
                    self._cv.wait()
                title, subtitle = self._pending
                self._pending = None
            if self._failed:
                continue
            self._flush(title, subtitle)

    def show(self, title: str, subtitle: str = "") -> None:
        """Queue a screen; the background thread refreshes the panel."""
        if not self._enabled:
            return
        with self._cv:
            self._pending = (title, subtitle)
            self._cv.notify()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def show_idle(self) -> None:
        self.show(self._idle_text, self._idle_subtitle)

    def show_now(self, title: str, subtitle: str = "") -> bool:
        """Render synchronously (used by --test-display). Returns success."""
        return self._flush(title, subtitle)


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


def list_devices() -> None:
    """Show input devices (card reader) and serial ports (tower light)."""
    try:
        import evdev
        from evdev import ecodes
    except ImportError:
        print("Input devices: evdev not installed (only needed on the Pi)")
    else:
        print("Input devices (pick one whose name substring goes in reader.device_name):")
        for path in evdev.list_devices():
            device = evdev.InputDevice(path)
            keys = device.capabilities().get(ecodes.EV_KEY, [])
            print(f"  {device.path}\tkeyboard={ecodes.KEY_1 in keys}\tname={device.name!r}")
            device.close()
    try:
        import serial.tools.list_ports as list_ports
    except ImportError:
        print("Serial ports: pyserial not installed")
    else:
        print("Serial ports:")
        for port in list_ports.comports():
            vid = f"0x{port.vid:04X}" if port.vid else "?"
            tag = "  <- tower (CH340)" if port.vid == TOWER_VID else ""
            print(f"  {port.device}\tvid={vid}\t{port.description}{tag}")


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

    def flag(section: str, option: str, fallback: bool = False) -> bool:
        raw = text(section, option)
        return raw.lower() in ("1", "true", "yes", "on") if raw else fallback

    token = os.environ.get("CANVAS_TOKEN") or text("canvas", "token")
    return {
        "url": text("canvas", "url", "https://american.instructure.com"),
        "token": token,
        "course_id": number("canvas", "course_id", 0, int),
        "assignment_id": number("canvas", "assignment_id", 0, int),  # Safety Waiver
        "quiz_assignment_id": number("canvas", "quiz_assignment_id", 0, int),
        "quiz_passing_score": number("canvas", "quiz_passing_score", 9.0, float),
        "port": text("tower", "port", "auto"),
        "device_name": text("reader", "device_name"),
        "result_seconds": number("reader", "result_seconds", 6.0, float),
        "log_enabled": flag("log", "enabled", False),
        "log_url": text("log", "url"),
        "log_token": text("log", "token"),
        "log_timeout": number("log", "timeout", 10.0, float),
        "audio_enabled": flag("audio", "enabled", True),
        "audio_dir": text("audio", "dir", "Sounds"),
        "audio_player": text("audio", "player", "mpg123"),
        "audio_volume": number("audio", "volume", 80.0, float),
        "audio_args": text("audio", "player_args"),
        "audio_device": text("audio", "device", "auto"),
        "display_enabled": flag("display", "enabled", True),
        "display_cs": text("display", "cs", "CE0"),
        "display_dc": text("display", "dc", "D22"),
        "display_rst": text("display", "rst", "D13"),
        "display_busy": text("display", "busy"),
        "display_idle_text": text("display", "idle_text", "Swipe your AU card"),
        "display_idle_subtitle": text("display", "subtitle", "Design & Build Lab"),
        "neotower_enabled": flag("neotower", "enabled", True),
        "neotower_pin": text("neotower", "pin", "D18"),
        "neotower_count": number("neotower", "count", 36, int),
        "neotower_leds_per_strip": number("neotower", "leds_per_strip", 9, int),
        "neotower_brightness": number("neotower", "brightness", 80.0, float),
        "neotower_order": text("neotower", "order", "GRB"),
        "neotower_idle_seconds": number("neotower", "idle_seconds", 8.0, float),
    }


def make_neotower(config: dict) -> NeoTower:
    return NeoTower(
        enabled=config["neotower_enabled"],
        pin=config["neotower_pin"],
        count=config["neotower_count"],
        leds_per_strip=config["neotower_leds_per_strip"],
        brightness=config["neotower_brightness"],
        order=config["neotower_order"],
        idle_seconds=config["neotower_idle_seconds"],
    )


def make_display(config: dict) -> EInkDisplay:
    return EInkDisplay(
        enabled=config["display_enabled"],
        cs=config["display_cs"],
        dc=config["display_dc"],
        rst=config["display_rst"],
        busy=config["display_busy"],
        idle_text=config["display_idle_text"],
        idle_subtitle=config["display_idle_subtitle"],
    )


def run(config: dict, use_stdin: bool, mock: bool = False) -> int:
    towers = TowerSet(TowerLight(config["port"]), make_neotower(config))
    towers.show("idle")
    display = make_display(config)
    display.show_idle()
    canvas = MockCanvas() if mock else Canvas(config["url"], config["token"])
    audio = SoundPlayer(
        config["audio_dir"],
        config["audio_player"],
        config["audio_volume"],
        config["audio_args"],
        config["audio_enabled"],
        config["audio_device"],
    )
    roster: dict[str, int] | None = None
    names: dict[int, str] = {}

    swipes = (line for line in sys.stdin) if use_stdin else read_swipes_evdev(
        config["device_name"]
    )

    try:
        for raw in swipes:
            auid = parse_auid(raw)
            if not auid:
                continue
            audio.enqueue(auid)
            try:
                if roster is None:
                    roster, names = canvas.roster(config["course_id"])
                user_id = canvas.find_user(roster, auid)
                if user_id is None:
                    factor, pattern = "Unregistered", ("blink", "red")
                    display.show("Unregistered")
                else:
                    waiver = canvas.submission(
                        config["course_id"], config["assignment_id"], user_id
                    )
                    quiz = canvas.submission(
                        config["course_id"], config["quiz_assignment_id"], user_id
                    )
                    factor, pattern = classify(
                        waiver, quiz, config["quiz_passing_score"]
                    )
                    if factor == "Fully Cleared":
                        first = names.get(user_id, "")
                        display.show(
                            f"Welcome, {first}!" if first else "Welcome!",
                            "Safety training complete",
                        )
                    else:
                        display.show(factor)
                log.info("AUID %s -> %s", auid, factor)
                log_swipe(config, auid, factor)
                towers.present(pattern, config["result_seconds"])
            except requests.RequestException as exc:
                towers.show("error")
                display.show("Try again", "Connection error")
                log.error("Canvas error (swipe not logged): %s", exc)
                time.sleep(config["result_seconds"])
            towers.show("idle")
            display.show_idle()
    except KeyboardInterrupt:
        log.info("Stopping")
    finally:
        towers.close()
    return 0


def test_display(config: dict) -> int:
    display = make_display(config)
    screens = [
        (config["display_idle_text"], config["display_idle_subtitle"]),
        ("Welcome, Jack!", "Safety training complete"),
        ("Waiver Signed – Missing Quiz", ""),
        ("Quiz Passed – Missing Waiver", ""),
        ("Incomplete – No Progress", ""),
        ("Unregistered", ""),
    ]
    for title, subtitle in screens:
        print(f"Showing: {title}")
        if not display.show_now(title, subtitle):
            print("eInk display not available (check wiring, SPI, and libraries).")
            return 1
        time.sleep(3)
    return 0


def test_neotower(config: dict) -> int:
    tower = make_neotower(config)
    steps = (("idle", 8), ("green", 3), ("yellow", 3),
             ("red", 3), ("error", 3), ("idle", 3))
    for state, wait in steps:
        print(f"Showing: {state}")
        tower.show(state)
        time.sleep(wait)
    tower.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AU card reader -> Canvas tower light")
    parser.add_argument("--config", default="config.ini")
    parser.add_argument("--stdin", action="store_true", help="read swipes from stdin (dev)")
    parser.add_argument("--mock", action="store_true",
                        help="use a fake Canvas (test reader + tower offline)")
    parser.add_argument("--test-lamp", action="store_true",
                        help="cycle both tower lights (USB + NeoPixel)")
    parser.add_argument("--test-display", action="store_true",
                        help="cycle the eInk screens (idle + each result) and exit")
    parser.add_argument("--test-neotower", action="store_true",
                        help="cycle the NeoPixel tower states and exit")
    parser.add_argument("--list-devices", action="store_true",
                        help="list input devices + serial ports and exit")
    parser.add_argument("--log-test", action="store_true",
                        help="send one test row to the configured log URL and exit")
    parser.add_argument("--play", metavar="AUID",
                        help="play the sound for an AUID and exit")
    args = parser.parse_args(argv)

    config = load_config(args.config)

    if args.list_devices:
        list_devices()
        return 0

    if args.log_test:
        return log_test(config)

    if args.play:
        audio = SoundPlayer(
            config["audio_dir"],
            config["audio_player"],
            config["audio_volume"],
            config["audio_args"],
            enabled=True,
            device=config["audio_device"],
        )
        if audio.play_auid(args.play):
            return 0
        print(f"No sound file for {args.play!r} in {config['audio_dir']!r}")
        return 1

    if args.test_lamp:
        towers = TowerSet(TowerLight(config["port"]), make_neotower(config))
        for state, wait in (("idle", 2), ("green", 2), ("yellow", 2),
                            ("red", 2), ("error", 2), ("idle", 1)):
            towers.show(state)
            time.sleep(wait)
        towers.close()
        return 0

    if args.test_display:
        return test_display(config)

    if args.test_neotower:
        return test_neotower(config)

    return run(config, args.stdin, args.mock)


if __name__ == "__main__":
    raise SystemExit(main())
