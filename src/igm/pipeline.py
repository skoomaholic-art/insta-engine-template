"""MyBrand post pipeline: owner-approved text → rendered post → owner-approved final → Instagram.

Runs in GitHub Actions (workflow pipeline.yml) when data/pipeline/posts.json changes.
Statuses written by the Telegram bot / Mini App (Vercel):
    text_review → rendering        (owner approved the text)
    final_review → publishing      (owner approved the rendered final)
This module handles:
    rendering  → final_review      (render slides in Cover Studio, Reels video via ffmpeg)
    publishing → published         (publish through the official Instagram API)
Nothing is published without both owner approvals recorded in the post.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from .policy import is_blocked_username

POSTS_PATH = Path("data/pipeline/posts.json")
MEDIA_DIR = Path("media/posts")
DEFAULT_BASE = "https://your-app.vercel.app"
MEDIA_TTL = 3 * 3600


class PipelineError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state(path=POSTS_PATH):
    path = Path(path)
    if not path.exists():
        return {"schema_version": 1, "posts": []}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema_version") != 1 or not isinstance(state.get("posts"), list):
        raise PipelineError("Invalid pipeline schema")
    return state


def save_state(state, path=POSTS_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def log(post, event, note=None):
    post.setdefault("history", []).append({"at": utc_now(), "event": event, "by": "pipeline", **({"note": str(note)[:300]} if note else {})})
    post["updated_at_utc"] = utc_now()


# ---------- signed public media links (served by Vercel /api/media) ----------
def sign(path, exp, secret):
    msg = f"{path}:{int(exp)}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:40]


def media_url(base, path, secret, ttl=MEDIA_TTL, now=None):
    exp = int((now or time.time()) + ttl)
    return f"{base.rstrip('/')}/api/media?p={path}&e={exp}&s={sign(path, exp, secret)}"


# ---------- rendering ----------
def pages_or_draft(post, root="."):
    payload = {"pages": post.get("pages") or None, "draft": post["draft"], "bgs": {},
               "studio_sync": post.get("studio_sync") == 1}
    paths = {}
    if (post.get("bg") or {}).get("path"):
        paths["0"] = post["bg"]["path"]
    paths.update({str(k): v for k, v in (post.get("bgs") or {}).items() if v})
    for idx, rel in paths.items():
        if Path(root, rel).exists():
            mime = "image/png" if rel.endswith(".png") else "image/jpeg"
            payload["bgs"][idx] = f"data:{mime};base64," + base64.b64encode(Path(root, rel).read_bytes()).decode()
    return payload


def playwright_render(studio_url, post, timeout_ms=60000, root=".", with_layers=False):
    """Render every slide with the real Cover Studio engine; returns list of JPEG bytes."""
    from playwright.sync_api import sync_playwright  # installed only in the pipeline workflow

    payload = pages_or_draft(post, root)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 430, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(studio_url.rstrip("/") + "/cover-studio/?render=1", wait_until="networkidle", timeout=timeout_ms)
        page.wait_for_function("window.__skooma && window.__skooma.renderAll && window.SkoomaPages", timeout=timeout_ms)
        data_urls = page.evaluate(
            """async (payload) => {
                await document.fonts.ready;
                const pages = payload.pages || window.SkoomaPages.fromDraft(payload.draft);
                for (const [i, img] of Object.entries(payload.bgs || {})) {
                    const k = Number(i);
                    if (pages[k]) {
                        // Old posts retain the historical automatic framing.
                        // New Cover Studio submissions preserve the exact preview
                        // crop, positioning and dimming saved in post.pages.
                        const oldLayout = payload.studio_sync ? {} : { bgFit: 'cover', dim: 62, bgX: 50, bgY: 50 };
                        pages[k] = { ...pages[k], bgImage: img, ...oldLayout };
                    }
                }
                window.__skooma.loadGenerated(pages);
                await document.fonts.ready;
                const full = await window.__skooma.renderAll('image/jpeg', 0.92);
                // Reels: отдельно фон и текст, чтобы анимировать их по-разному
                const layers = payload.with_layers && window.__skooma.renderLayers ? await window.__skooma.renderLayers() : null;
                return { full, layers };
            }""",
            {**payload, "with_layers": with_layers},
        )
        browser.close()
    layers = data_urls.get("layers") if isinstance(data_urls, dict) else None
    data_urls = data_urls.get("full") if isinstance(data_urls, dict) else data_urls
    if not data_urls:
        raise PipelineError("Cover Studio returned no slides" + (f": {errors[0]}" if errors else ""))
    out = []
    for url in data_urls:
        head, _, b64 = str(url).partition(",")
        if not head.startswith("data:image/jpeg"):
            raise PipelineError("Cover Studio did not return JPEG")
        out.append(base64.b64decode(b64))
    if with_layers:
        decoded = [{"bg": base64.b64decode(x["bg"].partition(",")[2]), "fg": base64.b64decode(x["fg"].partition(",")[2])} for x in (layers or [])]
        return out, decoded
    return out


def make_reel_video(images, out_path, seconds_per_slide=4, min_seconds=8):
    """Silent 1080x1920 H.264 video with AAC track from slide images (Reels need video)."""
    if not shutil.which("ffmpeg"):
        raise PipelineError("ffmpeg is not available")
    per = max(seconds_per_slide, min_seconds / max(1, len(images)))
    with tempfile.TemporaryDirectory() as tmp:
        listing = Path(tmp) / "list.txt"
        lines = []
        for img in images:
            lines += [f"file '{Path(img).resolve()}'", f"duration {per:.2f}"]
        lines.append(f"file '{Path(images[-1]).resolve()}'")
        listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
        total = per * len(images)
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=44100:cl=stereo",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p,fps=30",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-profile:v", "high",
            "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(out_path),
        ]
        subprocess.run(cmd, check=True)
    return out_path


def _probe_size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0:s=x", str(path)], capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split("x")[:2]
    return int(w), int(h)


def own_image_filter(w, h):
    """Instagram feed accepts 4:5 … 1.91:1; pad (never crop) the owner's picture into that range."""
    ratio = h / w
    if ratio > 1.25:
        return "scale=-2:1350,pad=1080:1350:(ow-iw)/2:(oh-ih)/2:color=0x12161B"
    if w / h > 1.91:
        return "scale=1080:-2,pad=1080:566:(ow-iw)/2:(oh-ih)/2:color=0x12161B"
    return "scale=1080:-2"


