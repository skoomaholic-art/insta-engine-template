"""Регрессии по аудиту пайплайна: гонки с ботом, двойная публикация, зависшие посты, alert-события.

Только временные каталоги, локальные git-репозитории и фейковые клиенты Meta — без сети и публикаций.
"""
import hmac
import importlib.util
import io
import json
import os
import socket
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from igm import editor_call, lab, pipeline as pl
from igm.instagram import InstagramAPI, InstagramError
from igm.reel import scene_durations

REPO = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat(timespec="seconds")


def ready_post(pid="P0001", status="publishing", n=2):
    return {"id": pid, "status": status, "history": [],
            "approvals": {"text": {"by": "42", "at": "2026-01-01T00:00:00+00:00"}, "final": {"by": "42", "at": "2026-01-01T00:10:00+00:00"}},
            "draft": {"format": "portrait", "caption": "Подпись поста", "slides": [{"title": "T"}] * n},
            "media": {"images": [f"media/posts/{pid}/{i}.jpg" for i in range(1, n + 1)], "video": None,
                      "rendered_at": "2026-01-01T00:05:00+00:00"}}


class MetaSpy:
    """Фейковый Meta: публикация может «потерять ответ» после сохранения контейнера."""

    def __init__(self, lose_response=False, fail=None, container="PUBLISHED", recent=None, recent_fails=False):
        self.calls, self.lose_response, self.fail = 0, lose_response, fail
        self.container, self.recent, self.recent_fails = container, recent, recent_fails

    def publish_carousel(self, urls, caption, sleep=None, on_container=None):
        self.calls += 1
        if self.fail:
            raise self.fail
        if on_container:
            on_container("555")
        if self.lose_response:
            raise InstagramError("Network request failed (TimeoutError)", http_status=-1)
        return {"media_id": "777", "container_id": "555", "permalink": "https://instagram.com/p/new"}

    publish_single_image = lambda self, url, caption, sleep=None, on_container=None: self.publish_carousel([url], caption, sleep, on_container)

    def container_status(self, cid):
        if self.container is None:
            raise InstagramError("expired", code=100)
        return self.container

    def recent_media(self, limit=25):
        if self.recent_fails:
            raise InstagramError("down", http_status=500)
        return self.recent if self.recent is not None else []


def match(caption="Подпись поста", at=None):
    return [{"id": "888", "caption": caption, "permalink": "https://instagram.com/p/found",
             "timestamp": (at or NOW).strftime("%Y-%m-%dT%H:%M:%S+0000")}]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.commits, self.notes, self.alerts = [], [], []

    def write(self, *posts):
        pl.save_state({"schema_version": 1, "posts": list(posts)}, self.root / pl.POSTS_PATH)

    def posts(self):
        return {p["id"]: p for p in pl.load_state(self.root / pl.POSTS_PATH)["posts"]}

    def run_pl(self, api, renderer=None, env=None, now=None):
        env = {"PIPELINE_SECRET": "s" * 32, "IGM_ENABLE_PUBLISH": "true", **(env or {})}
        return pl.run(env=env, root=self.root, api_factory=lambda: api,
                      renderer=renderer or (lambda url, post: [b"jpg"] * len(post["draft"]["slides"])),
                      video_maker=lambda imgs, out: Path(out).write_bytes(b"mp4"),
                      commit=lambda msg, paths: self.commits.append(msg),
                      notifier=lambda base, secret, pid, event: self.notes.append(event),
                      alerter=lambda text: self.alerts.append(text) or True, sleep=lambda s: None, now=now)


