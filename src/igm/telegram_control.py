"""Owner-only Telegram review inbox for Instagram Growth Manager.

Free transport: Telegram Bot API getUpdates via scheduled GitHub Actions.
The bot NEVER posts on Instagram. It records approvals, edits, and rejections.
"""
import base64
import json
import os
import re
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .policy import is_blocked_username

QUEUE_PATH = "data/telegram/review-queue.json"
VALID_ACTIONS = frozenset(("approve", "reject", "revise"))
MAX_UPDATES = 50
MAX_ITEMS_SHOWN = 8
ITEM_ID = re.compile(r"^[A-Za-z0-9_-]{3,36}$")


class TelegramReviewError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_authorized(update, owner_chat_id):
    """Restrict to private messages from the one configured owner account."""
    message = update.get("message") or update.get("callback_query", {}).get("message") or {}
    actor = (update.get("callback_query") or {}).get("from") or message.get("from") or {}
    chat = message.get("chat") or {}
    owner = str(owner_chat_id or "")
    return bool(owner) and str(actor.get("id")) == owner and str(chat.get("id")) == owner and chat.get("type") == "private"


def safe_review_item(item):
    """No replies to a prohibited user and never silently authorize external comments."""
    if item.get("status") not in ("pending", "needs_revision"):
        return False
    if not ITEM_ID.fullmatch(str(item.get("id", ""))):
        return False
    if item.get("kind") not in ("reply", "post", "external_comment"):
        return False
    if is_blocked_username(item.get("target_username")):
        return False
    return bool(str(item.get("proposed_text") or "").strip())


def apply_action(state, item_id, action, owner_id, when=None):
    """Pure transition; approving records evidence only, never calls Instagram."""
    if action not in VALID_ACTIONS or not ITEM_ID.fullmatch(str(item_id)):
        raise TelegramReviewError("Invalid action or item ID")
    item = next((i for i in state.get("items", []) if i.get("id") == item_id), None)
    if not item or item.get("status") != "pending":
        raise TelegramReviewError("Card is missing or already decided")
    if is_blocked_username(item.get("target_username")):
        raise TelegramReviewError("Interaction prohibited by account safety policy")
    if not safe_review_item(item):
        raise TelegramReviewError("Review card is incomplete")
    when = when or utc_now()
    status = {"approve": "approved", "reject": "rejected", "revise": "needs_revision"}[action]
    item["status"] = status
    item["owner_review"] = {"action": action, "owner_id": str(owner_id), "at_utc": when}
    state.setdefault("decisions", []).append({
        "item_id": item_id,
        "kind": item["kind"],
        "context": {
            "topic": item.get("topic"),
            "situation": item.get("situation"),
            "target_username": item.get("target_username"),
            "original_comment": str(item.get("original_comment", ""))[:1200],
        },
        "draft": item["proposed_text"],
        "decision": action,
        "at_utc": when,
    })
    return item


def edit_card(state, item_id, new_text, owner_id, when=None):
    if not ITEM_ID.fullmatch(str(item_id)) or not new_text or not new_text.strip() or len(new_text) > 2000:
        raise TelegramReviewError("Invalid card ID or text length")
    item = next((i for i in state.get("items", []) if i.get("id") == item_id), None)
    if not item or item.get("status") not in ("needs_revision", "pending"):
        raise TelegramReviewError("Only pending or revision-requested cards may be edited")
    if is_blocked_username(item.get("target_username")):
        raise TelegramReviewError("Interaction prohibited by account safety policy")
    old_text = item.get("proposed_text", "")
    item["proposed_text"] = new_text.strip()
    item["status"] = "pending"
    item.pop("owner_review", None)
    state.setdefault("decisions", []).append({
        "item_id": item_id, "kind": item["kind"],
        "context": {"topic": item.get("topic"), "situation": item.get("situation"), "target_username": item.get("target_username")},
        "draft": old_text, "owner_revision": new_text.strip(), "decision": "edit",
        "at_utc": when or utc_now(), "owner_id": str(owner_id)
    })
    return item


def format_card(item):
    topic = str(item.get("topic") or "без темы")[:70]
    question = str(item.get("original_comment") or "")[:600]
    username = str(item.get("target_username") or "")[:80]
    proposal = str(item.get("proposed_text") or "")[:1300]
    lines = [f"IGM • карточка {item['id']}", f"Тип: {item['kind']}", f"Тема: {topic}"]
    if username:
        lines.append(f"Автор: @{username.lstrip('@')}")
    if question:
        lines.append(f"Исходный комментарий: {question}")
    lines.extend(["", "Вариант:", proposal, "", "После одобрения действие будет сохранено в базе. Отправка через Instagram отдельно защищена."])
    return "\n".join(lines)[:3950]


