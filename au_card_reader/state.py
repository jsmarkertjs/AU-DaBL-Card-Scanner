"""Shared state and result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class State(str, Enum):
    """High-level outcome for a swipe.

    ``IDLE`` is used between swipes (e.g. all lights off).
    """

    IDLE = "idle"
    NOT_ENROLLED = "not_enrolled"
    NOT_DONE = "not_done"
    DONE = "done"
    ERROR = "error"


LABELS: dict[State, str] = {
    State.IDLE: "idle",
    State.NOT_ENROLLED: "NOT ENROLLED (red)",
    State.NOT_DONE: "ENROLLED, NOT GRADED (yellow)",
    State.DONE: "GRADED (green)",
    State.ERROR: "ERROR",
}


@dataclass
class Evaluation:
    """Result of looking up a single AUID."""

    auid: str
    state: State
    message: str = ""
    canvas_user_id: int | None = None
    resolved_by: str | None = None
    enrolled: bool | None = None
    graded: bool | None = None
    assignment_name: str | None = None
    checked_at: datetime = field(default_factory=datetime.now)
