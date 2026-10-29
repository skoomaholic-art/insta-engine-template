"""Zero-contact comment moderation using only official Meta Instagram endpoints.

No follower enumeration, password access, DM sending or account scraping.
"""
from .instagram import InstagramAPI, InstagramError
from .policy import is_blocked_username

MAX_PAGES_PER_MEDIA = 3
MAX_MEDIA = 50


def moderate_comments(api=None):
    api = api or InstagramAPI()
    api.assert_correct_account()
    checked = removed = unidentified = 0
    media = api.recent_media(MAX_MEDIA)
    for item in media:
        mid = str(item.get("id", ""))
        if not mid.isdigit():
            continue
        cursor = None
        cursors_seen = set()
        for _ in range(MAX_PAGES_PER_MEDIA):
            page = api.list_comments(mid, after=cursor)
            if not isinstance(page, dict):
                raise InstagramError("Invalid Meta comments response")
            comments = page.get("data", [])
            if not isinstance(comments, list):
                raise InstagramError("Invalid Meta comments data")
            for comment in comments:
                checked += 1
                username = comment.get("username")
                if not username:
                    unidentified += 1
                    continue
                if is_blocked_username(username):
                    cid = str(comment.get("id", ""))
                    if not cid.isdigit():
                        raise InstagramError("Blocked-user comment lacks valid comment ID")
                    api.delete_comment(cid)
                    removed += 1
            cursor = page.get("paging", {}).get("cursors", {}).get("after")
            if not cursor or cursor in cursors_seen or len(comments) < 100:
                break
            cursors_seen.add(cursor)
    return {
        "media_checked": len(media),
        "comments_checked": checked,
        "comments_deleted": removed,
        "missing_username": unidentified,
        "scan_limit": f"Last {MAX_MEDIA} media, up to {MAX_PAGES_PER_MEDIA * 100} top-level comments each",
    }


def render_moderation(report):
    return ("## IGM: модерация комментариев\n\n"
            f"- Проверено последних публикаций: {report['media_checked']}\n"
            f"- Проверено комментариев: {report['comments_checked']}\n"
            f"- Удалено по стоп-листу: {report['comments_deleted']}\n"
            f"- Не удалось определить автора: {report['missing_username']}\n\n"
            "Проверка ограничена последними 50 публикациями и первыми 300 комментариями под каждой. "
            "Это не мгновенная модерация и не удаление подписчиков. "
            "Блокировка нежелательного аккаунта в Instagram остаётся наиболее надёжной защитой.")
