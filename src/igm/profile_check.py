"""Read-only check that the Instagram profile matches the approved branding.

Instagram API cannot edit name/BIO/avatar; the owner sets them in the app.
This only reads public profile fields and compares them with brand/profile-expected.json.
"""
import json
import unicodedata
from pathlib import Path

from .instagram import InstagramAPI

EXPECTED = Path(__file__).resolve().parents[2] / "brand" / "profile-expected.json"
PROFILE_FIELDS = "id,username,name,biography,profile_picture_url"


def _norm(text):
    text = unicodedata.normalize("NFC", str(text or "")).replace("\r\n", "\n")
    return "\n".join(line.strip() for line in text.strip().split("\n"))


def compare(actual, expected):
    """Pure comparison; returns list of (field, ok, note)."""
    rows = []
    for field in ("username", "name", "biography"):
        want, got = _norm(expected.get(field)), _norm(actual.get(field))
        if field == "username":
            want, got = want.lstrip("@").lower(), got.lstrip("@").lower()
        rows.append((field, got == want, "совпадает" if got == want else "ожидалось другое"))
    has_pic = bool(str(actual.get("profile_picture_url") or "").startswith("https://"))
    rows.append(("avatar", has_pic, "фото установлено" if has_pic else "фото не найдено"))
    return rows


def render_check(api=None, expected_path=EXPECTED):
    expected = json.loads(Path(expected_path).read_text(encoding="utf-8"))
    api = api or InstagramAPI()
    actual = api.request(api.account_id, params={"fields": PROFILE_FIELDS})
    labels = {"username": "Имя пользователя", "name": "Имя", "biography": "BIO", "avatar": "Аватар"}
    rows = compare(actual, expected)
    lines = ["## Проверка оформления профиля", ""]
    for field, ok, note in rows:
        lines.append(f"- {'✅' if ok else '❌'} {labels[field]}: {note}")
    if not all(ok for _, ok, _ in rows):
        lines += ["", "Сейчас в Instagram:", "", f"- Имя: `{actual.get('name') or '—'}`", "- BIO:", "", "```", str(actual.get("biography") or "—"), "```"]
    lines += ["", "Аватар проверяется только на наличие: API не даёт сравнить само изображение."]
    return "\n".join(lines)