class TelegramAPI:
    def __init__(self, token=None, transport=None):
        self.token = token or os.getenv("TG_BOT_TOKEN", "")
        if not self.token or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", self.token):
            raise TelegramReviewError("TG_BOT_TOKEN is missing/invalid")
        self.transport = transport or urlopen
        self.base = "https://api.telegram.org/bot" + self.token + "/"

    def call(self, method, payload):
        request = Request(
            self.base + method,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.transport(request, timeout=25) as response:
                data = json.load(response)
        except (HTTPError, URLError, TimeoutError):
            raise TelegramReviewError("Telegram Bot API request failed") from None
        if data.get("ok") is not True:
            raise TelegramReviewError("Telegram Bot API returned an error")
        return data.get("result")

    def send(self, chat_id, text, keyboard=None):
        payload = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
        if keyboard is not None:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        return self.call("sendMessage", payload)

    def ack(self, callback_id, text):
        try:
            self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:180]})
        except TelegramReviewError:
            # Scheduled polling might be too slow for callback acknowledgement.
            pass


class GitHubReviewStore:
    def __init__(self, repository=None, token=None, transport=None):
        repository = repository or os.getenv("GITHUB_REPOSITORY", "")
        token = token or os.getenv("GITHUB_TOKEN", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not token:
            raise TelegramReviewError("GitHub repository and token required")
        self.url = "https://api.github.com/repos/" + repository + "/contents/" + QUEUE_PATH
        self.headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self.transport = transport or urlopen

    def load(self):
        try:
            with self.transport(Request(self.url, headers=self.headers), timeout=20) as response:
                data = json.load(response)
            state = json.loads(base64.b64decode(data["content"]))
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError):
            raise TelegramReviewError("Review queue cannot be read") from None
        if state.get("schema_version") != 1 or not isinstance(state.get("items"), list) or not isinstance(state.get("decisions"), list):
            raise TelegramReviewError("Invalid review queue schema")
        return state, data["sha"]

    def save(self, state, sha):
        body = {
            "message": "telegram: persist owner review decisions and update offset",
            "sha": sha,
            "content": base64.b64encode((json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)+"\n").encode()).decode(),
        }
        request = Request(self.url, data=json.dumps(body).encode(), headers=self.headers, method="PUT")
        try:
            with self.transport(request, timeout=20) as response:
                result = json.load(response)
            return result["content"]["sha"]
        except (HTTPError, URLError, TimeoutError, KeyError):
            raise TelegramReviewError("Review queue write failed") from None


def _pending(state, limit=MAX_ITEMS_SHOWN):
    return [i for i in state["items"] if i.get("status") == "pending" and safe_review_item(i)][:limit]


def menu_keyboard():
    return [
        [{"text": "📝 Публикации", "callback_data": "menu:posts"},
         {"text": "💬 Комментарии", "callback_data": "menu:comments"}],
        [{"text": "📥 На проверку", "callback_data": "menu:pending"},
         {"text": "📊 Статус", "callback_data": "menu:status"}],
    ]


def review_buttons(card):
    return [[
        {"text": "✅ Одобрить", "callback_data": "review:"+card["id"]+":approve"},
        {"text": "✏️ Править", "callback_data": "review:"+card["id"]+":revise"},
        {"text": "⏭ Отклонить", "callback_data": "review:"+card["id"]+":reject"},
    ]]


def review_list(state, owner_id, kind=None):
    cards = _pending(state)
    if kind == "posts":
        cards = [x for x in cards if x.get("kind") == "post"]
    if kind == "comments":
        cards = [x for x in cards if x.get("kind") in ("reply", "external_comment")]
    if not cards:
        return [("send", int(owner_id), "IGM: новых карточек для этой категории нет.", menu_keyboard())]
    return [("send", int(owner_id), format_card(card), review_buttons(card)) for card in cards]


def count_status(state):
    counts = {}
    for card in state["items"]:
        label = str(card.get("status", "unknown"))
        counts[label] = counts.get(label, 0) + 1
    return "IGM: " + json.dumps(counts, ensure_ascii=False) + "\nОтветы и публикации не отправляются автоматически."


