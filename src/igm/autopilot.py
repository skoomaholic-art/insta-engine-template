"""Scheduled, fail-closed publisher for owner-authored and verified Instagram content.

Only one due IMAGE per run. A GitHub state lock is committed before any Meta write.
If interrupted after the lock, never blindly republish; the item needs review.
"""
import base64
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from pathlib import Path
from .instagram import InstagramAPI, InstagramError

QUEUE_PATH = "content/autopilot-queue.json"
POLICY_PATH = Path(__file__).resolve().parents[2] / "content" / "publishing-policy.json"
MAX_DELAY = timedelta(hours=48)


def _utc_date(iso):
    if not isinstance(iso, str) or "T" not in iso:
        raise ValueError("Publication time must be an ISO UTC timestamp")
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise ValueError("Publication time must be UTC")
    return dt.astimezone(timezone.utc)


def load_policy(path=POLICY_PATH):
    """Fail closed if the owner-review policy is absent or malformed."""
    try:
        policy = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("Publishing policy unavailable; refusing publication") from None
    if policy.get("schema_version") != 1 or policy.get("mode") not in ("review_each", "autonomous"):
        raise ValueError("Publishing policy is invalid; refusing publication")
    if policy["mode"] == "autonomous":
        if policy.get("owner_enabled_autonomy") is not True or not policy.get("owner_activation_reference"):
            raise ValueError("Autonomous mode lacks explicit owner activation")
    return policy


def _review_approved(post):
    """Explicit evidence of review; status alone is never an approval."""
    approved = post.get("owner_approved") is True
    ref = post.get("owner_approval_reference")
    when = post.get("owner_approved_at_utc")
    try:
        valid_time = _utc_date(when) if approved else None
    except (TypeError, ValueError):
        return False
    return approved and isinstance(ref, str) and len(ref.strip()) >= 5 and valid_time is not None


def _eligible(post, now, policy=None):
    policy = policy or load_policy()
    if post.get("status") != "ready" or post.get("format") != "IMAGE":
        return False
    if post.get("rights_cleared") is not True or post.get("facts_checked") is not True:
        return False
    if post.get("authorized_for_autopilot") is not True:
        return False
    if post.get("topic") not in policy.get("allowed_topics", []):
        return False
    if post.get("needs_manual_review") is not False or post.get("has_third_party_assets") is not False:
        return False
    if policy["mode"] == "review_each" and not _review_approved(post):
        return False
    if policy["mode"] == "autonomous" and not _review_approved(post):
        # Autonomous releases only low-risk, original content with a documented editorial check.
        if post.get("low_risk_editorial_review_passed") is not True or post.get("owner_approved") is False:
            return False
    if not isinstance(post.get("id"), str) or not re.fullmatch(r"[a-zA-Z0-9_-]{3,64}", post["id"]):
        return False
    caption = post.get("caption")
    url = post.get("image_url")
    if not isinstance(caption, str) or not caption.strip() or len(caption) > 2200:
        return False
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
        return False
    try:
        due = _utc_date(post.get("publish_at_utc"))
    except (ValueError, TypeError):
        return False
    return due <= now <= due + MAX_DELAY


def choose_due(queue, now=None, policy=None):
    now = now or datetime.now(timezone.utc)
    policy = policy or load_policy()
    ids = [p.get("id") for p in queue.get("posts", [])]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate queue IDs; refusing to publish")
    due = [post for post in queue.get("posts", []) if _eligible(post, now, policy)]
    return min(due, key=lambda x: (x["publish_at_utc"], x["id"])) if due else None


class GitHubQueue:
    def __init__(self, repository=None, token=None, transport=None):
        repository = repository or os.getenv("GITHUB_REPOSITORY", "")
        token = token or os.getenv("GITHUB_TOKEN", "")
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", repository) or not token:
            raise ValueError("Authenticated GitHub repository is required")
        self.url = "https://api.github.com/repos/" + repository + "/contents/" + QUEUE_PATH
        self.headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        }
        self.transport = transport or urlopen

    def load(self):
        try:
            with self.transport(Request(self.url, headers=self.headers), timeout=25) as response:
                payload = json.load(response)
            content = base64.b64decode(payload["content"])
            manifest = json.loads(content)
            if manifest.get("version") != 1 or not isinstance(manifest.get("posts"), list):
                raise ValueError("Invalid queue manifest schema")
            return manifest, payload["sha"]
        except (HTTPError, URLError):
            raise ValueError("GitHub queue read failed") from None

    def save(self, queue, sha):
        body = {
            "message": "ig-autopilot: update publish state",
            "sha": sha,
            "content": base64.b64encode((json.dumps(queue, ensure_ascii=False, sort_keys=True, indent=2)+"\n").encode()).decode(),
        }
        request = Request(self.url, data=json.dumps(body).encode(), headers=self.headers, method="PUT")
        try:
            with self.transport(request, timeout=25) as response:
                payload = json.load(response)
            return payload["content"]["sha"]
        except (HTTPError, URLError, KeyError):
            raise ValueError("GitHub queue update failed; no additional publish will be attempted") from None


def run_autopilot(queue_store=None, instagram=None, now=None):
    """No writes to Meta without global switch and a committed per-item lock."""
    if os.getenv("IGM_ENABLE_PUBLISH", "").strip().lower() != "true":
        return "IGM AUTOPILOT: выключен глобальным переключателем. Публикаций нет."
    now = now or datetime.now(timezone.utc)
    queue_store = queue_store or GitHubQueue()
    manifest, sha = queue_store.load()
    policy = load_policy()
    chosen = choose_due(manifest, now, policy)
    if chosen is None:
        return "IGM AUTOPILOT: готовых публикаций на этот временной слот нет."
    instagram = instagram or InstagramAPI()
    instagram.assert_correct_account()
    # Commit publishing state BEFORE sending content. A failed/uncertain publish will not be auto-retried.
    chosen["status"] = "publishing"
    chosen["started_at_utc"] = now.isoformat(timespec="seconds")
    sha = queue_store.save(manifest, sha)
    try:
        result = instagram.publish_image(chosen["image_url"], chosen["caption"])
    except InstagramError:
        # Leave locked as publishing; cannot determine safely whether Meta already accepted the post.
        return "IGM AUTOPILOT: ошибка Meta при публикации, карточка заблокирована от повторов. Требуется проверка."
    chosen["status"] = "published"
    chosen["published_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    chosen["instagram_media_id"] = result["media_id"]
    queue_store.save(manifest, sha)
    return f'IGM AUTOPILOT: опубликовано {chosen["id"]}, media ID {result["media_id"]}.'