class DoublePublishTests(Base):
    def test_lost_response_is_reconciled_not_failed(self):
        self.write(ready_post())
        api = MetaSpy(lose_response=True, container="PUBLISHED", recent=match())
        self.run_pl(api)
        post = self.posts()["P0001"]
        self.assertEqual(api.calls, 1)
        self.assertEqual(post["status"], "published")
        self.assertEqual(post["ig"]["media_id"], "888")
        self.assertNotIn("ig_pending", post)
        self.assertEqual(self.commits[:2], ["pipeline: publishing P0001", "pipeline: container P0001"])
        self.assertEqual(self.notes, ["published"])

    def test_uncertain_outcome_then_owner_retry_does_not_publish_twice(self):
        self.write(ready_post())
        api = MetaSpy(lose_response=True, container=None, recent_fails=True)
        self.run_pl(api)
        post = self.posts()["P0001"]
        self.assertEqual(post["status"], "failed")
        self.assertEqual(post["error"]["stage"], "publish_uncertain")
        self.assertEqual(post["ig_pending"]["container_id"], "555")
        self.assertTrue(self.alerts)
        # владелец нажал «Повторить» — бот вернул статус publishing; пост на самом деле вышел
        post["status"] = "publishing"
        self.write(post)
        api2 = MetaSpy(container="PUBLISHED", recent=match())
        self.run_pl(api2)
        self.assertEqual(api2.calls, 0)
        self.assertEqual(self.posts()["P0001"]["status"], "published")
        self.assertEqual(self.posts()["P0001"]["ig"]["media_id"], "888")

    def test_retry_publishes_when_container_not_published(self):
        post = ready_post()
        post["ig_pending"] = {"container_id": "555", "at": iso(NOW - timedelta(hours=1))}
        post["error"] = {"stage": "publish_uncertain", "message": "x", "at": "t"}
        self.write(post)
        api = MetaSpy(container="FINISHED", recent=[])
        self.run_pl(api)
        self.assertEqual(api.calls, 1)
        self.assertEqual(self.posts()["P0001"]["ig"]["media_id"], "777")

    def test_retry_refused_when_outcome_cannot_be_checked(self):
        post = ready_post()
        post["ig_pending"] = {"container_id": "555", "at": iso(NOW - timedelta(hours=1))}
        self.write(post)
        api = MetaSpy(container=None, recent_fails=True)
        self.run_pl(api)
        self.assertEqual(api.calls, 0)
        self.assertEqual(self.posts()["P0001"]["status"], "failed")

    def test_token_expired_sends_alert(self):
        self.write(ready_post())
        self.run_pl(MetaSpy(fail=InstagramError("Meta API returned HTTP 400; error code 190, subcode 463", code=190, subcode=463, http_status=400)))
        self.assertEqual(self.posts()["P0001"]["error"]["stage"], "publish")
        self.assertEqual(self.alerts, [pl.TOKEN_EXPIRED_TEXT])
        self.assertLessEqual(len(pl.TOKEN_EXPIRED_TEXT), 500)


class RenderBindingTests(Base):
    def test_final_approval_bound_to_render_id(self):
        post = ready_post()
        post["approvals"]["final"]["rid"] = "2025-12-31T00:00:00+00:00"  # согласован старый рендер
        self.write(post)
        api = MetaSpy()
        self.run_pl(api)
        self.assertEqual(api.calls, 0)
        self.assertIn("rid", self.posts()["P0001"]["error"]["message"])
        self.assertEqual(self.notes, ["failed"])

    def test_final_approval_older_than_render_refused(self):
        post = ready_post()
        post["approvals"]["final"]["at"] = "2026-01-01T00:01:00+00:00"
        self.write(post)
        api = MetaSpy()
        self.run_pl(api)
        self.assertEqual(api.calls, 0)
        self.assertEqual(self.posts()["P0001"]["status"], "failed")

    def test_matching_rid_publishes(self):
        post = ready_post()
        post["approvals"]["final"]["rid"] = post["media"]["rendered_at"]
        self.write(post)
        api = MetaSpy()
        self.run_pl(api)
        self.assertEqual(api.calls, 1)
        self.assertEqual(self.posts()["P0001"]["status"], "published")


