"""Safe Telegram approval protocol tests. No real network or Instagram writes."""
import copy
import unittest

from igm.telegram_control import (
    TelegramReviewError, apply_action, edit_card, handle_update,
    is_authorized, safe_review_item, format_card,
)


def item(kind="reply", status="pending", username="viewer_42"):
    return {
        "id": "R0001",
        "kind": kind,
        "status": status,
        "target_username": username,
        "topic": "cinema",
        "situation": "question",
        "original_comment": "Что посмотреть вечером?",
        "proposed_text": "Посмотри драму с сильным финалом. Какой жанр нравится?",
        "media_id": "100",
        "comment_id": "200"
    }


def state(items=None):
    return {"schema_version": 1, "last_update_id": 0, "items": items or [], "decisions": []}


def update(uid="1234", text="/pending", private=True):
    return {
        "update_id": 1,
        "message": {
            "text": text,
            "from": {"id": int(uid)},
            "chat": {"id": int(uid), "type": "private" if private else "group"},
        },
    }


def cb(uid="1234", value="review:R0001:approve"):
    return {
        "update_id": 2,
        "callback_query": {
            "id": "callbackid",
            "data": value,
            "from": {"id": int(uid)},
            "message": {"chat": {"id": int(uid), "type": "private"}},
        },
    }


class TelegramReviewTests(unittest.TestCase):
    def test_whitelist_owner_and_require_private_chat(self):
        self.assertTrue(is_authorized(update(), "1234"))
        self.assertFalse(is_authorized(update("4321"), "1234"))
        self.assertFalse(is_authorized(update(private=False), "1234"))
        self.assertFalse(is_authorized(cb("4321"), "1234"))
        self.assertFalse(is_authorized(update(), ""))

    def test_denied_username_remains_denied(self):
        blocked = item(username="@AiKa_St")
        self.assertFalse(safe_review_item(blocked))
        with self.assertRaisesRegex(TelegramReviewError, "prohibited"):
            apply_action(state([blocked]), "R0001", "approve", "1234")

    def test_approval_is_only_recorded_not_executed(self):
        data = state([item()])
        out = handle_update(cb(), data, "1234")
        self.assertEqual(data["items"][0]["status"], "approved")
        self.assertEqual(data["items"][0]["owner_review"]["owner_id"], "1234")
        self.assertEqual(data["decisions"][0]["decision"], "approve")
        self.assertTrue(any(x[0] == "send" for x in out))
        self.assertEqual(len(data["decisions"]), 1)
        self.assertFalse(any(x[0] == "instagram" for x in out))

    def test_duplicate_callback_never_reapproves(self):
        data = state([item()])
        handle_update(cb(), data, "1234")
        response = handle_update(cb(), data, "1234")
        self.assertEqual(len(data["decisions"]), 1)
        self.assertEqual(response[0][0], "ack")

    def test_owner_edit_saves_before_and_after(self):
        data = state([item()])
        handle_update(cb(value="review:R0001:revise"), data, "1234")
        self.assertEqual(data["items"][0]["status"], "needs_revision")
        out = handle_update(update(text="/edit R0001 Другой, более точный ответ"), data, "1234")
        self.assertEqual(data["items"][0]["status"], "pending")
        self.assertEqual(data["items"][0]["proposed_text"], "Другой, более точный ответ")
        self.assertEqual(data["decisions"][-1]["decision"], "edit")
        self.assertEqual(out[0][0], "send")

    def test_unauthorized_actor_cannot_edit(self):
        data = state([item()])
        before = copy.deepcopy(data)
        self.assertEqual(handle_update(update("4321", "/edit R0001 changed"), data, "1234"), [])
        self.assertEqual(data, before)

    def test_no_external_comment_publishing(self):
        data = state([item(kind="external_comment")])
        result = handle_update(cb(), data, "1234")
        self.assertEqual(data["items"][0]["status"], "approved")
        self.assertNotIn("published", str(result))

    def test_status_and_pending_drafts(self):
        data = state([item()])
        self.assertEqual(handle_update(update(text="/pending"), data, "1234")[0][0], "send")
        status = handle_update(update(text="/status"), data, "1234")
        self.assertIn("pending", status[0][2])

    def test_prohibited_callback_is_ignored_for_other_owner(self):
        data = state([item()])
        self.assertEqual(handle_update(cb(uid="4321"), data, "1234"), [])
        self.assertEqual(data["items"][0]["status"], "pending")

    def test_invalid_or_empty_card_fails_closed(self):
        invalid = item()
        invalid["proposed_text"] = ""
        self.assertFalse(safe_review_item(invalid))
        with self.assertRaises(TelegramReviewError):
            apply_action(state([invalid]), "R0001", "approve", "1234")


if __name__ == "__main__":
    unittest.main()