def render_own(post, root="."):
    """Owner's own photos/video uploaded by the bot: normalize to Instagram specs without changing content."""
    if not shutil.which("ffmpeg"):
        raise PipelineError("ffmpeg is not available")
    sources = (post.get("own") or {}).get("files") or []
    if not sources:
        raise PipelineError("Нет загруженных файлов")
    folder = Path(root) / MEDIA_DIR / post["id"]
    rel, video = [], None
    if post["draft"].get("format") == "reel":
        src = Path(root) / sources[0]
        out = folder / "reel.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-t", "90",
                        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x12161B,format=yuv420p,fps=30",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "44100",
                        "-movflags", "+faststart", str(out)], check=True)
        if subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(out)],
                          capture_output=True, text=True).stdout.strip() == "":
            tmp = folder / "reel_a.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                            "-c:v", "copy", "-c:a", "aac", "-shortest", str(tmp)], check=True)
            tmp.replace(out)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out), "-frames:v", "1", "-q:v", "2", str(folder / "1.jpg")], check=True)
        rel = [str((MEDIA_DIR / post["id"] / "1.jpg").as_posix())]
        video = str((MEDIA_DIR / post["id"] / "reel.mp4").as_posix())
    else:
        for i, s in enumerate(sources[:10], 1):
            src = Path(root) / s
            w, h = _probe_size(src)
            out = folder / f"{i}.jpg"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", own_image_filter(w, h), "-q:v", "2", str(out)], check=True)
            rel.append(str((MEDIA_DIR / post["id"] / f"{i}.jpg").as_posix()))
    post["media"] = {"images": rel, "video": video, "rendered_at": utc_now()}
    post["status"] = "final_review"
    post["error"] = None
    log(post, "rendered", f"own: {len(rel)} image(s)" + (" + video" if video else ""))
    return post