class StuckTests(Base):
    def stuck(self, minutes, **extra):
        post = ready_post(status="publishing_in_progress")
        at = iso(NOW - timedelta(minutes=minutes))
        post["history"] = [{"at": at, "event": "publish_started", "by": "pipeline"}]
        post["updated_at_utc"] = at
        post.update(extra)
        return post

    def test_fresh_lock_untouched(self):
        self.write(self.stuck(5))
        self.assertEqual(pl.work_plan(self.root), {})

    def test_stuck_publication_found_in_instagram(self):
        self.write(self.stuck(25))
        self.assertEqual(pl.work_plan(self.root), {"P0001": "reconcile"})
        api = MetaSpy(recent=match(at=NOW - timedelta(minutes=24)))
        self.run_pl(api)
        self.assertEqual(api.calls, 0)
        self.assertEqual(self.posts()["P0001"]["status"], "published")
        self.assertEqual(self.notes, ["published"])

    def test_stuck_publication_not_found_fails_with_alert(self):
        self.write(self.stuck(25))
        api = MetaSpy(container=None, recent=[])
        self.run_pl(api)
        post = self.posts()["P0001"]
        self.assertEqual(api.calls, 0)
        self.assertEqual(post["status"], "failed")
        self.assertEqual(post["error"]["stage"], "publish_uncertain")
        self.assertEqual(len(self.alerts), 1)

    def test_stuck_render_retried_once_then_failed(self):
        post = ready_post(status="rendering")
        post["updated_at_utc"] = iso(NOW - timedelta(minutes=40))
        self.write(post)
        self.run_pl(MetaSpy())
        self.assertEqual(self.commits, ["pipeline: re-render P0001", "pipeline: render P0001"])
        self.assertEqual(self.posts()["P0001"]["status"], "final_review")
        self.assertNotIn("render_retry", self.posts()["P0001"])
        # повторный рендер тоже завис (запуск оборвался): второй раз не пробуем
        post = ready_post(status="rendering")
        stamp = iso(NOW - timedelta(minutes=40))
        post["updated_at_utc"] = post["render_retry"] = stamp
        self.write(post)
        self.commits, self.notes = [], []
        self.run_pl(MetaSpy(), renderer=lambda url, p: self.fail("must not render again"))
        self.assertEqual(self.posts()["P0001"]["status"], "failed")
        self.assertEqual(self.notes, ["failed"])
        self.assertEqual(len(self.alerts), 1)


class WorkPlanTests(Base):
    def test_kinds_for_heavy_install_skip(self):
        due = ready_post("P0002", status="scheduled")
        due["scheduled_at"] = iso(NOW - timedelta(minutes=1))
        self.write(ready_post("P0001", status="rendering"), due, ready_post("P0003", status="final_review"))
        self.assertEqual(pl.work_plan(self.root), {"P0001": "render", "P0002": "publish"})
        self.assertEqual(pl.needs_work(self.root), ["P0001", "P0002"])

    def test_robot_never_commits_kick(self):
        with self.assertRaises(pl.PipelineError):
            pl.git_commit_push("x", [str(pl.KICK_PATH)])

    def test_main_fails_early_without_secret(self):
        out = io.StringIO()
        with patch.dict(os.environ, {"PIPELINE_SECRET": ""}), patch("sys.argv", ["pipeline", "run"]), redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                pl.main()
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("::error::PIPELINE_SECRET", out.getvalue())


class FakeResponse:
    def __init__(self, data, status=200):
        self.data, self.status = data, status

    def read(self, *a):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class StreamResponse(io.BytesIO):
    """Потоковый ответ (read(n) заканчивается на b"") — как у urlopen."""


class NotifyTests(unittest.TestCase):
    def test_alert_event_signed_and_text_limited(self):
        seen = {}

        def transport(req, timeout):
            seen["body"], seen["sig"] = json.loads(req.data), req.get_header("X-pipeline-signature")
            return FakeResponse(b"{}")
        self.assertTrue(pl.send_alert("https://x.example", "secret", "я" * 900, transport=transport))
        body = seen["body"]
        self.assertEqual((body["id"], body["event"]), ("P0000", "alert"))
        self.assertEqual(len(body["text"]), 500)
        expected = hmac.new(b"secret", f"P0000:alert:{body['ts']}".encode(), "sha256").hexdigest()
        self.assertEqual(seen["sig"], expected)

    def test_notify_logs_http_status(self):
        def transport(req, timeout):
            raise HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b""))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertFalse(pl.notify("https://x.example", "secret", "P0001", "published", transport=transport))
            self.assertFalse(pl.notify("https://x.example", "", "P0001", "published", transport=transport))
        self.assertIn("notify failed: published P0001: HTTP 401", out.getvalue())
        self.assertIn("PIPELINE_SECRET", out.getvalue())


