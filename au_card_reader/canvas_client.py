"""Canvas REST client and evaluator.

Ready to use once ``canvas.base_url``, a token, ``course_id`` and
``assignment_id`` are configured. Until then, run the app with ``--mock``.

A student is considered done (green) when the submission is **graded**: either
``graded_at`` is set, or ``workflow_state`` is in ``completed_states``.
"""

from __future__ import annotations

import logging

from .config import Config
from .evaluator import Evaluator
from .mapping import UserResolver
from .state import Evaluation, State

logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:  # pragma: no cover - requests is a hard runtime dep
    requests = None  # type: ignore


class CanvasError(RuntimeError):
    """Raised for Canvas API failures (network, auth, HTTP errors)."""


class CanvasClient:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0):
        if requests is None:  # pragma: no cover
            raise CanvasError("The 'requests' package is required for Canvas access.")
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"
        self._session.headers["Accept"] = "application/json"

    def _request(self, url: str, params: dict | None = None):
        try:
            response = self._session.get(url, params=params, timeout=self._timeout)
        except requests.RequestException as exc:  # type: ignore[union-attr]
            raise CanvasError(f"Request to {url} failed: {exc}") from exc

        if response.status_code == 404:
            return None
        if response.status_code == 401:
            raise CanvasError("Unauthorized (missing or expired Canvas token).")
        if response.status_code == 403:
            raise CanvasError("Forbidden (token lacks access to this resource).")
        if response.status_code >= 400:
            raise CanvasError(
                f"HTTP {response.status_code} for {url}: {response.text[:200]}"
            )
        return response

    def _get(self, path: str, params: dict | None = None):
        response = self._request(f"{self._base}{path}", params)
        return None if response is None else response.json()

    def _get_all(self, path: str, params: dict | None = None) -> list[dict]:
        """GET a paginated list endpoint, following the ``Link: rel=next``."""
        url = f"{self._base}{path}"
        results: list[dict] = []
        while url:
            response = self._request(url, params)
            if response is None:
                break
            data = response.json()
            if isinstance(data, list):
                results.extend(data)
            else:
                results.append(data)
            url = (response.links.get("next") or {}).get("url")
            params = None  # the next link already carries its query string
        return results

    def user_by_sis(self, sis_id: str) -> dict | None:
        return self._get(f"/api/v1/users/sis_user_id:{sis_id}")

    def course_users(self, course_id: int) -> list[dict]:
        """All users in a course (paginated). Each has sis_user_id/login_id."""
        return self._get_all(
            f"/api/v1/courses/{course_id}/users", params={"per_page": 100}
        )

    def enrollments(self, course_id: int, user_id: int) -> list[dict]:
        result = self._get(
            f"/api/v1/courses/{course_id}/enrollments",
            params={"user_id": user_id, "state[]": "active"},
        )
        return result or []

    def submission(self, course_id: int, assignment_id: int, user_id: int) -> dict | None:
        return self._get(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
            f"/submissions/{user_id}"
        )

    def assignment(self, course_id: int, assignment_id: int) -> dict | None:
        return self._get(f"/api/v1/courses/{course_id}/assignments/{assignment_id}")


class CanvasEvaluator(Evaluator):
    def __init__(self, config: Config, client: CanvasClient | None = None,
                 resolver: UserResolver | None = None):
        self._cfg = config
        self._client = client or CanvasClient(
            config.canvas.base_url,
            config.canvas.token,
            config.canvas.request_timeout_seconds,
        )
        self._resolver = resolver or UserResolver(
            self._client,
            mode=config.mapping.mode,
            csv_path=config.mapping.csv_path,
            candidate_widths=config.parsing.auid_candidate_widths,
            course_id=config.canvas.course_id,
            roster_cache_ttl=config.mapping.roster_cache_ttl_seconds,
        )
        self._assignment_name: str | None = None

    @property
    def client(self) -> CanvasClient:
        return self._client

    @property
    def resolver(self) -> UserResolver:
        return self._resolver

    def _is_graded(self, submission: dict | None) -> bool:
        if not submission:
            return False
        if submission.get("graded_at"):
            return True
        return submission.get("workflow_state") in self._cfg.canvas.completed_states

    def evaluate(self, auid: str) -> Evaluation:
        user_id, method = self._resolver.resolve(auid)
        if user_id is None:
            return Evaluation(
                auid,
                State.NOT_ENROLLED,
                "No Canvas user found for this AUID",
                resolved_by=method,
                enrolled=False,
            )

        # A roster/CSV hit already proves enrollment; skip the extra API call.
        if method.startswith("roster") or method.startswith("csv"):
            enrolled = True
        else:
            enrolled = bool(
                self._client.enrollments(self._cfg.canvas.course_id, user_id)
            )
        if not enrolled:
            return Evaluation(
                auid,
                State.NOT_ENROLLED,
                "Not enrolled in this course",
                canvas_user_id=user_id,
                resolved_by=method,
                enrolled=False,
            )

        submission = self._client.submission(
            self._cfg.canvas.course_id, self._cfg.canvas.assignment_id, user_id
        )

        if self._assignment_name is None:
            assignment = self._client.assignment(
                self._cfg.canvas.course_id, self._cfg.canvas.assignment_id
            )
            if assignment:
                self._assignment_name = assignment.get("name")

        if self._is_graded(submission):
            return Evaluation(
                auid,
                State.DONE,
                "Assignment graded",
                canvas_user_id=user_id,
                resolved_by=method,
                enrolled=True,
                graded=True,
                assignment_name=self._assignment_name,
            )

        return Evaluation(
            auid,
            State.NOT_DONE,
            "Enrolled but assignment not graded",
            canvas_user_id=user_id,
            resolved_by=method,
            enrolled=True,
            graded=False,
            assignment_name=self._assignment_name,
        )