def make_live_reel(post, layers, folder, root="."):
    """Живой Reels: движение фона, анимация текста, переходы и музыка из библиотеки."""
    from .reel import make_motion_reel, pick_track
    recent = [((p.get("music") or {}).get("file")) for p in load_state(Path(root) / POSTS_PATH)["posts"][-6:]]
    seed = int(hashlib.sha1(post["id"].encode()).hexdigest()[:8], 16)
    track = None
    if (post.get("music") or {}).get("file") and Path(root, post["music"]["file"]).exists():
        track = post["music"]  # трек, выбранный владельцем
    else:
        from .reel import style_of
        style = style_of(post["draft"].get("reel_style"))
        mood = post["draft"].get("music_mood") if post["draft"].get("music_mood") in style["music"] else style["music"][0]
        track = pick_track(mood, root, seed, exclude=[r for r in recent if r])
    with tempfile.TemporaryDirectory() as tmp:
        bgs, fgs = [], []
        for i, layer in enumerate(layers):
            b, f = Path(tmp) / f"bg{i}.jpg", Path(tmp) / f"fg{i}.png"
            b.write_bytes(layer["bg"]); f.write_bytes(layer["fg"])
            bgs.append(b); fgs.append(f)
        # 2.5D для сцен с фотографией, клипы владельца — вместо фона сцены
        photo = {int(k) for k, v in (post.get("bgs") or {}).items() if v} | ({0} if (post.get("bg") or {}).get("path") else set())
        clips = {int(k): Path(root, v) for k, v in (post.get("clips") or {}).items() if v and Path(root, v).exists()}
        info = make_motion_reel(bgs, fgs, post["draft"].get("slides") or [{}] * len(layers), folder / "reel.mp4",
                                music=Path(root, track["file"]) if track else None, seed=seed, depth=photo, clips=clips,
                                style=post["draft"].get("reel_style"))
    post["music"] = {k: track.get(k) for k in ("file", "title", "mood", "credit", "license")} if track else None
    post["reel_info"] = info
    return info


def render_post(post, studio_url, root=".", renderer=playwright_render, video_maker=make_reel_video):
    if (post.get("source") or {}).get("kind") == "own":
        return render_own(post, root)
    is_reel = post["draft"].get("format") == "reel"
    layers = None
    if renderer is playwright_render:
        result = renderer(studio_url, post, root=root, with_layers=is_reel)
        images, layers = result if is_reel else (result, None)
    else:
        images = renderer(studio_url, post)
    if not 1 <= len(images) <= 10:
        raise PipelineError(f"Unsupported number of slides: {len(images)}")
    folder = Path(root) / MEDIA_DIR / post["id"]
    folder.mkdir(parents=True, exist_ok=True)
    for old in list(folder.glob("[0-9]*.jpg")) + list(folder.glob("reel.mp4")):
        old.unlink()  # прежний рендер; исходники владельца (src-*, bg-*) не трогаем
    rel = []
    for i, data in enumerate(images, 1):
        target = folder / f"{i}.jpg"
        target.write_bytes(data)
        rel.append(str((MEDIA_DIR / post["id"] / f"{i}.jpg").as_posix()))
    video = None
    if is_reel:
        if layers and len(layers) == len(rel):
            make_live_reel(post, layers, folder, root)
        else:
            video_maker([Path(root) / r for r in rel], folder / "reel.mp4")
        video = str((MEDIA_DIR / post["id"] / "reel.mp4").as_posix())
    post["media"] = {"images": rel, "video": video, "rendered_at": utc_now()}
    post["status"] = "final_review"
    post["error"] = None
    log(post, "rendered", f"{len(rel)} slide(s)" + (" + video" if video else ""))
    return post


