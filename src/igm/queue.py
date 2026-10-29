"""GitHub Issues is the draft queue. No separate database or paid service."""
import json
import os
import re
from datetime import date, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from .plan import items


class DraftQueueError(RuntimeError):
    pass


class DraftQueue:
    def __init__(self, repository=None, token=None, transport=None):
        self.repository = repository or os.getenv('GITHUB_REPOSITORY', '')
        self.token = token or os.getenv('GITHUB_TOKEN', '')
        self.transport = transport or urlopen
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', self.repository):
            raise DraftQueueError('GITHUB_REPOSITORY must be owner/repository')
        if not self.token:
            raise DraftQueueError('GITHUB_TOKEN unavailable: run queue inside GitHub Actions')
        self.base = 'https://api.github.com/repos/' + self.repository + '/issues'

    def _request(self, url, post=None):
        data = json.dumps(post, ensure_ascii=False).encode('utf-8') if post is not None else None
        request = Request(url, data=data, headers={
            'Authorization': 'Bearer ' + self.token,
            'Accept': 'application/vnd.github+json',
            'Content-Type': 'application/json',
            'X-GitHub-Api-Version': '2022-11-28',
        }, method='POST' if post is not None else 'GET')
        try:
            with self.transport(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as ex:
            raise DraftQueueError(f'GitHub Issues error HTTP {ex.code}') from None
        except URLError:
            raise DraftQueueError('GitHub Issues network error') from None

    def create_week(self, start=None, days=7):
        start_date = date.fromisoformat(start) if start else date.today()
        days = max(1, min(int(days), 28))
        # Follow first 28 days of plan; title includes date for idempotency.
        existing = []
        page = 1
        while page <= 10:
            batch = self._request(f'{self.base}?state=all&per_page=100&page={page}')
            existing.extend(x.get('title', '') for x in batch if 'pull_request' not in x)
            if len(batch) < 100:
                break
            page += 1
        result = []
        for idx, post in enumerate(items()[:days]):
            day = start_date + timedelta(days=idx)
            title = f'[DRAFT] {day:%Y-%m-%d} {post["id"]}: {post["title"]}'
            if title in existing:
                result.append(f'- Уже есть: {title}')
                continue
            body = (f'## MyBrand | {day:%d.%m.%Y} (Asia/Almaty)\n\n'
                    f'**Формат:** {post["format"]}\n\n'
                    f'**Хук:** {post["hook"]}\n\n'
                    f'**Визуальный бриф:** {post["visual"]}\n\n'
                    f'**Подпись:**\n\n{post["caption"]}\n\n'
                    '- [ ] Оригинальные материалы готовы\n'
                    '- [ ] Права, субтитры и читаемость проверены\n'
                    '- [ ] Утверждено вручную\n\n'
                    'Черновик - не публикация. Эта задача ничего не выкладывает в Instagram.')
            created = self._request(self.base, post={'title': title, 'body': body})
            result.append(f'- Создан: [{title}]({created.get("html_url", "")})')
        return '## Очередь контента на GitHub Issues\n\n' + '\n'.join(result)
