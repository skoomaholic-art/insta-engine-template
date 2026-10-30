import json
import tempfile
import unittest
from pathlib import Path

from igm import pipeline as pl


def make_post(status="rendering", fmt="portrait", n=3, approvals=True):
    post = {"id": "P0001", "status": status, "history": [],
            "draft": {"format": fmt, "caption": "Подпись", "slides": [{"title": "T", "subtitle": "S"}] * n}}
    if approvals:
        post["approvals"] = {"text": {"by": "42", "at": "t"}, "final": {"by": "42", "at": "t"}}
    return post


class FakeAPI:
    def __init__(self):
        self.calls = []

    def publish_carousel(self, urls, caption, sleep=None, on_container=None):
        self.calls.append(("carousel", urls, caption))
        return {"media_id": "111", "permalink": "https://instagram.com/p/x"}

    def publish_single_image(self, url, caption, sleep=None, on_container=None):
        self.calls.append(("image", url, caption))
        return {"media_id": "112"}

    def publish_reel(self, url, caption, cover_url=None, sleep=None, on_container=None):
        self.calls.append(("reel", url, cover_url))
        return {"media_id": "113"}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.commits, self.notes = [], []

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, *posts):
        pl.save_state({"schema_version": 1, "posts": list(posts)}, self.root / pl.POSTS_PATH)

    def state(self):
        return pl.load_state(self.root / pl.POSTS_PATH)

    def run_pl(self, env=None, api=None, renderer=None):
        env = {"PIPELINE_SECRET": "s" * 32, "IGM_ENABLE_PUBLISH": "true", **(env or {})}
        return pl.run(env=env, root=self.root, api_factory=lambda: api or FakeAPI(),
                      renderer=renderer or (lambda url, post: [b"\xff\xd8jpg"] * len(post["draft"]["slides"])),
                      video_maker=lambda imgs, out: Path(out).write_bytes(b"mp4"),
                      commit=lambda msg, paths: self.commits.append(msg),
                      notifier=lambda base, secret, pid, event: self.notes.append(event), sleep=lambda s: None)

    def test_render_creates_slides_and_waits_for_final_approval(self):
        self.write(make_post("rendering", "portrait", 3))
        self.run_pl()
        post = self.state()["posts"][0]
        self.assertEqual(post["status"], "final_review")
        self.assertEqual(len(post["media"]["images"]), 3)
        self.assertTrue((self.root / "media/posts/P0001/3.jpg").exists())
        self.assertEqual(self.notes, ["final_ready"])

    def test_reel_gets_video(self):
        self.write(make_post("rendering", "reel", 1))
        self.run_pl()
        self.assertEqual(self.state()["posts"][0]["media"]["video"], "media/posts/P0001/reel.mp4")

    def test_publish_carousel_with_signed_urls_and_lock(self):
        post = make_post("publishing", "portrait", 3)
        post["media"] = {"images": [f"media/posts/P0001/{i}.jpg" for i in (1, 2, 3)], "video": None}
        self.write(post)
        api = FakeAPI()
        self.run_pl(api=api)
        kind, urls, caption = api.calls[0]
        self.assertEqual(kind, "carousel")
        self.assertIn("/api/media?p=media/posts/P0001/1.jpg&e=", urls[0])
        self.assertEqual(self.state()["posts"][0]["status"], "published")
        self.assertEqual(self.commits[0], "pipeline: publishing P0001")  # lock committed before API call
        self.assertEqual(self.notes, ["published"])

    def test_no_publish_without_both_approvals_or_flag(self):
        post = make_post("publishing", approvals=False)
        post["media"] = {"images": ["a.jpg", "b.jpg"]}
        self.write(post)
        api = FakeAPI()
        self.run_pl(api=api)
        self.assertEqual(api.calls, [])
        self.assertEqual(self.state()["posts"][0]["status"], "failed")
        post = make_post("publishing")
        post["media"] = {"images": ["a.jpg", "b.jpg"]}
        self.write(post)
        self.run_pl(env={"IGM_ENABLE_PUBLISH": "false"}, api=api)
        self.assertEqual(api.calls, [])
        self.assertIn("IGM_ENABLE_PUBLISH", self.state()["posts"][0]["error"]["message"])

    def test_in_progress_lock_is_never_republished(self):
        post = make_post("publishing_in_progress")
        post["media"] = {"images": ["a.jpg", "b.jpg"]}
        self.write(post)
        api = FakeAPI()
        self.run_pl(api=api)
        self.assertEqual(api.calls, [])

    def test_render_failure_is_reported(self):
        self.write(make_post("rendering"))
        def boom(url, post):
            raise RuntimeError("studio down")
        self.run_pl(renderer=boom)
        post = self.state()["posts"][0]
        self.assertEqual(post["status"], "failed")
        self.assertEqual(post["error"]["stage"], "render")
        self.assertEqual(self.notes, ["failed"])

    def test_hook_signature_vector(self):
        self.assertEqual(pl.hook_signature("P0001", "final_ready", 1700000000, "secret"),
                         __import__("hmac").new(b"secret", b"P0001:final_ready:1700000000", "sha256").hexdigest())

    def test_signature_matches_known_vector(self):
        # Same vector is checked in the Vercel /api/media tests
        self.assertEqual(pl.sign("media/posts/P0001/1.jpg", 1700000000, "secret"), "f7f72bbba5fa45c479ebdf23f7d49c8e0d423258")

if __name__ == "__main__":
    unittest.main()
