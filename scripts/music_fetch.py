"""Стартовая музыкальная библиотека для Reels: треки Kevin MacLeod (incompetech.com), лицензия CC BY 4.0.
Скачивает, обрезает до 100 секунд, сжимает и пишет content/music.json. Запускается вручную (music-library.yml)."""
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

TRACKS = {
    "tense": ["Volatile Reaction", "Ossuary 6 - Air", "Hitman", "The Descent", "Darkest Child"],
    "calm": ["Long Note Four", "Dreamy Flashback", "Lost Frontier", "Floating Cities"],
    "epic": ["Impact Moderato", "Heroic Age", "Five Armies", "Crusade"],
    "sad": ["Sad Trio", "Heartbreaking", "Wounded"],
    "upbeat": ["Sneaky Snitch", "Local Forecast - Elevator", "Monkeys Spinning Monkeys", "Investigations"],
    "mystery": ["Ghost Story", "Night on the Docks - Sax", "Unholy Knight", "Comfortable Mystery 4"],
}
BASE = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/"
OUT = Path("media/music")
INDEX = Path("content/music.json")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    data = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {"schema_version": 1, "tracks": []}
    have = {t["file"] for t in data["tracks"]}
    report = []
    for mood, titles in TRACKS.items():
        for title in titles:
            slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")
            file = f"{OUT}/{mood}-{slug}.mp3"
            if file in have:
                report.append(f"= {title}")
                continue
            raw = Path(f"/tmp/{slug}.mp3")
            try:
                req = urllib.request.Request(BASE + urllib.parse.quote(title) + ".mp3", headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    raw.write_bytes(r.read())
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-t", "100", "-af", "afade=t=out:st=97:d=3",
                                "-ac", "2", "-ar", "44100", "-b:a", "128k", file], check=True)
            except Exception as err:
                report.append(f"x {title}: {err}")
                continue
            data["tracks"].append({"file": file, "title": title, "mood": mood, "author": "Kevin MacLeod",
                                   "license": "CC BY 4.0", "source": "incompetech.com",
                                   "credit": f"Kevin MacLeod, «{title}» (incompetech.com), CC BY 4.0", "enabled": True})
            report.append(f"+ {title} ({mood})")
    data["note"] = "Музыка для Reels. mood: tense|calm|epic|sad|upbeat|mystery. credit добавляется в подпись. enabled=false — не использовать трек"
    INDEX.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n".join(report))
    print(f"TOTAL {len(data['tracks'])}")
    return 0 if data["tracks"] else 1


if __name__ == "__main__":
    sys.exit(main())
