import os
import tempfile
import unittest

from au_card_reader.canvas_client import CanvasClient, CanvasEvaluator
from au_card_reader.config import Config
from au_card_reader.mapping import UserResolver
from au_card_reader.state import State


class FakeResponse:
    def __init__(self, status_code=200, payload=None, links=None):
        self.status_code = status_code
        self._payload = payload
        self.links = links or {}
        self.text = ""

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self._responses.pop(0)


class PaginationTests(unittest.TestCase):
    def test_follows_next_link(self):
        client = CanvasClient("https://example.test", "token")
        client._session = FakeSession(
            [
                FakeResponse(
                    200,
                    [{"id": 1}],
                    {"next": {"url": "https://example.test/page2"}},
                ),
                FakeResponse(200, [{"id": 2}]),
            ]
        )
        users = client.course_users(7)
        self.assertEqual([u["id"] for u in users], [1, 2])
        self.assertEqual(len(client._session.calls), 2)
        self.assertEqual(client._session.calls[1][0], "https://example.test/page2")

    def test_404_returns_empty(self):
        client = CanvasClient("https://example.test", "token")
        client._session = FakeSession([FakeResponse(404)])
        self.assertEqual(client.course_users(7), [])


class RosterClient:
    def __init__(self, users):
        self._users = users
        self.calls = 0

    def course_users(self, course_id):
        self.calls += 1
        return self._users


class RosterResolverTests(unittest.TestCase):
    def test_matches_padded_sis_variant(self):
        client = RosterClient(
            [{"id": 42, "sis_user_id": "05550363", "login_id": "alice"}]
        )
        resolver = UserResolver(client, mode="roster", course_id=1,
                                candidate_widths=[7, 8])
        user_id, method = resolver.resolve("5550363")
        self.assertEqual(user_id, 42)
        self.assertTrue(method.startswith("roster"))

    def test_matches_login_id_fallback(self):
        client = RosterClient([{"id": 7, "sis_user_id": None, "login_id": "5550363"}])
        resolver = UserResolver(client, mode="roster", course_id=1)
        self.assertEqual(resolver.resolve("5550363"), (7, "roster:5550363"))

    def test_no_match(self):
        client = RosterClient([{"id": 7, "sis_user_id": "999", "login_id": "x"}])
        resolver = UserResolver(client, mode="roster", course_id=1)
        self.assertEqual(resolver.resolve("5550363"), (None, "none"))

    def test_roster_is_cached(self):
        client = RosterClient([{"id": 42, "sis_user_id": "5550363"}])
        resolver = UserResolver(client, mode="roster", course_id=1,
                                roster_cache_ttl=60)
        resolver.resolve("5550363")
        resolver.resolve("5550363")
        self.assertEqual(client.calls, 1)

    def test_csv_takes_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "map.csv")
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write("auid,canvas_user_id\n5550363,99\n")
            client = RosterClient([])
            resolver = UserResolver(client, mode="roster", csv_path=path,
                                    course_id=1)
            self.assertEqual(resolver.resolve("5550363"), (99, "csv"))
            self.assertEqual(client.calls, 0)


class EvalClient:
    def __init__(self, users, submission, assignment=None):
        self._users = users
        self._submission = submission
        self._assignment = assignment or {"name": "HW1"}
        self.enrollment_calls = 0

    def course_users(self, course_id):
        return self._users

    def submission(self, course_id, assignment_id, user_id):
        return self._submission

    def assignment(self, course_id, assignment_id):
        return self._assignment

    def enrollments(self, course_id, user_id):
        self.enrollment_calls += 1
        return [{"id": 1, "type": "StudentEnrollment"}]


def make_evaluator(users, submission):
    config = Config()
    config.canvas.course_id = 1
    config.canvas.assignment_id = 2
    config.mapping.mode = "roster"
    client = EvalClient(users, submission)
    resolver = UserResolver(client, mode="roster", course_id=1)
    return CanvasEvaluator(config, client=client, resolver=resolver), client


class GradedEvaluationTests(unittest.TestCase):
    USERS = [{"id": 42, "sis_user_id": "5550363", "login_id": "alice"}]

    def test_graded_at_is_done(self):
        ev, _ = make_evaluator(
            self.USERS,
            {"graded_at": "2024-01-01T00:00:00Z", "workflow_state": "graded"},
        )
        result = ev.evaluate("5550363")
        self.assertEqual(result.state, State.DONE)
        self.assertTrue(result.graded)

    def test_workflow_graded_is_done(self):
        ev, _ = make_evaluator(self.USERS, {"workflow_state": "graded"})
        self.assertEqual(ev.evaluate("5550363").state, State.DONE)

    def test_submitted_but_not_graded_is_not_done(self):
        ev, _ = make_evaluator(
            self.USERS,
            {"workflow_state": "submitted", "submitted_at": "2024-01-01T00:00:00Z"},
        )
        result = ev.evaluate("5550363")
        self.assertEqual(result.state, State.NOT_DONE)
        self.assertFalse(result.graded)

    def test_no_submission_is_not_done(self):
        ev, _ = make_evaluator(self.USERS, None)
        self.assertEqual(ev.evaluate("5550363").state, State.NOT_DONE)

    def test_unknown_auid_is_not_enrolled(self):
        ev, _ = make_evaluator(self.USERS, {"workflow_state": "graded"})
        self.assertEqual(ev.evaluate("1000000").state, State.NOT_ENROLLED)

    def test_roster_hit_skips_enrollment_call(self):
        ev, client = make_evaluator(self.USERS, {"workflow_state": "graded"})
        ev.evaluate("5550363")
        self.assertEqual(client.enrollment_calls, 0)


if __name__ == "__main__":
    unittest.main()
