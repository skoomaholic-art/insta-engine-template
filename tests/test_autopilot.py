"""Safety and idempotency tests for autonomous Instagram publishing."""
import copy
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from igm.autopilot import choose_due, run_autopilot
from igm.instagram import InstagramError

NOW = datetime(2026, 10, 28, 16, 0, tzinfo=timezone.utc)


def good_post():
    return {
        "id": "cinema_001",
        "topic": "cinema",
        "owner_approved": True,
        "owner_approval_reference": "chat-approval-example",
        "owner_approved_at_utc": "2026-10-28T14:00:00+00:00",
        "has_third_party_assets": False,
        "needs_manual_review": False,
        "status": "ready",
        "format": "IMAGE",
        "caption": "Original review of a film",
        "image_url": "https://example.org/our-original-art.jpg",
        "rights_cleared": True,
        "facts_checked": True,
        "authorized_for_autopilot": True,
        "publish_at_utc": "2026-10-28T16:00:00+00:00",
    }


class FakeStore:
    def __init__(self, posts):
        self.manifest = {"version": 1, "posts": posts}
        self.states = []

    def load(self):
        return self.manifest, "start-sha"

    def save(self, queue, sha):
        self.states.append(copy.deepcopy(queue["posts"][0]["status"]))
        return f"sha-{len(self.states)}"


class FakeInstagram:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def assert_correct_account(self):
        return "mybrand"

    def publish_image(self, image_url, caption):
        self.calls += 1
        if self.fail:
            raise InstagramError("Meta unavailable")
        return {"media_id": "987654321", "container_id": "1234"}


class AutopilotTests(unittest.TestCase):
    def test_disabled_never_calls_instagram(self):
        store = FakeStore([good_post()])
        client = FakeInstagram()
        with patch.dict(os.environ, {"IGM_ENABLE_PUBLISH": "false"}):
            answer = run_autopilot(store, client, NOW)
        self.assertIn("выключен", answer)
        self.assertEqual(client.calls, 0)
        self.assertEqual(store.states, [])

    def test_rights_required_and_unsupported_formats_not_posted(self):
        for key, value in [
            ("rights_cleared", False),
            ("facts_checked", False),
            ("authorized_for_autopilot", False),
            ("format", "REEL"),
            ("image_url", "http://example.org/image.jpg"),
            ("status", "published"),
        ]:
            post = good_post()
            post[key] = value
            with self.subTest(key=key):
                self.assertIsNone(choose_due({"posts": [post]}, NOW))

    def test_expired_does_not_publish(self):
        post = good_post()
        post["publish_at_utc"] = (NOW - timedelta(days=3)).isoformat()
        self.assertIsNone(choose_due({"posts": [post]}, NOW))

    def test_duplicate_ids_fail_closed(self):
        p = good_post()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            choose_due({"posts": [p, copy.deepcopy(p)]}, NOW)

    def test_unapproved_draft_never_publishes(self):
        post = good_post()
        post["owner_approved"] = False
        self.assertIsNone(choose_due({"posts": [post]}, NOW))
        post["owner_approved"] = True
        post["owner_approval_reference"] = ""
        self.assertIsNone(choose_due({"posts": [post]}, NOW))
        post["owner_approval_reference"] = "chat-reviewed"
        post["owner_approved_at_utc"] = None
        self.assertIsNone(choose_due({"posts": [post]}, NOW))

    def test_review_flag_and_third_party_assets_block_publication(self):
        for field in ("needs_manual_review", "has_third_party_assets"):
            post = good_post()
            post[field] = True
            with self.subTest(field=field):
                self.assertIsNone(choose_due({"posts": [post]}, NOW))

    def test_invalid_autonomous_policy_fails_closed(self):
        from igm.autopilot import load_policy
        with patch("igm.autopilot.Path.read_text", return_value='{"schema_version":1,"mode":"autonomous"}'):
            with self.assertRaisesRegex(ValueError, "explicit owner activation"):
                load_policy()

    def test_enabled_flag_still_never_publishes(self):
        # Публикация автопилотом удалена: только пайплайн с двумя согласованиями владельца
        store = FakeStore([good_post()])
        client = FakeInstagram()
        with patch.dict(os.environ, {"IGM_ENABLE_PUBLISH": "true"}):
            answer = run_autopilot(store, client, NOW)
        self.assertIn("двумя согласованиями", answer)
        self.assertEqual(client.calls, 0)
        self.assertEqual(store.states, [])

    def test_autopilot_workflow_removed(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / ".github/workflows/instagram-autopilot.yml").exists())


if __name__ == "__main__":
    unittest.main()
