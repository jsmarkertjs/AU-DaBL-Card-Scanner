"""Configuration loading for the AU Card Reader.

Configuration lives in a YAML file (see ``config.example.yaml``). The Canvas
token may also be supplied through the ``AU_CANVAS_TOKEN`` (or
``CANVAS_TOKEN``) environment variable so it never has to be committed.
"""

from __future__ import annotations

import dataclasses
import os
import typing
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Default parsing pattern derived from an observed swipe: ``;00555036321?``
# -> AUID ``5550363``. Kept configurable because the exact encoding may vary.
DEFAULT_SWIPE_PATTERN = r"^;00(?P<auid>\d{7})\d{2}\?$"


@dataclass
class CanvasConfig:
    base_url: str = "https://au.instructure.com"
    token: str = ""
    course_id: int = 0
    assignment_id: int = 0
    # A submission counts as "graded" when workflow_state is one of these.
    # (graded_at being set also counts, regardless of this list.)
    completed_states: list[str] = field(default_factory=lambda: ["graded"])
    request_timeout_seconds: float = 10.0


@dataclass
class MappingConfig:
    mode: str = "roster"  # roster | sis | csv
    csv_path: str = "auid_to_canvas_id.csv"
    roster_cache_ttl_seconds: float = 600.0


@dataclass
class ParsingConfig:
    patterns: list[str] = field(default_factory=lambda: [DEFAULT_SWIPE_PATTERN])
    auid_candidate_widths: list[int] = field(default_factory=lambda: [7, 8])
    # Fallback when no regex matches: take the digit run and drop this many
    # leading / trailing digits (``00555036321`` -2/-2 -> ``5550363``).
    trim_prefix: int = 2
    trim_suffix: int = 2


@dataclass
class InputConfig:
    source: str = "evdev"  # evdev (Pi) | stdin (dev)
    device_name_regex: str = r"(?i)(reader|hid|card|magtek|symbol)"


@dataclass
class GpioConfig:
    red: int = 17
    yellow: int = 27
    green: int = 22
    active_high: bool = True


@dataclass
class IndicatorConfig:
    driver: str = "console"  # console | gpio
    gpio: GpioConfig = field(default_factory=GpioConfig)


@dataclass
class BehaviorConfig:
    debounce_seconds: float = 3.0
    cache_ttl_seconds: float = 60.0
    clear_after_seconds: float = 8.0


@dataclass
class Config:
    canvas: CanvasConfig = field(default_factory=CanvasConfig)
    mapping: MappingConfig = field(default_factory=MappingConfig)
    parsing: ParsingConfig = field(default_factory=ParsingConfig)
    input: InputConfig = field(default_factory=InputConfig)
    indicator: IndicatorConfig = field(default_factory=IndicatorConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)


def _from_dict(cls: type, data: dict | None) -> typing.Any:
    """Build a dataclass from a dict, ignoring unknown keys and recursing
    into nested dataclass fields."""
    if not isinstance(data, dict):
        return cls()
    hints = typing.get_type_hints(cls)
    kwargs: dict[str, typing.Any] = {}
    for f in dataclasses.fields(cls):
        if f.name not in data or data[f.name] is None:
            continue
        value = data[f.name]
        field_type = hints.get(f.name)
        if dataclasses.is_dataclass(field_type) and isinstance(value, dict):
            kwargs[f.name] = _from_dict(field_type, value)
        else:
            kwargs[f.name] = value
    return cls(**kwargs)


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load config from a YAML file (if present) and apply env overrides."""
    data: dict = {}
    if path is not None:
        config_path = Path(path)
        if config_path.exists():
            with config_path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        else:
            raise FileNotFoundError(f"Config file not found: {config_path}")

    config = _from_dict(Config, data)

    token = os.environ.get("AU_CANVAS_TOKEN") or os.environ.get("CANVAS_TOKEN")
    if token:
        config.canvas.token = token

    return config
