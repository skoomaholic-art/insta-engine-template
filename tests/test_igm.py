import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

from igm.commands import handle
from igm.instagram import InstagramAPI, InstagramError
from igm.plan import items, render_plan, render_caption
from igm.queue import DraftQueue, DraftQueueError
from igm.__main__ import load_from_event


class IGMTests(unittest.TestCase):
    def test_28_original_ideas(self):
        data = items()
        self.assertEqual(len(data), 28)
        self.assertEqual(len(set(x['id'] for x in data)), 28)
        self.assertTrue(all(x['caption'] and x['visual'] for x in data))

    def test_plan(self):
        output = render_plan('2026-10-29', 7)
        self.assertIn('29.10', output)
        self.assertIn('04.11', output)
        self.assertEqual(output.count('| Reel |') + output.count('| Carousel |') + output.count('| Story |'), 7)
        self.assertIn('Публикации не выходят автоматически', output)

    def test_caption(self):
        self.assertIn('#MyBrandAI', items()[0]['caption'])
        self.assertIn('Хук на экране:', render_caption('D01'))

    def test_no_publish_by_default(self):
        with patch.dict(os.environ, {'IGM_ENABLE_PUBLISH': 'false'}):
            with self.assertRaisesRegex(ValueError, 'отключены'):
                handle({'command': 'publish-image', 'confirm': 'PUBLISH',
                        'image_url': 'https://example.org/image.jpg', 'caption': 'demo'})

    def test_confirmation_required(self):
        with patch.dict(os.environ, {'IGM_ENABLE_PUBLISH': 'true'}):
            with self.assertRaisesRegex(ValueError, 'подтверждающая'):
                handle({'command': 'publish-image', 'image_url': 'https://example.org/image.jpg', 'caption': 'demo'})

    def test_missing_auth(self):
        with patch.dict(os.environ, {'IG_USER_ID': '', 'IG_ACCESS_TOKEN': ''}):
            with self.assertRaises(InstagramError):
                InstagramAPI()

    def test_issue_rejects_other_user(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'event.json'
            p.write_text(json.dumps({'issue': {'user': {'login': 'randomperson'},
                'title': '[IGM] report', 'body': '{"command":"report"}'}}))
            with patch.dict(os.environ, {'GITHUB_EVENT_PATH': str(p), 'GITHUB_REPOSITORY_OWNER':'YOUR_GITHUB_LOGIN'}):
                with self.assertRaisesRegex(ValueError, 'Only the repository owner'):
                    load_from_event()

    def test_issue_owner_command(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'event.json'
            p.write_text(json.dumps({'issue': {'user': {'login': 'YOUR_GITHUB_LOGIN'},
                'title': '[IGM] plan', 'body': '```json\n{"command":"plan","days":7}\n```'}}))
            with patch.dict(os.environ, {'GITHUB_EVENT_PATH': str(p), 'GITHUB_REPOSITORY_OWNER':'YOUR_GITHUB_LOGIN'}):
                self.assertEqual(load_from_event()['command'], 'plan')

    def test_prevent_password_in_image_url(self):
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return None
        client = InstagramAPI('123456', 'testtoken', transport=lambda *a,**k:FakeResponse())
        with self.assertRaises(InstagramError):
            client.publish_image('https://user:pass@example.org/a.jpg', 'test')

    def test_http_header_bearer_and_report(self):
        seen = []
        class FakeResponse:
            def __init__(self, data): self.data=data
            def __enter__(self): return self
            def __exit__(self, *a): return None
            def read(self): return json.dumps(self.data).encode()
        def transport(req, timeout):
            seen.append(req)
            self.assertIsInstance(req, Request)
            return FakeResponse({'id':'123','username':'test','followers_count':4,'media_count':0})
        client=InstagramAPI('123','token',transport=transport)
        # json.load() invokes .read() on FakeResponse
        profile=client.profile()
        self.assertEqual(profile['followers_count'],4)
        self.assertEqual(seen[0].get_header('Authorization'),'Bearer token')
        self.assertNotIn('token',seen[0].full_url)

    def test_uses_direct_instagram_login_by_default(self):
        with patch.dict(os.environ, {'IG_AUTH_MODE': 'instagram'}):
            client = InstagramAPI('123', 'fake-token')
            self.assertEqual(client.base, 'https://graph.instagram.com/v26.0')

    def test_facebook_mode_is_available_for_legacy_connections(self):
        with patch.dict(os.environ, {'IG_AUTH_MODE': 'facebook'}):
            client = InstagramAPI('123', 'fake-token')
            self.assertEqual(client.base, 'https://graph.facebook.com/v26.0')

    def test_publish_blocks_wrong_instagram_username(self):
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return None
            def read(self): return json.dumps({'id':'123','username':'someone.else'}).encode()
        with patch.dict(os.environ, {'IG_AUTH_MODE':'instagram','IG_EXPECTED_USERNAME':'mybrand'}):
            client = InstagramAPI('123','fake-token',transport=lambda *a, **k: FakeResponse())
            with self.assertRaisesRegex(InstagramError, 'mismatch'):
                client.publish_image('https://example.org/image.jpg', 'demo')

    def test_queue_requires_authenticated_github(self):
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': '', 'GITHUB_TOKEN': ''}):
            with self.assertRaises(DraftQueueError):
                DraftQueue()

    def test_queue_creates_drafts_not_posts_and_deduplicates(self):
        existing = []
        requests = []
        class FakeResponse:
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *a): return None
            def read(self): return json.dumps(self.body).encode()
        def transport(req, timeout):
            requests.append(req)
            if req.get_method() == 'GET':
                return FakeResponse(existing)
            payload = json.loads(req.data.decode())
            self.assertTrue(payload['title'].startswith('[DRAFT]'))
            self.assertIn('Черновик - не публикация', payload['body'])
            existing.append({'title': payload['title']})
            return FakeResponse({'html_url': 'https://github.com/example/repo/issues/1'})
        queue = DraftQueue('example/repo', 'fake-token', transport=transport)
        result = queue.create_week('2026-10-29', 3)
        self.assertEqual(result.count('- Создан:'), 3)
        result_again = queue.create_week('2026-10-29', 3)
        self.assertEqual(result_again.count('- Уже есть:'), 3)
        self.assertEqual(sum(req.get_method()=='POST' for req in requests), 3)


if __name__ == '__main__':
    unittest.main()
