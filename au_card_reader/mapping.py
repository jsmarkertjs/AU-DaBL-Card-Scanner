"""Resolve an AUID to a Canvas user id.

Canvas may key students by SIS id, login id, or something else. Rather than
assume, we support three modes:

* ``roster`` (default) - fetch the course roster once (``/courses/:id/users``)
  and match the AUID and its zero-padded variants against each user's
  ``sis_user_id`` / ``login_id``. Cached with a TTL so swipes don't re-fetch.
* ``sis`` - call ``/users/sis_user_id:<auid>`` per swipe. May need elevated
  permissions and more API calls.
* ``csv`` - use a local ``auid_to_canvas_id.csv`` map (columns
  ``auid,canvas_user_id``). Always checked first when present.

Run ``--discover <AUID>`` to see which candidate matches, or ``--dump-roster``
to inspect the resolved roster.
"""

from __future__ import annotations

import csv
import logging
import time
from pathlib import Path
from typing import Iterable

from .parsing import auid_candidates

logger = logging.getLogger(__name__)


class UserResolver:
    def __init__(
        self,
        client,
        mode: str = "roster",
        csv_path: str | None = None,
        candidate_widths: Iterable[int] = (7, 8),
        course_id: int | None = None,
        roster_cache_ttl: float = 600.0,
    ):
        self._client = client
        self._mode = mode
        self._widths = list(candidate_widths)
        self._course_id = course_id
        self._roster_ttl = roster_cache_ttl
        self._csv = self._load_csv(csv_path) if csv_path else {}
        self._roster: dict[str, int] | None = None
        self._roster_expires = 0.0

    @staticmethod
    def _load_csv(csv_path: str) -> dict[str, int]:
        path = Path(csv_path)
        if not path.exists():
            return {}
        mapping: dict[str, int] = {}
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                auid = (row.get("auid") or "").strip()
                user_id = (row.get("canvas_user_id") or "").strip()
                if auid and user_id:
                    mapping[auid] = int(user_id)
        logger.info("Loaded %d AUID mappings from %s", len(mapping), csv_path)
        return mapping

    def candidates(self, auid: str) -> list[str]:
        """Possible Canvas identifiers derived from an AUID."""
        return auid_candidates(auid, self._widths)

    @staticmethod
    def _roster_keys(user: dict) -> list[str]:
        keys: list[str] = []
        for field in ("sis_user_id", "login_id"):
            value = user.get(field)
            if value is None:
                continue
            value = str(value).strip()
            for variant in (value, value.lstrip("0")):
                if variant and variant not in keys:
                    keys.append(variant)
        if user.get("id") is not None:
            keys.append(str(user["id"]))
        return keys

    def roster(self, force: bool = False) -> dict[str, int]:
        """Return the cached AUID->user_id roster, fetching if stale."""
        now = time.monotonic()
        if not force and self._roster is not None and now < self._roster_expires:
            return self._roster
        if self._course_id is None:
            raise ValueError("course_id is required for roster mapping")
        users = self._client.course_users(self._course_id)
        roster: dict[str, int] = {}
        for user in users:
            user_id = user.get("id")
            if user_id is None:
                continue
            for key in self._roster_keys(user):
                roster.setdefault(key, int(user_id))
        logger.info(
            "Loaded roster with %d users (%d keys) for course %s",
            len(users),
            len(roster),
            self._course_id,
        )
        self._roster = roster
        self._roster_expires = now + self._roster_ttl
        return roster

    def resolve(self, auid: str) -> tuple[int | None, str]:
        """Return ``(canvas_user_id, method)``; id is ``None`` when unknown."""
        if auid in self._csv:
            return self._csv[auid], "csv"

        if self._mode == "roster":
            roster = self.roster()
            for candidate in self.candidates(auid):
                if candidate in roster:
                    return roster[candidate], f"roster:{candidate}"

        elif self._mode == "sis":
            for candidate in self.candidates(auid):
                user = self._client.user_by_sis(candidate)
                if user and user.get("id"):
                    return int(user["id"]), f"sis:{candidate}"

        return None, "none"
