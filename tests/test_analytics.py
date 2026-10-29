"""Regression tests for longitudinal Instagram snapshot storage."""
import base64
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request
from unittest.mock import patch

from igm.analytics import collect_snapshot, persist_snapshot, history, render_trend, commit_github_snapshot
from igm.instagram import InstagramError


class FakeInstagram:
    def profile(self):
        return {"id": "17841425822615813", "username": "mybrand",
                "followers_count": 4, "media_count": 1}

    def recent_media(self, limit=50):
        return [{"id": "123456", "timestamp": "2026-10-27T13:02:00+0000",
                 "media_type": "IMAGE", "like_count": 3, "comments_count": 1,
                 "permalink": "https://www.instagram.com/p/demo/",
                 "caption": "Public sample"}]


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.data).encode("utf-8")


class SnapshotTests(unittest.TestCase):
    def test_snapshot_uses_almaty_date_and_no_secret(self):
        now = datetime(2026, 10, 27, 23, 15, tzinfo=timezone.utc)
        with patch.dict("os.environ", {"IG_EXPECTED_USERNAME": "mybrand"}):
            snap = collect_snapshot(FakeInstagram(), now)
        self.assertEqual(snap["date_almaty"], "2026-10-28")
        self.assertEqual(snap["account"]["followers_count"], 4)
        self.assertEqual(len(snap["media"]), 1)
        self.assertNotIn("caption", snap["media"][0])
        self.assertNotIn("access_token", json.dumps(snap))

    def test_wrong_account_cannot_be_saved(self):
        class WrongAccount(FakeInstagram):
            def profile(self):
                return {"username": "different.account", "followers_count": 5}
        with self.assertRaisesRegex(InstagramError, "Wrong authorized"):
            collect_snapshot(WrongAccount())

    def test_idempotent_daily_overwrite_and_trend(self):
        snap = collect_snapshot(FakeInstagram(), datetime(2026, 10, 28, 0, 15, tzinfo=timezone.utc))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            one = persist_snapshot(snap, root)
            two = persist_snapshot(snap, root)
            self.assertEqual(one, two)
            self.assertEqual(len(list(root.glob("*.json"))), 1)
            self.assertEqual(len(history(root)), 1)
            self.assertIn("Подписчиков сейчас: 4", render_trend(root))

    def test_github_commit_updates_existing_date(self):
        calls = []
        def mock_transport(request, timeout):
            self.assertIsInstance(request, Request)
            calls.append(request)
            if request.get_method() == "GET":
                return FakeResponse({"sha": "existing_blob_sha"})
            payload = json.loads(request.data)
            self.assertEqual(payload["sha"], "existing_blob_sha")
            saved = json.loads(base64.b64decode(payload["content"]))
            self.assertEqual(saved["account"]["username"], "mybrand")
            self.assertEqual(saved["date_almaty"], "2026-10-28")
            return FakeResponse({"commit": {"sha": "new_commit_sha"}})
        snap = collect_snapshot(FakeInstagram(), datetime(2026, 10, 28, tzinfo=timezone.utc))
        path = commit_github_snapshot(snap, repository="YOUR_GITHUB_LOGIN/insta-engine",
                                      token="test-token", transport=mock_transport)
        self.assertEqual(path, "data/snapshots/2026-10-28.json")
        self.assertEqual([r.get_method() for r in calls], ["GET", "PUT"])
        self.assertNotIn("test-token", calls[1].full_url)


if __name__ == "__main__":
    unittest.main()
