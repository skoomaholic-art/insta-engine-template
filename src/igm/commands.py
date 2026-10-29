"""Strict command router. No arbitrary code execution from GitHub issues."""
import os
from .instagram import InstagramAPI, InstagramError
from .plan import render_plan, render_caption
from .queue import DraftQueue
from .analytics import collect_snapshot, commit_github_snapshot, render_trend
from .autopilot import run_autopilot
from .profile_check import render_check

HELP = '''## Instagram Growth Manager 0.1 - MyBrand

Команды (`[IGM]` в названии GitHub Issue, JSON в описании):

- `{"command":"status"}` - проверка конфигурации (без чтения токена)
- `{"command":"plan","days":7,"start":"2026-10-29"}` - план на 7-28 дней
- `{"command":"caption","post_id":"D01"}` - черновик текста
- `{"command":"queue","days":7,"start":"2026-10-29"}` - создать Issue-черновики на неделю (без публикации)
- `{"command":"publish-check"}` - проверить, может ли токен публиковать (без публикации)
- `{"command":"profile-check"}` - сверить имя, BIO и аватар с утверждённым оформлением (только чтение)
- `{"command":"report"}` - живые метрики и последние публикации (нужна Meta)
- `{"command":"publish-image","image_url":"https://.../picture.jpg","caption":"...","confirm":"PUBLISH"}` - публикация ОДНОЙ картинки, если отдельно включена публикация

Никакой накрутки, автолайков, массфолловинга, массовых DM или обхода API.
'''


