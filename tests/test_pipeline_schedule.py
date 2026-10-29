import unittest
from datetime import datetime, timezone

from igm.pipeline import is_due


class ScheduledPublishTest(unittest.TestCase):
    def test_due_only_when_scheduled_and_time_passed(self):
        now = datetime(2026, 10, 30, 13, 5, tzinfo=timezone.utc)
        post = {"status": "scheduled", "scheduled_at": "2026-10-30T13:00:00Z", "approvals": {"text": {"by": "1"}, "final": {"by": "1"}}}
        self.assertTrue(is_due(post, now))
        self.assertFalse(is_due({**post, "scheduled_at": "2026-10-30T14:00:00Z"}, now))
        self.assertFalse(is_due({**post, "status": "final_review"}, now))
        self.assertFalse(is_due({**post, "scheduled_at": "мусор"}, now))


if __name__ == "__main__":
    unittest.main()