class InstagramErrorTests(unittest.TestCase):
    def api(self, responses):
        responses = list(responses)

        def transport(req, timeout):
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return FakeResponse(json.dumps(item).encode())
        with patch.dict(os.environ, {"IG_AUTH_MODE": "instagram"}):
            return InstagramAPI(account_id="1", access_token="t", transport=transport), responses

    @staticmethod
    def http(code, body):
        return HTTPError("https://graph.instagram.com/x", code, "err", {}, io.BytesIO(json.dumps(body).encode()))

    def test_codes_parsed_into_fields(self):
        api, _ = self.api([self.http(400, {"error": {"code": 190, "error_subcode": 463}})])
        with self.assertRaises(InstagramError) as ctx:
            api.request("me")
        self.assertEqual((ctx.exception.code, ctx.exception.subcode, ctx.exception.http_status), (190, 463, 400))
        self.assertTrue(ctx.exception.token_expired)
        api, _ = self.api([{"error": {"code": 10}}])
        with self.assertRaises(InstagramError) as ctx:
            api.request("me")
        self.assertEqual(ctx.exception.code, 10)

    def test_insights_permission_error_by_code_not_substring(self):
        api, left = self.api([{"error": {"code": 10}}])
        with self.assertRaises(InstagramError):
            api.media_insights("123", "REELS")
        self.assertEqual(left, [])  # не стал перебирать метрики

    def test_wait_container_tolerates_transient_5xx_and_knows_published(self):
        api, _ = self.api([self.http(502, {}), {"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED"}])
        self.assertEqual(api.wait_container("5", sleep=lambda s: None), "FINISHED")
        api, _ = self.api([{"status_code": "PUBLISHED"}])
        self.assertEqual(api.wait_container("5", sleep=lambda s: None), "PUBLISHED")
        api, _ = self.api([self.http(500, {})] * 4)
        with self.assertRaises(InstagramError):
            api.wait_container("5", sleep=lambda s: None)
        api, _ = self.api([self.http(400, {"error": {"code": 100}})])
        with self.assertRaises(InstagramError):
            api.wait_container("5", sleep=lambda s: None)

    def test_already_published_container_is_not_published_again(self):
        saved = []
        api, left = self.api([{"username": "mybrand"}, {"id": "55"}, {"status_code": "PUBLISHED"}, {"id": "999"}])
        with patch.object(api, "expected_username", "mybrand"):
            with self.assertRaisesRegex(InstagramError, "PUBLISHED"):
                api.publish_single_image("https://x.example/1.jpg", "c", sleep=lambda s: None, on_container=saved.append)
        self.assertEqual(saved, [])
        self.assertEqual(len(left), 1)  # media_publish не вызывался

    def test_container_saved_before_media_publish(self):
        order = []
        api, _ = self.api([{"username": "mybrand"}, {"id": "55"}, {"status_code": "FINISHED"}, {"id": "999"}, {"permalink": "p"}])
        real = api.request
        api.request = lambda path, **kw: (order.append(path), real(path, **kw))[1]
        with patch.object(api, "expected_username", "mybrand"):
            res = api.publish_single_image("https://x.example/1.jpg", "c", sleep=lambda s: None, on_container=lambda cid: order.append(f"saved:{cid}"))
        self.assertEqual(res["media_id"], "999")
        self.assertLess(order.index("saved:55"), order.index("1/media_publish"))


class LabAlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        pl.save_state({"schema_version": 1, "posts": []}, self.root / pl.POSTS_PATH)
        self.alerts = []

    def run_lab(self, now, env):
        return lab.run(root=self.root, now=now, env=env, alerter=lambda text: self.alerts.append(text) or True)

    def test_token_age_warning_from_day_50_once_a_day(self):
        issued = datetime(2026, 9, 1, tzinfo=timezone.utc)
        env = {"IG_TOKEN_ISSUED_AT": "2026-09-01"}
        self.run_lab(issued + timedelta(days=49), env)
        self.assertEqual(self.alerts, [])
        day50 = issued + timedelta(days=50, hours=3)
        self.run_lab(day50, env)
        self.run_lab(day50 + timedelta(hours=6), env)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("IG_ACCESS_TOKEN", self.alerts[0])
        self.assertLessEqual(len(self.alerts[0]), 500)
        self.run_lab(day50 + timedelta(days=1), env)
        self.assertEqual(len(self.alerts), 2)
        saved = json.loads((self.root / lab.LAB_PATH).read_text(encoding="utf-8"))
        self.assertIn("token_age", saved["alerts"])

    def test_no_issue_date_no_warning(self):
        self.run_lab(datetime(2026, 12, 1, tzinfo=timezone.utc), {})
        self.assertEqual(self.alerts, [])

    def test_code_190_in_lab_alerts(self):
        published = {"id": "P0001", "status": "published", "draft": {},
                     "ig": {"media_id": "123", "published_at": "2026-11-09T10:00:00+00:00", "type": "REELS"}}
        pl.save_state({"schema_version": 1, "posts": [published]}, self.root / pl.POSTS_PATH)

        class Expired:
            def media_insights(self, *a):
                raise InstagramError("token", code=190)
            media_basic = media_insights
        lab.run(root=self.root, api_factory=Expired, now=datetime(2026, 11, 10, 12, tzinfo=timezone.utc), env={},
                alerter=lambda text: self.alerts.append(text) or True)
        self.assertEqual(self.alerts, [pl.TOKEN_EXPIRED_TEXT])

    def test_deleted_media_by_subcode_field(self):
        def make():
            return {"id": "P0001", "status": "published", "draft": {},
                    "ig": {"media_id": "123", "published_at": "2026-11-09T10:00:00+00:00", "type": "REELS"}}

        class Gone:
            def __init__(self, sub):
                self.sub = sub

            def media_insights(self, *a):
                raise InstagramError("x", code=100, subcode=self.sub)
            media_basic = media_insights
        for sub, missing in ((33, True), (330, None)):
            p = make()
            lab.collect({"posts": [p]}, Gone(sub), datetime(2026, 11, 10, 12, tzinfo=timezone.utc))
            self.assertEqual(p["ig"].get("missing"), missing)


