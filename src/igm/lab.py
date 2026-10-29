"""Viral Lab: per-post metrics at 24h / 72h / 7d and segment analysis for the AI editor.

metrics: for every published pipeline post, store Instagram Insights at checkpoints.
analyze: compare segments (format, genre, freshness, angle, hook, source, slot, weekday, origin)
         by an index relative to the account median, and write data/pipeline/lab.json.
A segment is called a leader/laggard only with enough posts; one viral post proves nothing.
"""
import argparse
import json
import re
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .pipeline import POSTS_PATH, load_state, save_state, utc_now

LAB_PATH = Path("data/pipeline/lab.json")
ALMATY = timezone(timedelta(hours=5))
CHECKPOINTS = (("h24", 24), ("h72", 72), ("d7", 168))
MAX_AGE_H = 30 * 24
MIN_N = 3
DIMENSIONS = {
    "format": "Формат", "genre": "Жанр", "freshness": "Свежесть", "angle": "Подача", "hook": "Хук",
    "source": "Источник темы", "origin": "Кто сделал", "slot": "Время выхода", "weekday": "День недели", "title": "Тайтл",
    "visual": "Картинка",
}
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def safe_error(err):
    """Текст ошибки без токенов и ссылок с ключами."""
    text = re.sub(r"(access_token|api_key|token)=[^&\s]+", r"\1=***", str(err))
    return (type(err).__name__ + ": " + text)[:300]


def collect(state, api, now=None):
    """Fill post['metrics'] checkpoints. Returns (updated_count, source) where source is insights|basic."""
    now = now or datetime.now(timezone.utc)
    updated, source = 0, "insights"
    errors = collect.errors = []
    for post in state["posts"]:
        ig = post.get("ig") or {}
        if post.get("status") != "published" or not ig.get("media_id") or ig.get("missing"):
            continue
        published = parse_time(ig.get("published_at"))
        if not published:
            continue
        age_h = (now - published).total_seconds() / 3600
        if age_h > MAX_AGE_H + 24:
            continue
        metrics = post.setdefault("metrics", {})
        due = [name for name, hours in CHECKPOINTS if age_h >= hours and name not in metrics]
        if not due and age_h > MAX_AGE_H:
            continue
        try:
            values = api.media_insights(ig["media_id"], ig.get("type", ""))
        except Exception as err:
            errors.append(f"{post['id']} insights: {safe_error(err)}")
            try:
                values, source = api.media_basic(ig["media_id"]), "basic"
            except Exception as err2:
                if "subcode 33" in str(err2):  # пост удалён из Instagram — больше не запрашиваем
                    ig["missing"] = True
                    post["ig"] = ig
                    errors.append(f"{post['id']}: нет в Instagram (удалён), исключён из замеров")
                    continue
                errors.append(f"{post['id']} basic: {safe_error(err2)}")
                continue
        values = {k: v for k, v in values.items() if isinstance(v, (int, float))}
        stamp = {"at": now.isoformat(timespec="seconds"), "age_h": round(age_h, 1), "source": source, **values}
        for name in due:
            metrics[name] = stamp
        metrics["latest"] = stamp
        updated += 1
    return updated, source


def score(m):
    """Single comparable number for a post at a checkpoint.
    Views (or reach) weighted up by saves and shares — the signals Instagram rewards for distribution."""
    if not m:
        return None
    base = m.get("views") or m.get("reach")
    if base:
        actions = (m.get("saved") or 0) + (m.get("shares") or 0)
        return base * (1 + 5 * actions / max(base, 1))
    likes, comments = m.get("likes"), m.get("comments")
    if likes is None and comments is None:
        return None
    return (likes or 0) + 3 * (comments or 0)


def visual_source(post):
    """Откуда картинка: кадры/постеры из базы, своё фото или строгий фон без фото."""
    if (post.get("source") or {}).get("kind") == "own":
        return "своё фото/видео"
    if post.get("image_sources"):
        return "кадры/постеры"
    if post.get("bgs") or (post.get("bg") or {}).get("path"):
        return "свои картинки"
    return "строгий фон"


def post_features(post):
    draft = post.get("draft") or {}
    tags = draft.get("tags") or {}
    src = post.get("source") or {}
    feats = {
        "format": "Пост" if draft.get("format") in ("portrait", "square") and len(draft.get("slides") or []) == 1 and src.get("kind") != "own"
                  else {"reel": "Reels", "portrait": "Карусель", "square": "Квадрат"}.get(draft.get("format"), draft.get("format") or "—"),
        "freshness": tags.get("freshness") or "не указано",
        "angle": tags.get("angle") or "не указано",
        "hook": tags.get("hook") or "не указано",
        "source": {"auto": "ИИ-редактор", "news": "новость", "bot": "своя тема", "app": "своя тема", "studio": "конструктор", "manual": "сам в конструкторе", "own": "свой контент", "own_text": "свой текст"}.get(src.get("kind"), src.get("kind") or "—"),
        "origin": {"own": "свои фото/видео", "own_text": "свой текст", "manual": "сам в конструкторе"}.get(src.get("kind"), "ИИ"),
        "genre": tags.get("genres") or ["не указано"],
        "title": tags.get("titles") or [],
        "visual": visual_source(post),
    }
    published = parse_time((post.get("ig") or {}).get("published_at"))
    if published:
        local = published.astimezone(ALMATY)
        feats["slot"] = "утро (до 12)" if local.hour < 12 else "день (12–18)" if local.hour < 18 else "вечер (18+)"
        feats["weekday"] = WEEKDAYS[local.weekday()]
    return feats


