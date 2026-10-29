"""Deterministic, free content strategy and calendar."""
import json
from datetime import date, timedelta
from pathlib import Path

PLAN = Path(__file__).resolve().parents[2] / 'content' / 'content-plan.json'


def items():
    return json.loads(PLAN.read_text(encoding='utf-8'))['posts']


def render_plan(start=None, days=7):
    start = date.fromisoformat(start) if start else date.today()
    chosen = items()[:max(1, min(int(days), 28))]
    lines = [f'## MyBrand: контент-план с {start:%d.%m.%Y}', '',
             '| Дата (UTC+5) | Формат | Тема | Хук |', '|---|---|---|---|']
    for idx, item in enumerate(chosen):
        day = start + timedelta(days=idx)
        esc = lambda s: str(s).replace('|', '\\|').replace('\n', ' ')
        lines.append(f'| {day:%d.%m} | {esc(item["format"])} | {esc(item["title"])} | {esc(item["hook"])} |')
    lines += ['', 'Публикации не выходят автоматически. Подготовь оригинальные материалы, проверь права и утверди текст.']
    return '\n'.join(lines)


def render_caption(post_id):
    post = next((p for p in items() if p['id'] == post_id), None)
    if not post:
        raise ValueError('Нет такой темы. Используй plan, чтобы увидеть идентификаторы в content/content-plan.json')
    return (f'## {post["title"]}\n\n**Формат:** {post["format"]}\n\n'
            f'**Хук на экране:** {post["hook"]}\n\n'
            f'**Тезисы:** {post["outline"]}\n\n'
            f'**Подпись:**\n\n{post["caption"]}\n\n'
            f'**CTA:** {post["cta"]}\n\n'
            f'**Визуал:** {post["visual"]}\n\n'
            'Публикация требует ручной проверки и утверждения.')
