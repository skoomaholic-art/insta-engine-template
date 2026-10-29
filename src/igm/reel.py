"""Живые Reels из слайдов конструктора: движение фона, появление текста, переходы, музыка.

Каждый слайд приходит двумя слоями: фон (JPEG, с затемнением) и текст с логотипом (прозрачный PNG).
ffmpeg двигает фон (наезд, отъезд, панорама), плавно выводит текст, склеивает сцены переходами
и кладёт музыку с плавным началом и концом. Всё бесплатно, работает на GitHub Actions.
"""
import json
import random
import shutil
import subprocess
from pathlib import Path

FPS = 30
XFADE = 0.5
W, H = 1080, 1920
MAX_SECONDS = 90
MOODS = ("tense", "calm", "epic", "sad", "upbeat", "mystery")
MUSIC_DIR = Path("media/music")
MUSIC_INDEX = Path("content/music.json")

# движения фона: наезд, отъезд, панорама вправо, панорама вверх
MOTIONS = [
    ("1+0.10*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    ("1.10-0.10*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    ("1.12", "(iw-iw/zoom)*on/{n}", "ih/2-(ih/zoom/2)"),
    ("1.12", "iw/2-(iw/zoom/2)", "(ih-ih/zoom)*(1-on/{n})"),
]
TRANSITIONS = ["fade", "slideleft", "smoothup", "fadeblack", "slideup"]

# Пресеты Reels: темп сцен, переходы, сила движения, настроение музыки. ИИ выбирает по теме, владелец может сменить
REEL_STYLES = {
    "dynamic":   {"label": "Динамичный",  "first": 2.6, "lo": 2.2, "hi": 4.0, "per_word": 0.28, "xfade": 0.3, "fade_in": 0.25,
                  "transitions": ["slideleft", "smoothup", "slideright", "wipeleft", "zoomin"], "zoom": 0.16, "music": ["upbeat", "epic", "tense"]},
    "calm":      {"label": "Спокойный",   "first": 3.8, "lo": 4.0, "hi": 8.0, "per_word": 0.45, "xfade": 0.9, "fade_in": 0.7,
                  "transitions": ["fade", "dissolve"], "zoom": 0.06, "music": ["calm", "sad"]},
    "epic":      {"label": "Эпичный",     "first": 3.2, "lo": 3.0, "hi": 6.0, "per_word": 0.38, "xfade": 0.6, "fade_in": 0.45,
                  "transitions": ["fadeblack", "fade", "circleopen"], "zoom": 0.14, "music": ["epic"]},
    "thriller":  {"label": "Напряжённый", "first": 3.0, "lo": 2.6, "hi": 5.0, "per_word": 0.34, "xfade": 0.35, "fade_in": 0.3,
                  "transitions": ["fadeblack", "hblur", "fade"], "zoom": 0.12, "music": ["tense", "mystery"]},
    "nostalgic": {"label": "Ностальгия",  "first": 3.6, "lo": 3.6, "hi": 7.0, "per_word": 0.42, "xfade": 0.8, "fade_in": 0.6,
                  "transitions": ["fade", "dissolve", "fadewhite"], "zoom": 0.07, "music": ["calm", "sad"]},
    "mystery":   {"label": "Загадочный",  "first": 3.4, "lo": 3.2, "hi": 6.5, "per_word": 0.4, "xfade": 0.7, "fade_in": 0.6,
                  "transitions": ["dissolve", "fadeblack", "fade"], "zoom": 0.09, "music": ["mystery", "tense"]},
}
DEFAULT_STYLE = "dynamic"


def style_of(name):
    return REEL_STYLES.get(name) or REEL_STYLES[DEFAULT_STYLE]


class ReelError(Exception):
    pass


def scene_durations(slides, style=None):
    """Сколько держать сцену: по количеству слов на экране (успеть прочитать) и темпу пресета."""
    st = style_of(style)
    out = []
    for i, sl in enumerate(slides):
        words = len(f"{sl.get('title', '')} {sl.get('subtitle', '')}".split())
        d = st["first"] if i == 0 else 1.6 + words * st["per_word"]
        out.append(round(min(st["hi"], max(st["lo"] if i else st["first"], d)), 2))
    total = sum(out) - st["xfade"] * (len(out) - 1)
    if total > MAX_SECONDS:  # не длиннее лимита
        k = MAX_SECONDS / total
        out = [round(d * k, 2) for d in out]
    return out


def _scene(x):
    """Сцена: картинка (двигаем zoompan), готовое видео (2.5D) или клип владельца."""
    if isinstance(x, dict):
        return x
    return {"kind": "video" if str(x).endswith(".mp4") else "still", "path": x}


def build_command(bgs, fgs, durations, out_path, music=None, seed=0, style=None):
    if not (len(bgs) == len(fgs) == len(durations)) or not bgs:
        raise ReelError("Нужны фон и текст на каждую сцену")
    scenes = [_scene(b) for b in bgs]
    st = style_of(style)
    xf, fade_in = st["xfade"], st["fade_in"]
    rnd = random.Random(seed)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for sc, fg, d in zip(scenes, fgs, durations):
        if sc["kind"] == "still":
            cmd += ["-i", str(sc["path"])]
        else:  # видео: при нехватке длины зацикливаем
            cmd += ["-stream_loop", "-1", "-t", f"{d:.2f}", "-i", str(sc["path"])]
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{d:.2f}", "-i", str(fg)]
    total = sum(durations) - xf * (len(durations) - 1)
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
    else:
        cmd += ["-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=44100:cl=stereo"]
    audio_idx = 2 * len(scenes)
    parts = []
    for i, (sc, d) in enumerate(zip(scenes, durations)):
        n = int(round(d * FPS))
        if sc["kind"] == "still":
            z, x, y = MOTIONS[(i + rnd.randrange(len(MOTIONS))) % len(MOTIONS)] if i else MOTIONS[0]
            k = st["zoom"] / 0.10  # сила движения пресета
            z = z.replace("0.10", f"{0.10 * k:.3f}").replace("1.12", f"{1 + 0.12 * k:.3f}")
            z, x, y = (e.format(n=n) for e in (z, x, y))
            parts.append(f"[{2 * i}:v]scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
                         f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS},setsar=1,format=yuv420p[b{i}]")
        else:
            dim = float(sc.get("dim") or 0)
            shade = f",lutrgb=r=val*{1 - dim:.2f}:g=val*{1 - dim:.2f}:b=val*{1 - dim:.2f}" if dim > 0 else ""
            parts.append(f"[{2 * i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1{shade},"
                         f"trim=duration={d:.2f},setpts=PTS-STARTPTS,format=yuv420p[b{i}]")
        if i == 0:  # крючок: текст виден с первого кадра (по нему Instagram делает превью)
            parts.append(f"[{2 * i + 1}:v]format=rgba[f{i}]")
            parts.append(f"[b{i}][f{i}]overlay=x=0:y=0:shortest=1,trim=duration={d:.2f},setpts=PTS-STARTPTS,format=yuv420p[s{i}]")
        else:
            parts.append(f"[{2 * i + 1}:v]format=rgba,fade=t=in:st=0.1:d={fade_in:.2f}:alpha=1[f{i}]")
            parts.append(f"[b{i}][f{i}]overlay=x=0:y='if(lt(t,0.6),40*(1-t/0.6),0)':shortest=1,trim=duration={d:.2f},setpts=PTS-STARTPTS,format=yuv420p[s{i}]")
    last, offset = "s0", 0.0
    for i in range(1, len(durations)):
        offset += durations[i - 1] - xf
        tr = st["transitions"][(i - 1) % len(st["transitions"])]
        parts.append(f"[{last}][s{i}]xfade=transition={tr}:duration={xf}:offset={offset:.2f}[x{i}]")
        last = f"x{i}"
    fade_out = max(0.0, total - 1.6)
    parts.append(f"[{audio_idx}:a]atrim=0:{total:.2f},asetpts=PTS-STARTPTS,afade=t=in:d=0.6,afade=t=out:st={fade_out:.2f}:d=1.5,volume=0.85[a]")
    cmd += ["-filter_complex", ";".join(parts), "-map", f"[{last}]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-t", f"{total:.2f}", "-movflags", "+faststart", str(out_path)]
    return cmd, total


def make_motion_reel(bgs, fgs, slides, out_path, music=None, seed=0, depth=None, clips=None, style=None):
    """depth: индексы сцен с фото → 2.5D; clips: {индекс: путь к видео владельца}."""
    if not shutil.which("ffmpeg"):
        raise ReelError("ffmpeg is not available")
    durations = scene_durations(slides, style)
    clips, depth = clips or {}, set(depth or [])
    scenes = []
    for i, b in enumerate(bgs):
        if i in clips:
            scenes.append({"kind": "video", "path": clips[i], "dim": 0.35})
        elif i in depth:
            try:
                from .parallax import parallax_clip
                out = Path(out_path).with_name(f"_p{i}.mp4")
                parallax_clip(b, out, durations[i] + 0.1, move=i, strength=style_of(style)["zoom"] / 0.10)
                scenes.append({"kind": "video", "path": out})
            except Exception:  # нет модели или OpenCV — обычное движение
                scenes.append({"kind": "still", "path": b})
        else:
            scenes.append({"kind": "still", "path": b})
    bgs = scenes
    cmd, total = build_command(bgs, fgs, durations, out_path, music, seed, style)
    subprocess.run(cmd, check=True)
    for tmp in Path(out_path).parent.glob("_p*.mp4"):
        tmp.unlink()
    return {"seconds": round(total, 1), "scenes": len(durations), "style": style if style in REEL_STYLES else DEFAULT_STYLE,
            "depth": sorted(depth - set(clips)), "clips": sorted(clips)}


# ---------- музыкальная библиотека ----------
def load_library(root="."):
    p = Path(root) / MUSIC_INDEX
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [t for t in data.get("tracks", []) if (Path(root) / t.get("file", "")).exists() and t.get("enabled", True)]


def pick_track(mood, root=".", seed=0, exclude=()):
    """Трек под настроение; если такого нет — любой, кроме недавних."""
    lib = load_library(root)
    if not lib:
        return None
    rnd = random.Random(seed)
    same = [t for t in lib if t.get("mood") == mood and t["file"] not in exclude]
    pool = same or [t for t in lib if t["file"] not in exclude] or lib
    return rnd.choice(pool)


def music_credit(track):
    """Строка в подпись, если лицензия требует указать автора."""
    if not track or not track.get("credit"):
        return None
    return f"Музыка: {track['credit']}"
