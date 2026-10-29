"""Account safety policy for Instagram Growth Manager.

This is an exact username match, not a fuzzy name search.
"""
BLOCKED_USERNAMES = frozenset({"aika_st"})

class InteractionProhibited(ValueError):
    pass

def normalize_username(username):
    return str(username or "").strip().lstrip("@").casefold()

def is_blocked_username(username):
    return normalize_username(username) in BLOCKED_USERNAMES

def require_allowed_recipient(username):
    if is_blocked_username(username):
        raise InteractionProhibited("Outbound interaction prohibited by owner policy")
    return username

def policy_status():
    return ("## IGM Account Safety Policy\n\n"
            "- @Aika_St: **никакого взаимодействия** (DM, ответы, лайки, упоминания, подписки).\n"
            "- Комментарии от точного имени пользователя будут удаляться при доступной модерации API.\n"
            "- **Принудительное удаление подписчиков через официальный API недоступно.**\n"
            "- Для запрета подписки необходимо вручную заблокировать аккаунт в Instagram.\n"
            "- Никаких действий с подписчиками IGM не совершает.\n")