class ReelLengthTests(unittest.TestCase):
    def test_single_scene_reel_at_least_3_2_seconds(self):
        for style in ("dynamic", "thriller", "epic", "calm"):
            d = scene_durations([{"title": "Один"}], style)
            self.assertGreaterEqual(sum(d), 3.2, style)


class EditorCallTests(unittest.TestCase):
    env = {"PIPELINE_BASE_URL": "https://x.example", "PIPELINE_SECRET": "s"}

    def call(self, transport):
        out = io.StringIO()
        with redirect_stdout(out):
            code = editor_call.main(transport=transport, env=self.env)
        return code, out.getvalue()

    def test_timeout_is_warning(self):
        def transport(req, timeout):
            raise URLError(socket.timeout("timed out"))
        code, out = self.call(transport)
        self.assertEqual(code, 0)
        self.assertIn("::warning::", out)

    def test_server_error_annotated(self):
        def transport(req, timeout):
            raise HTTPError(req.full_url, 500, "err", {}, io.BytesIO(b'{"error": "OpenAI quota"}'))
        code, out = self.call(transport)
        self.assertEqual(code, 1)
        self.assertIn("::error::", out)
        self.assertIn("HTTP 500", out)
        self.assertIn("OpenAI quota", out)

    def test_refusal_and_success(self):
        code, out = self.call(lambda req, timeout: FakeResponse(b'{"ok": false, "error": "limit"}'))
        self.assertEqual(code, 1)
        self.assertIn("::error::", out)
        code, out = self.call(lambda req, timeout: FakeResponse(b'{"ok": true, "post": "P0009"}'))
        self.assertEqual(code, 0)
        self.assertIn("::notice::", out)


@unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy ставится только для рендера")
class ParallaxDownloadTests(unittest.TestCase):
    def test_partial_download_not_cached_and_not_retried(self):
        from igm import parallax
        calls = []

        def opener(url, timeout):
            calls.append(timeout)
            return StreamResponse(b"x" * 10)
        with tempfile.TemporaryDirectory() as d, patch.object(parallax, "_DOWNLOAD_FAILED", None):
            target = Path(d) / "m.onnx"
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    parallax.ensure_model(target, opener=opener)
            self.assertEqual(calls, [parallax.DOWNLOAD_TIMEOUT])  # вторая сцена не перекачивает
            self.assertFalse(target.exists())
            self.assertFalse(target.with_name("m.onnx.part").exists())

    def test_complete_download_renamed(self):
        from igm import parallax
        with tempfile.TemporaryDirectory() as d, patch.object(parallax, "_DOWNLOAD_FAILED", None), patch.object(parallax, "MODEL_MIN_BYTES", 100):
            target = Path(d) / "m.onnx"
            target.write_bytes(b"broken")  # испорченный кэш выбрасывается
            parallax.ensure_model(target, opener=lambda url, timeout: StreamResponse(b"x" * 200))
            self.assertEqual(target.stat().st_size, 200)


