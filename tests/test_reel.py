import json
import tempfile
import unittest
from pathlib import Path

from igm.reel import build_command, music_credit, pick_track, scene_durations


class ReelTest(unittest.TestCase):
    def test_durations_follow_text_and_cap(self):
        d = scene_durations([{"title": "Крючок"}, {"title": "Коротко"}, {"title": "Длинная мысль", "subtitle": " ".join(["слово"] * 20)}], "epic")
        self.assertEqual(d[0], 3.2)
        calm, fast = scene_durations([{"title": "a b c d e f"}] * 3, "calm"), scene_durations([{"title": "a b c d e f"}] * 3, "dynamic")
        self.assertGreater(sum(calm), sum(fast))
        self.assertLess(d[1], d[2])
        self.assertLessEqual(max(d), 7.0)
        many = scene_durations([{"title": "x " * 30}] * 20, "calm")
        self.assertLessEqual(sum(many) - 0.9 * 19, 90.01)

    def test_command_has_motion_transitions_and_music(self):
        cmd, total = build_command(["b0.jpg", "b1.jpg"], ["f0.png", "f1.png"], [3.2, 4.0], "out.mp4", music="m.mp3", style="epic")
        fc = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("zoompan", fc)
        self.assertIn("xfade", fc)
        self.assertIn("afade=t=out", fc)
        self.assertAlmostEqual(total, 6.6)
        self.assertIn("fadeblack", fc)  # переход пресета «Эпичный»
        self.assertIn("m.mp3", cmd)

    def test_track_pick_by_mood_and_credit(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "media/music").mkdir(parents=True)
            for f in ("a.mp3", "b.mp3"):
                Path(root, "media/music", f).write_bytes(b"x")
            Path(root, "content").mkdir()
            Path(root, "content/music.json").write_text(json.dumps({"tracks": [
                {"file": "media/music/a.mp3", "mood": "tense", "credit": "A"},
                {"file": "media/music/b.mp3", "mood": "calm", "credit": "B"},
                {"file": "media/music/missing.mp3", "mood": "tense"}]}), encoding="utf-8")
            self.assertEqual(pick_track("tense", root)["file"], "media/music/a.mp3")
            self.assertEqual(pick_track("epic", root, exclude=["media/music/a.mp3"])["file"], "media/music/b.mp3")
            self.assertEqual(music_credit({"credit": "A"}), "Музыка: A")
        self.assertIsNone(pick_track("tense", "/nonexistent"))


if __name__ == "__main__":
    unittest.main()
