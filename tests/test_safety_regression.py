"""Offline safety regressions for the existing IGM post pipeline.

Only temporary directories and fake Meta clients are used.
No GitHub changes, Telegram messages, real media URLs, or Instagram publishing.
"""
import json
import tempfile
import unittest
from pathlib import Path

from igm import pipeline as pl


def post(status, pid="P1001", fmt="portrait"):
    return {
        "id": pid,
        "status": status,
        "history": [],
        "approvals": {
            "text": {"by": "test-owner", "at": "2026-01-01T00:00:00Z"},
            "final": {"by": "test-owner", "at": "2026-01-01T00:05:00Z"},
        },
        "draft": {
            "format": fmt,
            "caption": "Тестовый черновик. НЕ публиковать",
            "slides": [
                {"title": "Обложка", "subtitle": "Тест"},
                {"title": "Тезис", "subtitle": "Тест"},
            ],
        },
        "media": {"images": [
            f"media/posts/{pid}/1.jpg", f"media/posts/{pid}/2.jpg"
        ], "video": None},
        "ig": None,
    }


class OfflineMeta:
    """A spy: all calls are in-memory and never reach a network."""
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def publish_carousel(self, urls, caption, sleep=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("unknown Meta outcome")
        return {"media_id": "999", "permalink": None}

    def publish_single_image(self, url, caption, sleep=None):
        return self.publish_carousel([url], caption, sleep)

    def publish_reel(self, url, caption, cover_url=None, sleep=None):
        return self.publish_carousel([url], caption, sleep)


class SafePipelineRegression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.commits = []
        self.notifications = []
        self.env = {
            "PIPELINE_SECRET": "test-only-secret-" + "s"*32,
            "IGM_ENABLE_PUBLISH": "true",
        }

    def write(self, posts):
        pl.save_state({"schema_version": 1, "posts": posts}, self.root / pl.POSTS_PATH)

    def read(self):
        return pl.load_state(self.root / pl.POSTS_PATH)["posts"]

    def execute(self, api):
        return pl.run(
            env=self.env, root=self.root,
            api_factory=lambda: api,
            renderer=lambda url, item: [b"fake-jpeg"],
            video_maker=lambda images, out: Path(out).write_bytes(b"fake-video"),
            commit=lambda message, paths: self.commits.append(message),
            notifier=lambda base, secret, pid, event: self.notifications.append(event),
            sleep=lambda seconds: None,
        )

    def test_immutable_outcomes_are_never_processed_or_published(self):
        statuses = ["published", "rejected", "final_review", "failed", "publishing_in_progress"]
        posts = [post(status, f"P{1001+i}") for i, status in enumerate(statuses)]
        posts[0]["ig"] = {"media_id": "111", "permalink": "https://www.instagram.com/p/test/"}
        self.write(posts)
        before = (self.root / pl.POSTS_PATH).read_bytes()
        spy = OfflineMeta()
        self.assertEqual(pl.needs_work(root=self.root), [])
        self.assertEqual(self.execute(spy), [])
        self.assertEqual(spy.calls, 0)
        self.assertEqual(self.commits, [])
        self.assertEqual(self.notifications, [])
        self.assertEqual((self.root / pl.POSTS_PATH).read_bytes(), before)

    def test_unknown_meta_outcome_blocks_automatic_second_attempt(self):
        item = post("publishing")
        self.write([item])
        spy = OfflineMeta(fail=True)
        self.execute(spy)
        self.assertEqual(spy.calls, 1)
        self.assertEqual(self.read()[0]["status"], "failed")
        self.assertEqual(self.read()[0]["error"]["stage"], "publish")
        self.assertEqual(self.commits[0], "pipeline: publishing P1001")
        self.execute(spy)  # repeat job with same on-disk state
        self.assertEqual(spy.calls, 1)
        self.assertEqual(self.notifications, ["failed"])

    def test_render_replaces_only_generated_jpg_preserving_owner_assets(self):
        item = post("rendering")
        folder = self.root / "media/posts/P1001"
        folder.mkdir(parents=True)
        owned = {
            "src-own-photo.jpg": b"owner-original",
            "bg-from-owner.png": b"owner-background",
            "bg-db-123abc.jpg": b"chosen-background",
        }
        for name, content in owned.items():
            (folder / name).write_bytes(content)
        (folder / "1.jpg").write_bytes(b"old-generated")
        pl.render_post(item, "https://example.invalid", root=self.root,
                       renderer=lambda url, p: [b"jpeg-one", b"jpeg-two"],
                       video_maker=lambda images, out: None)
        self.assertEqual(item["status"], "final_review")
        self.assertEqual((folder / "1.jpg").read_bytes(), b"jpeg-one")
        self.assertEqual((folder / "2.jpg").read_bytes(), b"jpeg-two")
        for name, content in owned.items():
            self.assertEqual((folder / name).read_bytes(), content)
        self.assertEqual(item["approvals"]["final"]["by"], "test-owner")
        # Render only sets final_review; this isolated function never publishes.

    def test_publish_without_final_owner_approval_fails_before_any_meta_call(self):
        item=post("publishing")
        item["approvals"].pop("final")
        self.write([item])
        spy=OfflineMeta()
        self.execute(spy)
        self.assertEqual(spy.calls,0)
        self.assertEqual(self.read()[0]["status"],"failed")
        self.assertIn("двух согласований", self.read()[0]["error"]["message"])


if __name__ == "__main__":
    unittest.main()
