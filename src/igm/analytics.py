"""Free daily snapshots and longitudinal analysis for MyBrand."""
import base64
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .instagram import InstagramAPI, InstagramError

ALMATY = timezone(timedelta(hours=5))
SNAPSHOT_DIR = Path("data/snapshots")
FIELDS = ("id", "timestamp", "media_type", "permalink", "like_count", "comments_count")


def _safe_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def collect_snapshot(api=None, now=None):
    """Read only. Never log tokens, contact lists or raw API responses."""
    api = api or InstagramAPI()
    now = now or datetime.now(timezone.utc)
    account = api.profile()
    actual = str(account.get("username", "")).strip().lstrip("@").lower()
    expected = os.getenv("IG_EXPECTED_USERNAME", "mybrand").strip().lstrip("@").lower()
    if actual != expected:
        raise InstagramError("Wrong authorized Instagram account; snapshot not saved")
    records = []
    for media in api.recent_media(50):
        record = {field: media.get(field) for field in FIELDS if field in media}
        record["like_count"] = _safe_int(media.get("like_count"))
        record["comments_count"] = _safe_int(media.get("comments_count"))
        records.append(record)
    records.sort(key=lambda x: (x.get("timestamp") or "", str(x.get("id", ""))))
    return {
        "schema_version": 1,
        "source": "instagram_graph_api",
        "captured_at_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "date_almaty": now.astimezone(ALMATY).strftime("%Y-%m-%d"),
        "account": {
            "username": actual,
            "followers_count": _safe_int(account.get("followers_count")),
            "media_count": _safe_int(account.get("media_count")),
        },
        "media": records,
        "insights_status": "not_configured",
        "note": "Only basic counters; no reach, views, saves or shares until Insights permission is granted.",
    }


def persist_snapshot(snapshot, root=SNAPSHOT_DIR):
    """Idempotent daily file. Date is computed in Asia/Almaty; safe JSON only."""
    day = snapshot["date_almaty"]
    datetime.strptime(day, "%Y-%m-%d")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / (day + ".json")
    target.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return target


def history(root=SNAPSHOT_DIR):
    root = Path(root)
    result = []
    for path in sorted(root.glob("????-??-??.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or data.get("account", {}).get("username") != "mybrand":
            continue
        result.append(data)
    return result


def render_trend(root=SNAPSHOT_DIR):
    snapshots = history(root)
    if not snapshots:
        return ("## Viral Lab: история ещё не собиралась\n\n"
                "Запусти Analytics Snapshot workflow или дождись ежедневного сбора. "
                "Реальные показатели не подменяются прогнозами.")
    first, last = snapshots[0], snapshots[-1]
    old = first["account"].get("followers_count")
    new = last["account"].get("followers_count")
    delta = f"{new - old:+d}" if isinstance(old, int) and isinstance(new, int) else "н/д"
    lines = [
        "## Viral Lab: история MyBrand",
        "",
        f"- Снимков: {len(snapshots)}",
        f"- Период: {first['date_almaty']} - {last['date_almaty']} (UTC+5)",
        f"- Подписчиков сейчас: {new if new is not None else 'н/д'}",
        f"- Изменение подписчиков за период: {delta}",
        f"- Публикаций сейчас: {last['account'].get('media_count', 'н/д')}",
        "",
        "| Дата | Подписчики | Публикации |",
        "|---|---:|---:|",
    ]
    for data in snapshots[-14:]:
        acc = data["account"]
        lines.append(f"| {data['date_almaty']} | {acc.get('followers_count', 'н/д')} | {acc.get('media_count', 'н/д')} |")
    lines.extend([
        "",
        "Это сохранённые базовые счётчики Instagram, не прогноз вирусности.",
        "Сравнение ниш кино/сериалы и нормализация по охвату появятся после публикаций, тегирования тем и разрешения Insights."
    ])
    return "\n".join(lines)


def commit_github_snapshot(snapshot, repository=None, token=None, transport=None):
    """Store one JSON document per Almaty day in private GitHub repo.

    Does not expose credentials or grant unauthenticated writes.
    """
    repository = repository or os.getenv("GITHUB_REPOSITORY", "")
    token = token or os.getenv("GITHUB_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid GitHub repository")
    if not token:
        raise ValueError("GITHUB_TOKEN missing")
    day = snapshot["date_almaty"]
    datetime.strptime(day, "%Y-%m-%d")
    path = "data/snapshots/" + day + ".json"
    endpoint = "https://api.github.com/repos/" + repository + "/contents/" + path
    transport = transport or urlopen
    headers = {"Authorization": "Bearer " + token,
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "Content-Type": "application/json"}
    sha = None
    try:
        with transport(Request(endpoint, headers=headers), timeout=20) as response:
            sha = json.load(response).get("sha")
    except HTTPError as exc:
        if exc.code != 404:
            raise ValueError(f"GitHub storage read HTTP {exc.code}") from None
    except URLError:
        raise ValueError("GitHub storage network failure") from None
    body = {"message": "analytics: save Instagram metrics " + day,
            "content": base64.b64encode((json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")).decode("ascii")}
    if sha:
        body["sha"] = sha
    request = Request(endpoint, data=json.dumps(body).encode("utf-8"), headers=headers, method="PUT")
    try:
        with transport(request, timeout=20) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise ValueError(f"GitHub storage write HTTP {exc.code}") from None
    except URLError:
        raise ValueError("GitHub storage network failure") from None
    if not result.get("commit", {}).get("sha"):
        raise ValueError("GitHub did not confirm snapshot commit")
    return path
