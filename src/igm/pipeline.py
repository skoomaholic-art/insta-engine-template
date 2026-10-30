"""MyBrand post pipeline: owner-approved text → rendered post → owner-approved final → Instagram.

Runs in GitHub Actions (workflow pipeline.yml) when the bot pushes data/pipeline/kick.json
(plus a backup hourly schedule). The robot itself never writes kick.json.
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
import copy
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from .instagram import InstagramError
from .policy import is_blocked_username

POSTS_PATH = Path("data/pipeline/posts.json")
KICK_PATH = Path("data/pipeline/kick.json")  # пишет только бот: пуш этого файла запускает pipeline.yml
MEDIA_DIR = Path("media/posts")
DEFAULT_BASE = "https://your-app.vercel.app"
MEDIA_TTL = 3 * 3600


class PipelineError(RuntimeError):
    pass


class ConcurrentUpdate(PipelineError):
    """Бот изменил posts.json одновременно с роботом и git не смог слить правки: пост пропускаем без записи, повторит следующий запуск."""


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


MIN_REEL_SECONDS = 3.2  # Instagram не принимает Reels короче 3 с


def _probe_duration(path):
    """Длительность файла в секундах (0, если ffprobe не смог прочитать)."""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


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
        # клип владельца короче минимума Instagram — держим последний кадр до MIN_REEL_SECONDS
        short = max(0.0, MIN_REEL_SECONDS - _probe_duration(src))
        hold = f",tpad=stop_mode=clone:stop_duration={short:.2f}" if short > 0 else ""
        audio_pad = ["-af", f"apad=pad_dur={short:.2f}"] if short > 0 else []
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-t", "90",
                        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x12161B,format=yuv420p,fps=30" + hold,
                        *audio_pad,
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
    final = approvals.get("final") or {}
    if not (approvals.get("text") or {}).get("by") or not final.get("by"):
        raise PipelineError("Нет двух согласований владельца (текст и финал)")
    ig = post.get("ig") or {}
    if ig.get("media_id") or ig.get("container_id") or post.get("status") == "published":
        raise PipelineError("Пост уже опубликован")
    if is_blocked_username(post.get("draft", {}).get("target_username")):
        raise PipelineError("Взаимодействие запрещено политикой аккаунта")
    media = post.get("media") or {}
    if not media.get("images"):
        raise PipelineError("Нет готовых изображений")
    # Контракт с ботом: финал согласован именно для этого рендера (approvals.final.rid = media.rendered_at)
    rendered_at = media.get("rendered_at")
    if "rid" in final and final.get("rid") != rendered_at:
        raise PipelineError("Финал согласован для другого рендера (rid не равен media.rendered_at) — согласуйте актуальный финал заново")
    final_at, rendered = parse_iso(final.get("at")), parse_iso(rendered_at)
    if final_at and rendered and final_at < rendered:
        raise PipelineError("Финал согласован раньше последнего рендера — согласуйте актуальный финал заново")
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



def post_caption(post):
    """Подпись в том виде, в каком она уходит в Instagram (для сверки с опубликованными)."""
    return with_image_credit(str((post.get("draft") or {}).get("caption") or "").strip(), post)


def send_to_instagram(post, api, base, secret, env=os.environ, sleep=time.sleep, on_container=None):
    """Отправить пост в Instagram. on_container(id) вызывается ДО media_publish (сохраняем ig_pending)."""
    caption = can_publish(post, env)
    media = post["media"]
    url = lambda path: media_url(base, path, secret)
    kw = {"on_container": on_container} if on_container else {}
    fmt = post["draft"].get("format")
    if fmt == "reel":
        if not media.get("video"):
            raise PipelineError("Для Reels нет видео")
        result = api.publish_reel(url(media["video"]), caption, cover_url=url(media["images"][0]), sleep=sleep, **kw)
        kind = "REELS"
    elif len(media["images"]) >= 2:
        result = api.publish_carousel([url(p) for p in media["images"]], caption, sleep=sleep, **kw)
        kind = "CAROUSEL"
    else:
        result = api.publish_single_image(url(media["images"][0]), caption, sleep=sleep, **kw)
        kind = "IMAGE"
    return result, kind


def media_kind(post):
    fmt = (post.get("draft") or {}).get("format")
    if fmt == "reel":
        return "REELS"
    return "CAROUSEL" if len((post.get("media") or {}).get("images") or []) >= 2 else "IMAGE"


def apply_published(post, result, kind, note=None):
    """Записать факт публикации в пост (и после обычной публикации, и после сверки «завис/неизвестный исход»)."""
    post["ig"] = {"media_id": result.get("media_id") or None, "permalink": result.get("permalink") or None,
                  "published_at": result.get("published_at") or utc_now(), "type": kind}
    if result.get("container_id"):
        post["ig"]["container_id"] = str(result["container_id"])
    post.pop("ig_pending", None)
    post["status"] = "published"
    post["error"] = None
    log(post, "published", note or result.get("permalink") or result.get("media_id") or result.get("container_id"))
    return post


def publish_post(post, api, base, secret, env=os.environ, sleep=time.sleep, on_container=None):
    result, kind = send_to_instagram(post, api, base, secret, env, sleep, on_container)
    return apply_published(post, result, kind)


# ---------- сверка: вышел ли пост, если ответ Meta потерялся или запуск оборвался ----------
RECONCILE_WINDOW = timedelta(hours=2)


def _norm_caption(text):
    return " ".join(str(text or "").split())[:120]


def publish_started_at(post):
    """Когда робот поставил блокировку publishing_in_progress (последняя запись publish_started)."""
    for h in reversed(post.get("history") or []):
        if isinstance(h, dict) and h.get("event") == "publish_started":
            return parse_iso(h.get("at"))
    return None


def find_published(api, post, caption, now=None):
    """Проверка без публикации: статус контейнера из ig_pending и/или пост с той же подписью среди последних.
    Возвращает dict с media_id/permalink/container_id, если пост вышел; None — если точно не вышел.
    Если проверить не удалось — PipelineError (публиковать повторно нельзя)."""
    now = now or datetime.now(timezone.utc)
    pending = post.get("ig_pending") or {}
    cid = str(pending.get("container_id") or "")
    status = None
    if cid:
        try:
            status = api.container_status(cid)
        except Exception:  # контейнер мог истечь (24 ч) — опираемся на список публикаций
            status = None
    since = parse_iso(pending.get("at")) or publish_started_at(post) or (now - RECONCILE_WINDOW)
    lo, hi = since - timedelta(minutes=10), since + RECONCILE_WINDOW
    want = _norm_caption(caption)
    try:
        media = api.recent_media(limit=25)
    except Exception as err:
        if status == "PUBLISHED":
            return {"container_id": cid, "media_id": None, "permalink": None}
        if status:  # контейнер жив и не опубликован — значит, поста нет
            return None
        raise PipelineError(f"Не удалось проверить, вышел ли пост в Instagram ({type(err).__name__}); повторная публикация заблокирована") from None
    for m in media or []:
        at = parse_iso(m.get("timestamp"))
        if want and _norm_caption(m.get("caption")) == want and (at is None or lo <= at <= hi):
            return {"media_id": str(m.get("id") or "") or None, "permalink": m.get("permalink"), "container_id": cid or None,
                    "published_at": at.isoformat(timespec="seconds") if at else None}
    if status == "PUBLISHED":
        return {"container_id": cid, "media_id": None, "permalink": None}
    return None


# ---------- notifications to the owner via Vercel bot ----------
ALERT_ID = "P0000"
TOKEN_EXPIRED_TEXT = ("Токен Instagram истёк или отозван (ошибка Meta 190): публикации и метрики остановлены. "
                      "Как обновить: Meta for Developers → ваше приложение → Instagram → API setup with Instagram login → "
                      "Generate token для mybrand; вставьте его в GitHub → IGM → Settings → Secrets and variables → Actions "
                      "→ секрет IG_ACCESS_TOKEN и укажите сегодняшнюю дату в переменной IG_TOKEN_ISSUED_AT.")


def hook_signature(post_id, event, ts, secret):
    return hmac.new(secret.encode(), f"{post_id}:{event}:{int(ts)}".encode(), hashlib.sha256).hexdigest()


def notify(base, secret, post_id, event, transport=urlopen, timeout=25, text=None):
    if not secret:
        print(f"notify skipped: {event} {post_id}: PIPELINE_SECRET не задан")
        return False
    ts = int(time.time())
    payload = {"id": post_id, "event": event, "ts": ts}
    if text is not None:
        payload["text"] = str(text)[:500]
    body = json.dumps(payload, ensure_ascii=False).encode()
    req = Request(base.rstrip("/") + "/api/pipeline-hook", data=body, method="POST",
                  headers={"Content-Type": "application/json", "X-Pipeline-Signature": hook_signature(post_id, event, ts, secret)})
    try:
        with transport(req, timeout=timeout) as r:
            status = getattr(r, "status", 200)
            if not 200 <= status < 300:
                print(f"notify failed: {event} {post_id}: HTTP {status}")
                return False
            return True
    except Exception as err:  # notification failure must not undo a completed render/publish
        print(f"notify failed: {event} {post_id}: HTTP {getattr(err, 'code', None) or type(err).__name__}")
        return False


def send_alert(base, secret, text, transport=urlopen):
    """Событие alert (id P0000): бот пересылает text владельцу."""
    return notify(base, secret, ALERT_ID, "alert", transport=transport, text=str(text)[:500])


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


# ---------- git ----------
def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _restore_remote(paths):
    """После неудачного rebase: ветка = origin/main, все файлы как на сервере, кроме медиа этого поста
    (их использует повторная попытка). posts.json — серверный, без маркеров конфликта."""
    _git("reset", "-q", "--mixed", "origin/main")
    keep = [f":(exclude){Path(p).as_posix()}" for p in paths if Path(p).as_posix() != POSTS_PATH.as_posix()]
    _git("checkout", "origin/main", "--", ".", *keep)


def git_commit_push(message, paths):
    if any(Path(p).as_posix() == KICK_PATH.as_posix() for p in paths):
        raise PipelineError("Робот не пишет kick.json: его пишет только бот")
    subprocess.run(["git", "add", "--", *paths], check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        return False
    subprocess.run(["git", "commit", "-q", "-m", message], check=True)
    for attempt in range(4):
        if subprocess.run(["git", "push", "-q", "origin", "HEAD:main"]).returncode == 0:
            return True
        if _git("fetch", "-q", "origin", "main").returncode != 0:
            raise PipelineError("git fetch failed")
        if _git("rebase", "-q", "origin/main").returncode != 0:
            # Конфликт с ботом: не коммитим файл с маркерами и не затираем чужие правки
            _git("rebase", "--abort")
            _restore_remote(paths)
            raise ConcurrentUpdate("posts.json одновременно изменён ботом (конфликт rebase)")
    raise PipelineError("git push failed")


# ---------- зависшие посты ----------
STUCK_PUBLISH = timedelta(minutes=20)
STUCK_RENDER = timedelta(minutes=30)
ACTIVE = ("rendering", "publishing", "scheduled", "publishing_in_progress")


def last_change(post):
    times = [parse_iso(post.get("updated_at_utc"))]
    hist = post.get("history") or []
    if hist and isinstance(hist[-1], dict):
        times.append(parse_iso(hist[-1].get("at")))
    times = [t for t in times if t]
    return max(times) if times else None


def is_stuck(post, now=None):
    """Пост слишком долго в промежуточном статусе (запуск оборвался). Без отметок времени — не трогаем."""
    now = now or datetime.now(timezone.utc)
    changed = last_change(post)
    if not changed:
        return False
    if post.get("status") == "publishing_in_progress":
        return now - changed > STUCK_PUBLISH
    if post.get("status") == "rendering":
        return now - changed > STUCK_RENDER
    return False


def _render_retried(post):
    """Последнее изменение поста — наш перезапуск рендера (значит, он снова завис)."""
    retry, changed = parse_iso(post.get("render_retry")), last_change(post)
    return bool(retry and changed and retry >= changed - timedelta(seconds=5))


def _changes(before, after):
    """Правка, которую сделал робот: изменённые поля целиком + новые записи истории.
    Её можно повторить на свежей копии posts.json, не трогая правки бота в других постах."""
    missing = object()
    keys = [k for k in set(before) | set(after) if k != "history" and before.get(k, missing) != after.get(k, missing)]
    new_hist = (after.get("history") or [])[len(before.get("history") or []):]

    def mutate(p):
        for k in keys:
            if k in after:
                p[k] = copy.deepcopy(after[k])
            else:
                p.pop(k, None)
        p.setdefault("history", []).extend(copy.deepcopy(new_hist))
    return mutate


def run(env=os.environ, root=".", api_factory=None, renderer=playwright_render, video_maker=make_reel_video,
        commit=git_commit_push, notifier=notify, sleep=time.sleep, alerter=None, now=None):
    base = env.get("PIPELINE_BASE_URL") or DEFAULT_BASE
    secret = env.get("PIPELINE_SECRET", "")
    path = Path(root) / POSTS_PATH
    report = []
    if alerter is None:
        # в тестах notifier подменён — тогда и alert идёт через него (без сети)
        alerter = (lambda text: send_alert(base, secret, text)) if notifier is notify else (lambda text: notifier(base, secret, ALERT_ID, "alert"))

    def find(state, pid):
        return next((p for p in state["posts"] if p.get("id") == pid), None)

    def persist(pid, mutate, message, paths, expect=None):
        """Всегда: свежий posts.json с диска → правка только этого поста → коммит.
        Состояние, прочитанное до git pull/rebase, никогда не записываем (иначе откатим правки бота)."""
        for attempt in range(2):
            state = load_state(path)
            post = find(state, pid)
            if post is None:
                raise ConcurrentUpdate(f"{pid} исчез из posts.json")
            if expect and post.get("status") not in expect:
                raise ConcurrentUpdate(f"{pid}: бот сменил статус на {post.get('status')}")
            mutate(post)
            save_state(state, path)
            try:
                commit(message, paths)
                return post
            except ConcurrentUpdate:
                if attempt:
                    raise

    def failed(pid, stage, err, expect_alert=None):
        message = str(err)[:400]

        def mark(p):
            if p.get("status") in ACTIVE:  # статус, выставленный ботом/владельцем, не перетираем
                p["status"] = "failed"
                p["error"] = {"stage": stage, "message": message, "at": utc_now()}
            log(p, f"{stage}_failed", err)
        try:
            persist(pid, mark, f"pipeline: {stage} failed {pid}", [str(POSTS_PATH)])
        except ConcurrentUpdate as cu:
            report.append(f"{pid}: {stage} failed, not recorded ({cu})")
            return
        notifier(base, secret, pid, "failed")
        if isinstance(err, InstagramError) and err.token_expired:
            alerter(TOKEN_EXPIRED_TEXT)
        elif expect_alert:
            alerter(expect_alert)
        report.append(f"{pid}: {stage} failed: {err}")

    def mark_found(pid, found, kind, note):
        persist(pid, lambda p: apply_published(p, found, kind, note), f"pipeline: published {pid}", [str(POSTS_PATH)])
        notifier(base, secret, pid, "published")
        report.append(f"{pid}: published ({note})")

    for pid in work_plan(root, now):
        state = load_state(path)
        post = find(state, pid)
        if not post:
            continue
        status = post["status"]
        if status == "scheduled":  # время отложенной публикации пришло — публикуем как обычно
            if not is_due(post, now):
                continue
            status = "publishing"
        pending_saved = False
        try:
            if status == "rendering":
                if is_stuck(post, now):
                    if _render_retried(post):
                        failed(pid, "render", PipelineError("Рендер завис повторно (нет результата больше 30 минут)"),
                               expect_alert=f"Пост {pid}: рендер завис дважды и помечен failed. Проверьте Actions → Post Pipeline.")
                        continue
                    stamp = utc_now()

                    def retry(p, stamp=stamp):
                        log(p, "render_retry", "рендер завис больше 30 минут — пробуем ещё раз")
                        p["render_retry"] = p["updated_at_utc"] = stamp
                    persist(pid, retry, f"pipeline: re-render {pid}", [str(POSTS_PATH)], expect=("rendering",))
                    state = load_state(path)
                    post = find(state, pid)
                before = copy.deepcopy(post)
                render_post(post, base, root, renderer, video_maker)
                change = _changes(before, post)

                def rendered(p, change=change):
                    change(p)
                    p.pop("render_retry", None)
                persist(pid, rendered, f"pipeline: render {pid}", [str(POSTS_PATH), str(MEDIA_DIR / pid)], expect=("rendering",))
                notifier(base, secret, pid, "final_ready")
                report.append(f"{pid}: rendered")
            elif status == "publishing_in_progress":
                # Сверка зависшей публикации: пост мог выйти, а запуск оборваться
                status = "reconcile"
                api = (api_factory or _default_api)()
                found = find_published(api, post, post_caption(post))
                if found:
                    mark_found(pid, found, media_kind(post), "найден в Instagram после зависания")
                else:
                    failed(pid, "publish_uncertain", PipelineError("Публикация прервалась; в Instagram пост не найден. Проверьте профиль и при необходимости повторите"),
                           expect_alert=f"Пост {pid} завис при публикации больше 20 минут и в Instagram не найден. Проверьте профиль mybrand; если поста нет — нажмите «Повторить».")
            elif status == "publishing":
                if not secret:
                    raise PipelineError("PIPELINE_SECRET не задан в IGM")
                can_publish(post, env)
                # Lock first so a re-run can never publish twice
                persist(pid, lambda p: (p.__setitem__("status", "publishing_in_progress"), log(p, "publish_started")),
                        f"pipeline: publishing {pid}", [str(POSTS_PATH)], expect=("publishing", "scheduled"))
                post = find(load_state(path), pid)
                api = (api_factory or _default_api)()
                if post.get("ig_pending") or (post.get("error") or {}).get("stage") == "publish_uncertain":
                    # прошлый исход неизвестен: сначала проверяем, не вышел ли пост
                    found = find_published(api, post, post_caption(post))
                    if found:
                        mark_found(pid, found, media_kind(post), "уже был опубликован — повтор не нужен")
                        continue

                def on_container(cid, pid=pid):
                    nonlocal pending_saved
                    persist(pid, lambda p: (p.__setitem__("ig_pending", {"container_id": str(cid), "at": utc_now()}), log(p, "container_ready", cid)),
                            f"pipeline: container {pid}", [str(POSTS_PATH)], expect=("publishing_in_progress",))
                    pending_saved = True
                result, kind = send_to_instagram(post, api, base, secret, env, sleep, on_container)
                persist(pid, lambda p: apply_published(p, result, kind), f"pipeline: published {pid}", [str(POSTS_PATH)])
                notifier(base, secret, pid, "published")
                report.append(f"{pid}: published")
        except ConcurrentUpdate as err:  # бот поменял posts.json одновременно — пропускаем, следующий запуск повторит
            report.append(f"{pid}: skipped, concurrent update: {err}")
        except Exception as err:  # record and tell the owner; never retry a publish silently
            if status == "rendering":
                failed(pid, "render", err)
                continue
            if status == "reconcile":
                failed(pid, "publish_uncertain", err, expect_alert=f"Пост {pid} завис при публикации, проверить Instagram не удалось: {str(err)[:200]}")
                continue
            if pending_saved:
                # запрос media_publish мог дойти до Meta: проверяем, прежде чем объявить ошибку
                try:
                    post = find(load_state(path), pid)
                    found = find_published((api_factory or _default_api)(), post, post_caption(post))
                except Exception:
                    found = None
                if found:
                    mark_found(pid, found, media_kind(post), "ответ Meta потерян, пост найден")
                    continue
                failed(pid, "publish_uncertain", err, expect_alert=None if isinstance(err, InstagramError) and err.token_expired else
                       f"Пост {pid}: исход публикации неизвестен ({str(err)[:150]}). Проверьте профиль перед «Повторить» — робот сверит сам.")
                continue
            failed(pid, "publish", err)
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


def work_plan(root=".", now=None):
    """{id: вид работы}: render — нужен браузер/ffmpeg; publish и reconcile — только API Instagram."""
    state = load_state(Path(root) / POSTS_PATH)
    plan = {}
    for p in state["posts"]:
        status = p.get("status")
        if status == "rendering":
            plan[p["id"]] = "render"
        elif status == "publishing" or is_due(p, now):
            plan[p["id"]] = "publish"
        elif status == "publishing_in_progress" and is_stuck(p, now):
            plan[p["id"]] = "reconcile"
    return plan


def needs_work(root=".", now=None):
    return list(work_plan(root, now))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["pending", "run"])
    args = parser.parse_args()
    if args.action == "pending":
        plan = work_plan()
        print(" ".join(f"{pid}:{kind}" for pid, kind in plan.items()))
        out = os.getenv("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"work={'yes' if plan else 'no'}\n")
                f.write(f"render={'yes' if 'render' in plan.values() else 'no'}\n")
                f.write(f"kinds={','.join(sorted(set(plan.values())))}\n")
        return
    if not os.getenv("PIPELINE_SECRET"):
        print("::error::PIPELINE_SECRET не задан в секретах IGM: робот не сможет сообщить боту о рендере/публикации. Добавьте секрет и перезапустите.")
        sys.exit(1)
    for line in run():
        print(line)


if __name__ == "__main__":
    main()