class WorkflowTests(unittest.TestCase):
    def read(self, name):
        return (REPO / ".github/workflows" / name).read_text(encoding="utf-8")

    def test_pipeline_triggered_by_kick_only(self):
        wf = self.read("pipeline.yml")
        self.assertIn("paths: ['data/pipeline/kick.json']", wf)
        self.assertNotIn("paths: ['data/pipeline/posts.json']", wf)
        self.assertIn("cron: '17 3-18 * * *'", wf)
        self.assertIn("workflow_dispatch", wf)
        self.assertIn("onnxruntime==", wf)
        self.assertIn("steps.check.outputs.render == 'yes'", wf)

    def test_side_workflows_do_not_share_pipeline_group(self):
        for name in ("lab.yml", "music-library.yml"):
            self.assertNotIn("group: igm-pipeline", self.read(name), name)


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@unittest.skipUnless(subprocess.run(["git", "--version"], capture_output=True).returncode == 0, "нужен git")
class GitRaceTests(unittest.TestCase):
    """Реальный git: бот пушит одновременно с роботом (как в Actions)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        ident = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        patcher = patch.dict(os.environ, ident)
        patcher.start()
        self.addCleanup(patcher.stop)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(t / "remote.git")], check=True)
        _git(t, "clone", "-q", str(t / "remote.git"), "robot")
        self.robot, self.bot = t / "robot", t / "bot"
        p1 = ready_post("P0001", status="publishing")
        p2 = {"id": "P0002", "status": "text_review", "history": [], "note": "a\nb\nc",
              "draft": {"caption": "Другой пост", "format": "portrait", "slides": [{"title": "x"}]}}
        pl.save_state({"schema_version": 1, "posts": [p1, p2]}, self.robot / pl.POSTS_PATH)
        _git(self.robot, "checkout", "-q", "-b", "main")
        _git(self.robot, "add", ".")
        _git(self.robot, "commit", "-qm", "init")
        _git(self.robot, "push", "-q", "origin", "HEAD:main")
        _git(t, "clone", "-q", "-b", "main", str(t / "remote.git"), "bot")
        cwd = os.getcwd()
        os.chdir(self.robot)
        self.addCleanup(os.chdir, cwd)

    def bot_edit(self, fn):
        path = self.bot / pl.POSTS_PATH
        state = pl.load_state(path)
        fn({p["id"]: p for p in state["posts"]})
        pl.save_state(state, path)
        _git(self.bot, "commit", "-qam", "bot edit")
        _git(self.bot, "push", "-q", "origin", "HEAD:main")

    def remote_posts(self):
        _git(self.bot, "pull", "-q", "--rebase", "origin", "main")
        return {p["id"]: p for p in pl.load_state(self.bot / pl.POSTS_PATH)["posts"]}

    def run_robot(self, api):
        return pl.run(env={"PIPELINE_SECRET": "s" * 32, "IGM_ENABLE_PUBLISH": "true"}, root=".", api_factory=lambda: api,
                      notifier=lambda *a: True, alerter=lambda text: True, sleep=lambda s: None)

    def test_concurrent_change_to_other_post_survives(self):
        # бот согласовал текст P0002 после того, как робот сделал checkout
        self.bot_edit(lambda posts: posts["P0002"].update(status="rendering_wait", approvals={"text": {"by": "42"}}))
        api = MetaSpy()
        report = self.run_robot(api)
        self.assertEqual(report, ["P0001: published"])
        posts = self.remote_posts()
        self.assertEqual(posts["P0001"]["status"], "published")
        self.assertEqual(posts["P0002"]["status"], "rendering_wait")  # правка бота не откатилась
        self.assertEqual(posts["P0002"]["approvals"]["text"]["by"], "42")

    def test_conflict_on_same_post_skips_without_overwriting(self):
        # владелец забрал пост себе ровно в момент запуска робота
        self.bot_edit(lambda posts: posts["P0001"].update(status="handed_off"))
        api = MetaSpy()
        report = self.run_robot(api)
        self.assertEqual(api.calls, 0)
        self.assertTrue(report and "skipped" in report[0], report)
        posts = self.remote_posts()
        self.assertEqual(posts["P0001"]["status"], "handed_off")
        text = (self.robot / pl.POSTS_PATH).read_text(encoding="utf-8")
        self.assertNotIn("<<<<<<<", text)
        self.assertEqual(_git(self.robot, "status", "--porcelain", "--", str(pl.POSTS_PATH)), "")


if __name__ == "__main__":
    unittest.main()