# ---------- publishing ----------
def can_publish(post, env=os.environ):
    if str(env.get("IGM_ENABLE_PUBLISH", "")).lower() != "true":
        raise PipelineError("Публикация выключена: в IGM переменная IGM_ENABLE_PUBLISH не равна true")
    approvals = post.get("approvals") or {}
    if not (approvals.get("text") or {}).get("by") or not (approvals.get("final") or {}).get("by"):
        raise PipelineError("Нет двух согласований владельца (текст и финал)")
    if (post.get("ig") or {}).get("media_id"):
        raise PipelineError("Пост уже опубликован")
    if is_blocked_username(post.get("draft", {}).get("target_username")):
        raise PipelineError("Взаимодействие запрещено политикой аккаунта")
    media = post.get("media") or {}
    if not media.get("images"):
        raise PipelineError("Нет готовых изображений")
    caption = str(post["draft"].get("caption") or "").strip()
    if not caption:
        raise PipelineError("Пустая подпись")
    return with_image_credit(caption, post)


IMAGE_CREDIT = "Изображения: © правообладатели"


def with_image_credit(caption, post):
    """Кадры и постеры из TMDB/fanart.tv: указываем источник перед хэштегами."""
    sources = sorted({str(x.get("source") or "").strip() for x in (post.get("image_sources") or []) if isinstance(x, dict)} - {""})
    credits = []
    if sources and IMAGE_CREDIT not in caption:
        credits.append(f"{IMAGE_CREDIT}, источник {', '.join(sources)}")
    music = (post.get("music") or {}).get("credit")
    if music and music not in caption:
        credits.append(f"Музыка: {music}")
    if not credits:
        return caption
    credit = "\n".join(credits)
    lines = caption.rstrip().split("\n")
    tail = []
    while lines and lines[-1].strip().startswith("#"):
        tail.insert(0, lines.pop())
    body = "\n".join(lines).rstrip()
    return "\n\n".join(x for x in (body, credit, "\n".join(tail).strip()) if x)


def publish_post(post, api, base, secret, env=os.environ, sleep=time.sleep):
    caption = can_publish(post, env)
    media = post["media"]
    url = lambda path: media_url(base, path, secret)
    fmt = post["draft"].get("format")
    if fmt == "reel":
        if not media.get("video"):
            raise PipelineError("Для Reels нет видео")
        result = api.publish_reel(url(media["video"]), caption, cover_url=url(media["images"][0]), sleep=sleep)
        kind = "REELS"
    elif len(media["images"]) >= 2:
        result = api.publish_carousel([url(p) for p in media["images"]], caption, sleep=sleep)
        kind = "CAROUSEL"
    else:
        result = api.publish_single_image(url(media["images"][0]), caption, sleep=sleep)
        kind = "IMAGE"
    post["ig"] = {"media_id": result["media_id"], "permalink": result.get("permalink") or None, "published_at": utc_now(), "type": kind}
    post["status"] = "published"
    post["error"] = None
    log(post, "published", result.get("permalink") or result["media_id"])
    return post


# ---------- notifications to the owner via Vercel bot ----------
def hook_signature(post_id, event, ts, secret):
    return hmac.new(secret.encode(), f"{post_id}:{event}:{int(ts)}".encode(), hashlib.sha256).hexdigest()


def notify(base, secret, post_id, event, transport=urlopen, timeout=25):
    if not secret:
        return False
    ts = int(time.time())
    body = json.dumps({"id": post_id, "event": event, "ts": ts}).encode()
    req = Request(base.rstrip("/") + "/api/pipeline-hook", data=body, method="POST",
                  headers={"Content-Type": "application/json", "X-Pipeline-Signature": hook_signature(post_id, event, ts, secret)})
    try:
        with transport(req, timeout=timeout) as r:
            return 200 <= getattr(r, "status", 200) < 300
    except Exception:  # notification failure must not undo a completed render/publish
        return False