def handle(data):
    if not isinstance(data, dict):
        raise ValueError('Команда должна быть JSON-объектом')
    cmd = data.get('command', 'help')
    if cmd == 'help':
        return HELP
    if cmd == 'autopilot':
        return run_autopilot()
    if cmd == 'status':
        has_id = bool(os.getenv('IG_USER_ID'))
        has_token = bool(os.getenv('IG_ACCESS_TOKEN'))
        publishing = os.getenv('IGM_ENABLE_PUBLISH', '').lower() == 'true'
        return ('## Статус MyBrand\n\n'
                f'- Instagram ID: {"настроен" if has_id else "не задан"}\n'
                f'- Meta access token: {"настроен" if has_token else "не задан"}\n'
                f'- Публикации: {"включены (требуют отдельного подтверждения)" if publishing else "заблокированы"}\n'
                f'- Graph API: {os.getenv("META_GRAPH_VERSION", "v26.0")}\n\n'
                'Отсутствие данных означает, что реальная связь с Instagram ещё не установлена.')
    if cmd == 'plan':
        return render_plan(data.get('start'), data.get('days', 7))
    if cmd == 'queue':
        return DraftQueue().create_week(data.get('start'), data.get('days', 7))
    if cmd == 'caption':
        return render_caption(str(data.get('post_id', '')))
    if cmd == 'snapshot':
        snap = collect_snapshot()
        path = commit_github_snapshot(snap)
        acc = snap['account']
        return ('## Снимок статистики сохранён\n\n'
                f'- Дата (UTC+5): {snap["date_almaty"]}\n'
                f'- Подписчики: {acc["followers_count"]}\n'
                f'- Публикации: {acc["media_count"]}\n'
                f'- Последних публикаций в снимке: {len(snap["media"])}\n'
                f'- Файл: `{path}`\n\n'
                'Сохранены базовые счётчики, не охваты или просмотры.')
    if cmd == 'publish-check':
        api = InstagramAPI()
        enabled = os.getenv('IGM_ENABLE_PUBLISH', '').lower() == 'true'
        try:
            limit = api.publishing_limit()
            perm = f"✅ разрешение на публикацию есть (использовано сегодня: {limit.get('quota_usage', '?')})"
        except InstagramError as err:
            perm = f"❌ токен не может публиковать: {err}. Нужен токен с instagram_business_content_publish"
        return ('## Готовность к публикации\n\n'
                f'- {perm}\n'
                f'- {"✅" if enabled else "❌"} IGM_ENABLE_PUBLISH = {"true" if enabled else "не true"}\n'
                f'- {"✅" if os.getenv("PIPELINE_SECRET") else "⚠️ проверяется только в pipeline.yml"} PIPELINE_SECRET')
    if cmd == 'pipeline-check':
        from .pipeline import ping, DEFAULT_BASE
        lines = ['## Проверка конвейера постов', '']
        try:
            limit = InstagramAPI().publishing_limit()
            lines.append(f"- ✅ токен Instagram может публиковать (сегодня использовано: {limit.get('quota_usage', 0)})")
        except InstagramError as err:
            lines.append(f'- ❌ токен Instagram не может публиковать: {err}')
        try:
            InstagramAPI().insights_probe()
            lines.append('- ✅ токен даёт Insights (охват, просмотры, сохранения, пересылки)')
        except InstagramError as err:
            lines.append(f'- ❌ нет Insights: {err}. Нужен токен с instagram_business_manage_insights')
        enabled = os.getenv('IGM_ENABLE_PUBLISH', '').lower() == 'true'
        lines.append(f'- {"✅" if enabled else "❌"} IGM_ENABLE_PUBLISH = {os.getenv("IGM_ENABLE_PUBLISH") or "не задан"}')
        base = os.getenv('PIPELINE_BASE_URL') or ''
        lines.append(f'- {"✅" if base else "⚠️"} PIPELINE_BASE_URL = {base or "не задан, беру " + DEFAULT_BASE}')
        secret = os.getenv('PIPELINE_SECRET', '')
        lines.append(f'- {"✅" if len(secret) >= 24 else "❌"} PIPELINE_SECRET в IGM {"задан" if secret else "не задан"}')
        r = ping(base or DEFAULT_BASE, secret)
        if r.get('ok'):
            groq = {'ok': '✅ GROQ_API_KEY работает', 'missing': '❌ GROQ_API_KEY не задан на Vercel', 'invalid': '❌ GROQ_API_KEY неверный'}.get(r.get('groq'), f"⚠️ Groq: {r.get('groq')}")
            lines += ['- ✅ Vercel принял подпись: PIPELINE_SECRET совпадает', f'- {groq}',
                      f"- {'✅' if r.get('cron_secret') else '❌'} CRON_SECRET на Vercel",
                      f"- {'✅' if r.get('telegram') else '❌'} бот прислал тестовое сообщение в Telegram",
                      f"- {'✅' if r.get('bridge') else '❌'} IGM_BRIDGE_TOKEN на Vercel"]
        else:
            lines.append(f"- ❌ {r.get('error')}")
        return '\n'.join(lines)
    if cmd == 'replan-prompts':
        from .pipeline import notify, DEFAULT_BASE
        pid = str(data.get('post_id', ''))
        ok = notify(os.getenv('PIPELINE_BASE_URL') or DEFAULT_BASE, os.getenv('PIPELINE_SECRET', ''), pid, 'replan', timeout=60)
        return f'Промпты {pid}: ' + ('пересобраны по разбору новости и отправлены в бот' if ok else 'не удалось пересобрать')
    if cmd == 'resend-prompts':
        from .pipeline import notify, DEFAULT_BASE
        pid = str(data.get('post_id', ''))
        ok = notify(os.getenv('PIPELINE_BASE_URL') or DEFAULT_BASE, os.getenv('PIPELINE_SECRET', ''), pid, 'prompts')
        return f'Промпты {pid}: ' + ('отправлены в бот' if ok else 'не удалось отправить')
    if cmd == 'profile-check':
        return render_check()
    if cmd == 'trend':
        return render_trend()
    if cmd == 'report':
        api = InstagramAPI()
        account = api.profile()
        posts = api.recent_media(20)
        followers = account.get('followers_count', 'нет доступа')
        lines = [f'## Instagram: @{account.get("username", "неизвестно")}', '',
                 f'- Подписчики: {followers}',
                 f'- Публикации: {account.get("media_count", "нет доступа")}',
                 '', '### Последние публикации', '',
                 '| Дата | Тип | Лайки | Комментарии | Ссылка |', '|---|---|---:|---:|---|']
        for p in posts:
            link = p.get('permalink', '')
            if link and not link.startswith('https://www.instagram.com/'):
                link = ''
            lines.append(f'| {p.get("timestamp", "")[:10]} | {p.get("media_type", "")} | '
                         f'{p.get("like_count", "н/д")} | {p.get("comments_count", "н/д")} | '
                         f'{"[открыть](" + link + ")" if link else "н/д"} |')
        lines += ['', 'Это фактические доступные счётчики API, а не просмотры или охват. '
                  'Расширенные Insights будут добавлены после выдачи соответствующих разрешений.']
        return '\n'.join(lines)
    if cmd == 'publish-image':
        if os.getenv('IGM_ENABLE_PUBLISH', '').lower() != 'true':
            raise ValueError('Публикации отключены. Задай IGM_ENABLE_PUBLISH=true в GitHub Actions Variables после подключения Meta')
        if data.get('confirm') != 'PUBLISH':
            raise ValueError('Нужна отдельная команда с confirm=PUBLISH, подтверждающая публикацию')
        raise ValueError('Прямая публикация отключена. Используй очередь IGM с подтверждённым решением владельца по каждой публикации.')
    raise ValueError('Неизвестная команда. Выполни {"command":"help"}')
