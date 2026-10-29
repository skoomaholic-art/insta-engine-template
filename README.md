# Instagram Manager · движок

Данные и «робот» для Instagram Manager: хранит посты и статистику, собирает картинки и Reels (движение, 2.5D, музыка) через GitHub Actions, публикует через официальный Instagram API после двух согласований владельца, собирает метрики через 24 ч, 72 ч и 7 дней.

Работает в паре с шаблоном **assistant-bot** (бот и мини-приложение). Гайд по установке лежит там: `guide/setup-guide.pdf`.

## Что настроить
Settings → Secrets and variables → Actions:
- Secrets: `IG_ACCESS_TOKEN`, `IG_USER_ID`, `PIPELINE_SECRET`, `TG_BOT_TOKEN`, `TG_OWNER_CHAT_ID`
- Variables: `PIPELINE_BASE_URL`, `IG_EXPECTED_USERNAME`, `IGM_ENABLE_PUBLISH` (true, когда готов к автопубликации)

Затем один раз запусти workflow **Music library for Reels**.

Музыка для Reels: Kevin MacLeod (incompetech.com), CC BY 4.0, автор указывается в подписи автоматически. Лицензия кода: MIT.
