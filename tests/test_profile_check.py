import json
import unittest

from igm.profile_check import EXPECTED, compare, render_check


class FakeAPI:
    account_id = "1"

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def request(self, path, params=None):
        self.calls.append((path, params))
        return self.payload


class ProfileCheckTests(unittest.TestCase):
    def setUp(self):
        self.expected = json.loads(EXPECTED.read_text(encoding="utf-8"))

    def test_matching_profile_passes_with_whitespace_noise(self):
        actual = {"username": "MyBrand", "name": self.expected["name"] + " ",
                  "biography": self.expected["biography"].replace("\n", " \r\n"),
                  "profile_picture_url": "https://cdn.example/p.jpg"}
        self.assertTrue(all(ok for _, ok, _ in compare(actual, self.expected)))

    def test_mismatch_is_reported_and_read_only(self):
        api = FakeAPI({"username": "mybrand", "name": "Old", "biography": ""})
        out = render_check(api)
        self.assertIn("❌ Имя", out)
        self.assertIn("❌ Аватар", out)
        self.assertEqual(len(api.calls), 1)
        self.assertIn("biography", api.calls[0][1]["fields"])


if __name__ == "__main__":
    unittest.main()
