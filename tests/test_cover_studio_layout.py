"""Do not change old image framing; preserve new editor-controlled visual settings."""
import tempfile
import unittest
from pathlib import Path
from igm.pipeline import pages_or_draft, playwright_render
import inspect


class CoverStudioLayoutTests(unittest.TestCase):
    def test_new_studio_posts_carry_preview_preservation_flag(self):
        post={
            "id":"P0099",
            "studio_sync":1,
            "draft":{"format":"portrait","slides":[{"title":"Test"}]},
            "pages":[{"bgX":18,"bgY":74,"dim":42,"bgFit":"contain","title":"Test"}],
            "bgs":{"0":"media/posts/P0099/bg-studio-0000000000000000.jpg"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp,"media/posts/P0099/bg-studio-0000000000000000.jpg")
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(b"\xff\xd8\xfftest")
            payload=pages_or_draft(post,root=tmp)
        self.assertTrue(payload["studio_sync"])
        self.assertEqual(payload["pages"][0]["bgX"],18)
        self.assertEqual(payload["pages"][0]["bgY"],74)
        self.assertEqual(payload["pages"][0]["dim"],42)
        self.assertEqual(payload["pages"][0]["bgFit"],"contain")
        self.assertTrue(payload["bgs"]["0"].startswith("data:image/jpeg;base64,"))

    def test_existing_posts_keep_legacy_rendering(self):
        old={"draft":{"format":"portrait","slides":[{"title":"Old"}]},
             "pages":[{"bgX":16,"bgY":40,"dim":28}],"bgs":{}}
        self.assertFalse(pages_or_draft(old)["studio_sync"])

    def test_browser_rendering_conditionally_preserves_studio_layout(self):
        # No HTTP or Meta calls: inspect browser interpolation used by Playwright.
        script=inspect.getsource(playwright_render)
        self.assertIn("payload.studio_sync ? {} :",script)
        self.assertIn("pages[k] = { ...pages[k], bgImage: img, ...oldLayout }",script)


if __name__ == "__main__":
    unittest.main()