def handle_update(update, state, owner_id):
    """Return outgoing messages after a durable state commit. No Instagram calls."""
    if not is_authorized(update, owner_id):
        return []
    cb = update.get("callback_query")
    if cb:
        data = str(cb.get("data", ""))
        parts = data.split(":")
        if len(parts) == 2 and parts[0] == "menu":
            choice = parts[1]
            if choice in ("posts", "comments", "pending"):
                return [("ack", cb["id"], "Список открыт")] + review_list(state, owner_id, None if choice == "pending" else choice)
            if choice == "status":
                return [("ack", cb["id"], "Статус")] + [("send", int(owner_id), count_status(state), menu_keyboard())]
            return []
        if len(parts) != 3 or parts[0] != "review":
            return []
        _, item_id, action = parts
        try:
            card = apply_action(state, item_id, action, owner_id)
            text = f"Карточка {item_id}: {card['status']}. Решение записано."
            if action == "revise":
                text += f" Напиши: /edit {item_id} новый текст"
            return [("ack", cb["id"], text), ("send", int(owner_id), text, None)]
        except TelegramReviewError as err:
            return [("ack", cb["id"], str(err))]
    message = update.get("message", {})
    text = str(message.get("text") or "").strip()
    if text.startswith("/edit "):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            return [("send", int(owner_id), "Используй /edit ID новый текст", None)]
        try:
            card = edit_card(state, parts[1], parts[2], owner_id)
            return [("send", int(owner_id), format_card(card), review_buttons(card))]
        except TelegramReviewError as err:
            return [("send", int(owner_id), str(err), None)]
    if text.startswith("/pending"):
        return review_list(state, owner_id)
    if text.startswith("/posts"):
        return review_list(state, owner_id, "posts")
    if text.startswith("/comments"):
        return review_list(state, owner_id, "comments")
    if text.startswith("/why "):
        parts = text.split(maxsplit=2)
        if len(parts) != 3 or not ITEM_ID.fullmatch(parts[1]) or not parts[2].strip() or len(parts[2]) > 1000:
            return [("send", int(owner_id), "Используй /why ID причина (до 1000 символов)", None)]
        card = next((i for i in state["items"] if i.get("id") == parts[1]), None)
        if card is None or not any(d.get("item_id") == parts[1] for d in state["decisions"]):
            return [("send", int(owner_id), "Не найдено решение по этой карточке.", None)]
        state["decisions"].append({
            "item_id": parts[1], "decision": "owner_reason", "reason": parts[2].strip(),
            "at_utc": utc_now(), "kind": card.get("kind"),
            "context": {"topic": card.get("topic"), "situation": card.get("situation")},
        })
        return [("send", int(owner_id), f"Причина решения по {parts[1]} записана.", None)]
    if text.startswith("/status"):
        return [("send", int(owner_id), count_status(state), menu_keyboard())]
    if text.startswith("/start") or text.startswith("/help"):
        return [("send", int(owner_id), "IGM • Telegram пульт\n/pending - все карточки\n/posts - публикации\n/comments - ответы и свои комментарии\n/status - состояние\n/edit ID текст - исправить\n/why ID причина - объяснить решение\n\nНичего не публикуется автоматически.", menu_keyboard())]
    return []


def run_poll(bot=None, store=None, owner_id=None):
    bot = bot or TelegramAPI()
    owner_id = owner_id or os.getenv("TG_OWNER_CHAT_ID", "").strip()
    # Read-only bootstrapping: tell any user their own chat ID, never approve content.
    if not owner_id:
        updates = bot.call("getUpdates", {"timeout": 0, "limit": MAX_UPDATES, "allowed_updates": ["message"]})
        for update in updates[-5:]:
            msg = update.get("message") or {}
            chat = msg.get("chat") or {}
            actor = msg.get("from") or {}
            if str(msg.get("text", "")).strip().startswith("/start") and chat.get("type") == "private" and chat.get("id") == actor.get("id"):
                bot.send(chat["id"], "IGM setup: твой Telegram chat ID: " + str(chat["id"]) + "\nДобавь его в GitHub Secret TG_OWNER_CHAT_ID. Пока он не добавлен, действия заблокированы.")
        # Confirm bootstrap updates to prevent repeated /start replies on every scheduled run.
        if updates:
            last = max((u.get("update_id", -1) for u in updates), default=-1)
            if last >= 0:
                bot.call("getUpdates", {"offset": last + 1, "timeout": 0, "limit": 1})
        return "IGM Telegram: waiting for owner chat ID in GitHub Secrets. No decisions processed."
    if not owner_id.isdigit() or len(owner_id) > 25:
        raise TelegramReviewError("TG_OWNER_CHAT_ID must be numeric")
    store = store or GitHubReviewStore()
    state, sha = store.load()
    offset = int(state.get("last_update_id", 0))
    updates = bot.call("getUpdates", {
        "offset": offset + 1, "timeout": 0, "limit": MAX_UPDATES,
        "allowed_updates": ["message", "callback_query"]
    })
    handled = 0
    for update in updates:
        update_id = update.get("update_id")
        if not isinstance(update_id, int) or update_id <= offset:
            continue
        outgoing = handle_update(update, state, owner_id)
        # Commit outcome and cursor before any outgoing Telegram messages.
        state["last_update_id"] = update_id
        sha = store.save(state, sha)
        offset = update_id
        handled += 1
        for action in outgoing:
            if action[0] == "send":
                bot.send(action[1], action[2], action[3])
            elif action[0] == "ack":
                bot.ack(action[1], action[2])
    # Notify owner once about newly available draft cards. Manual /pending can redisplay.
    unsent = [i for i in _pending(state) if not i.get("notified_at_utc")][:MAX_ITEMS_SHOWN]
    for card in unsent:
        card["notified_at_utc"] = utc_now()
        sha = store.save(state, sha)
        bot.send(int(owner_id), format_card(card), review_buttons(card))
    return f"IGM Telegram: processed {handled} updates; sent {len(unsent)} new cards; queue has {len(state['items'])} cards."


if __name__ == "__main__":
    print(run_poll())