def ping(base, secret, transport=urlopen):
    """Signed check that Vercel accepts our PIPELINE_SECRET; returns Vercel's self-report."""
    if not secret:
        return {"ok": False, "error": "PIPELINE_SECRET не передан в этот workflow"}
    ts = int(time.time())
    body = json.dumps({"id": "P0000", "event": "ping", "ts": ts}).encode()
    req = Request(base.rstrip("/") + "/api/pipeline-hook", data=body, method="POST",
                  headers={"Content-Type": "application/json", "X-Pipeline-Signature": hook_signature("P0000", "ping", ts, secret)})
    try:
        with transport(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except Exception as err:
        code = getattr(err, "code", None)
        if code == 401:
            return {"ok": False, "error": "Vercel отклонил подпись: PIPELINE_SECRET на Vercel не задан или не совпадает (нужен Redeploy после добавления)"}
        return {"ok": False, "error": f"Vercel не ответил ({code or type(err).__name__})"}


def git_commit_push(message, paths):
    subprocess.run(["git", "add", "--", *paths], check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        return False
    subprocess.run(["git", "commit", "-q", "-m", message], check=True)
    for attempt in range(4):
        if subprocess.run(["git", "push", "-q", "origin", "HEAD:main"]).returncode == 0:
            return True
        subprocess.run(["git", "pull", "-q", "--rebase", "origin", "main"], check=True)
    raise PipelineError("git push failed")


def run(env=os.environ, root=".", api_factory=None, renderer=playwright_render, video_maker=make_reel_video,
        commit=git_commit_push, notifier=notify, sleep=time.sleep):
    base = env.get("PIPELINE_BASE_URL") or DEFAULT_BASE
    secret = env.get("PIPELINE_SECRET", "")
    path = Path(root) / POSTS_PATH
    report = []

    def reload_post(pid):
        state = load_state(path)
        return state, next((p for p in state["posts"] if p.get("id") == pid), None)

    ids = needs_work(root)
    for pid in ids:
        state, post = reload_post(pid)
        if not post:
            continue
        status = post["status"]
        if status == "scheduled":  # время отложенной публикации пришло — публикуем как обычно
            if not is_due(post):
                continue
            status = "publishing"
        try:
            if status == "rendering":
                render_post(post, base, root, renderer, video_maker)
                save_state(state, path)
                commit(f"pipeline: render {pid}", [str(POSTS_PATH), str(MEDIA_DIR / pid)])
                notifier(base, secret, pid, "final_ready")
                report.append(f"{pid}: rendered")
            elif status == "publishing":
                if not secret:
                    raise PipelineError("PIPELINE_SECRET не задан в IGM")
                can_publish(post, env)
                # Lock first so a re-run can never publish twice
                post["status"] = "publishing_in_progress"
                log(post, "publish_started")
                save_state(state, path)
                commit(f"pipeline: publishing {pid}", [str(POSTS_PATH)])
                api = (api_factory or _default_api)()
                publish_post(post, api, base, secret, env, sleep)
                save_state(state, path)
                commit(f"pipeline: published {pid}", [str(POSTS_PATH)])
                notifier(base, secret, pid, "published")
                report.append(f"{pid}: published")
        except Exception as err:  # record and tell the owner; never retry a publish silently
            state, post = reload_post(pid) if status == "rendering" else (state, post)
            if post is None:
                continue
            stage = "render" if status == "rendering" else "publish"
            post["status"] = "failed"
            post["error"] = {"stage": stage, "message": str(err)[:400], "at": utc_now()}
            log(post, f"{stage}_failed", err)
            save_state(state, path)
            commit(f"pipeline: {stage} failed {pid}", [str(POSTS_PATH)])
            notifier(base, secret, pid, "failed")
            report.append(f"{pid}: {stage} failed: {err}")
    return report


def _default_api():
    from .instagram import InstagramAPI
    return InstagramAPI()


def is_due(post, now=None):
    """Отложенная публикация: оба согласования есть, время наступило."""
    at = parse_iso(post.get("scheduled_at"))
    now = now or datetime.now(timezone.utc)
    return post.get("status") == "scheduled" and at is not None and at <= now


def parse_iso(value):
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def needs_work(root=".", now=None):
    state = load_state(Path(root) / POSTS_PATH)
    return [p["id"] for p in state["posts"] if p.get("status") in ("rendering", "publishing") or is_due(p, now)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["pending", "run"])
    args = parser.parse_args()
    if args.action == "pending":
        ids = needs_work()
        print(" ".join(ids))
        out = os.getenv("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"work={'yes' if ids else 'no'}\n")
        return
    for line in run():
        print(line)


if __name__ == "__main__":
    main()
