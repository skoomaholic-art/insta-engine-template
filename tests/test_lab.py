import unittest
from datetime import datetime, timedelta, timezone

from igm import lab


def post(pid, fmt, genre, freshness, views, saved=0, shares=0, hours=80, kind="auto"):
    now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
    return {"id": pid, "status": "published", "source": {"kind": kind},
            "draft": {"format": fmt, "slides": [{"title": pid}] * (1 if fmt == "reel" else 2), "tags": {"genres": [genre], "freshness": freshness, "angle": "мнение", "hook": "вопрос"}},
            "ig": {"media_id": "1" + pid[1:], "published_at": (now - timedelta(hours=hours)).isoformat(), "type": "REELS" if fmt == "reel" else "CAROUSEL"},
            "metrics": {"h24": {"views": views, "reach": views, "saved": saved, "shares": shares}}}


class FakeAPI:
    def __init__(self, fail=False):
        self.fail = fail

    def media_insights(self, media_id, media_type=""):
        if self.fail:
            raise RuntimeError("no permission")
        return {"views": 1000, "reach": 800, "saved": 10, "shares": 5}

    def media_basic(self, media_id):
        return {"likes": 12, "comments": 3}


class LabTests(unittest.TestCase):
    def test_checkpoints_filled_by_age(self):
        now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
        p = post("P0001", "reel", "драма", "новинка", 0, hours=80)
        p.pop("metrics")
        state = {"posts": [p, {"id": "P0002", "status": "text_review"}]}
        n, src = lab.collect(state, FakeAPI(), now)
        self.assertEqual(n, 1)
        self.assertEqual(set(p["metrics"]), {"h24", "h72", "latest"})
        self.assertEqual(p["metrics"]["h24"]["views"], 1000)

    def test_fallback_to_basic_counters(self):
        p = post("P0001", "reel", "драма", "новинка", 0, hours=30)
        p.pop("metrics")
        lab.collect({"posts": [p]}, FakeAPI(fail=True), datetime(2026, 11, 10, 12, tzinfo=timezone.utc))
        self.assertEqual(p["metrics"]["h24"]["likes"], 12)
        self.assertEqual(p["metrics"]["h24"]["source"], "basic")

    def test_segments_need_enough_posts(self):
        posts = [post(f"P000{i}", "reel", "драма", "новинка", 3000) for i in range(1, 4)]
        posts += [post(f"P001{i}", "portrait", "комедия", "классика", 500) for i in range(1, 4)]
        posts += [post("P0021", "portrait", "ужасы", "классика", 900)]
        res = lab.analyze({"posts": posts})
        fmt = {s["value"]: s for s in res["segments"]["format"]}
        self.assertEqual(fmt["Reels"]["verdict"], "лидер")
        self.assertEqual(fmt["Карусель"]["verdict"], "аутсайдер")
        genre = {s["value"]: s for s in res["segments"]["genre"]}
        self.assertEqual(genre["ужасы"]["verdict"], "мало данных")
        self.assertEqual(res["posts_measured"], 7)
        self.assertEqual(res["top_posts"][0]["id"], "P0001")

    def test_saves_and_shares_boost_score(self):
        self.assertGreater(lab.score({"views": 1000, "saved": 50}), lab.score({"views": 1000}))
        self.assertEqual(lab.score({"likes": 10, "comments": 2}), 16)
        self.assertIsNone(lab.score({}))


if __name__ == "__main__":
    unittest.main()