def analyze(state, now=None):
    posts = [p for p in state["posts"] if p.get("status") == "published" and p.get("metrics")]
    checkpoint = "h72" if sum(1 for p in posts if "h72" in p["metrics"]) >= 5 else "h24"
    rows = []
    for p in posts:
        m = p["metrics"].get(checkpoint) or (p["metrics"].get("latest") if checkpoint == "h24" else None)
        s = score(m)
        if s is None:
            continue
        reach = (m.get("reach") or m.get("views") or 0) or None
        rows.append({"post": p, "score": s, "m": m, "save_rate": (m.get("saved") or 0) / reach if reach else None,
                     "share_rate": (m.get("shares") or 0) / reach if reach else None})
    median = statistics.median([r["score"] for r in rows]) if rows else None
    for r in rows:
        r["index"] = r["score"] / median if median else None
    segments = {}
    for dim in DIMENSIONS:
        groups = {}
        for r in rows:
            vals = post_features(r["post"]).get(dim)
            for v in (vals if isinstance(vals, list) else [vals]):
                if v:
                    groups.setdefault(str(v)[:60], []).append(r)
        out = []
        for value, items in groups.items():
            idx = [i["index"] for i in items if i["index"] is not None]
            med = statistics.median(idx) if idx else None
            sr = [i["save_rate"] for i in items if i["save_rate"] is not None]
            sh = [i["share_rate"] for i in items if i["share_rate"] is not None]
            verdict = "мало данных" if len(items) < MIN_N or med is None else "лидер" if med >= 1.2 else "аутсайдер" if med <= 0.8 else "средне"
            out.append({"value": value, "n": len(items), "index": round(med, 2) if med is not None else None,
                        "save_rate": round(statistics.mean(sr) * 100, 2) if sr else None,
                        "share_rate": round(statistics.mean(sh) * 100, 2) if sh else None, "verdict": verdict})
        if dim == "title":
            out = [o for o in out if o["n"] >= 2]
        out.sort(key=lambda o: (-(o["index"] or 0), -o["n"]))
        segments[dim] = out
    top = sorted(rows, key=lambda r: -r["score"])[:5]
    hypotheses = []
    for p in posts:
        hyp = (p.get("source") or {}).get("hypothesis")
        if hyp:
            r = next((x for x in rows if x["post"] is p), None)
            hypotheses.append({"post": p["id"], "hypothesis": hyp, "index": round(r["index"], 2) if r and r["index"] else None})
    return {
        "schema_version": 1,
        "updated_at_utc": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "checkpoint": checkpoint,
        "posts_measured": len(rows),
        "posts_published": sum(1 for p in state["posts"] if p.get("status") == "published"),
        "metric_source": "insights" if any(r["m"].get("reach") or r["m"].get("views") for r in rows) else ("basic" if rows else None),
        "median_score": round(median, 1) if median else None,
        "dimensions": DIMENSIONS,
        "segments": segments,
        "leaders": [{"dim": d, **s} for d, ss in segments.items() for s in ss if s["verdict"] == "лидер"][:10],
        "laggards": [{"dim": d, **s} for d, ss in segments.items() for s in ss if s["verdict"] == "аутсайдер"][:10],
        "top_posts": [{"id": r["post"]["id"], "title": ((r["post"].get("draft") or {}).get("slides") or [{}])[0].get("title"),
                       "index": round(r["index"], 2) if r["index"] else None, "permalink": (r["post"].get("ig") or {}).get("permalink"),
                       **{k: r["m"].get(k) for k in ("views", "reach", "saved", "shares", "likes", "comments") if r["m"].get(k) is not None}} for r in top],
        "hypotheses": hypotheses[-20:],
        "min_posts_for_verdict": MIN_N,
    }


def run(root=".", api_factory=None, now=None):
    path = Path(root) / POSTS_PATH
    state = load_state(path)
    note = "no published posts"
    if any(p.get("status") == "published" for p in state["posts"]):
        errors = []
        try:
            from .instagram import InstagramAPI
            api = (api_factory or InstagramAPI)()
            updated, source = collect(state, api, now)
            errors = collect.errors
            save_state(state, path)
            note = f"metrics updated for {updated} post(s) via {source}"
        except Exception as err:  # метрики не собрались — анализ всё равно пересчитываем
            errors = [f"setup: {safe_error(err)}"]
            note = "metrics not collected"
        for e in errors:
            print("lab:", e)
    lab = analyze(state, now)
    lab["errors"] = (errors if any(p.get("status") == "published" for p in state["posts"]) else [])[:20]
    out = Path(root) / LAB_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(lab, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return note + f"; lab: {lab['posts_measured']} measured"


def main():
    argparse.ArgumentParser().parse_args()
    print(run())


if __name__ == "__main__":
    main()
