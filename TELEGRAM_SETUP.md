# Telegram: управление перенесено в Personal Assistant

**Текущий бот:** [@your_bot](https://t.me/your_bot)

**Новый репозиторий кода и настроек:**
https://github.com/YOUR_GITHUB_LOGIN/assistant-bot

**Инструкция по токену, подтверждению Telegram ID и связи с IGM:**
https://github.com/YOUR_GITHUB_LOGIN/assistant-bot/blob/main/SETUP.md

Прежний workflow Telegram в этом репозитории отключён. Он не забирает обновления у нового бота. **Не добавляйте TG_BOT_TOKEN в IGM**: токен нового бота должен храниться в Actions Secrets репозитория -Personal-Assistant.

Instagram Growth Manager остаётся источником данных:
- `data/telegram/review-queue.json`: очередь и история решений
- `data/comments/decisions.json`: решения и примеры для будущей модерации
- `content/comment-policy.json`: режим предварительного согласования
- `src/igm/policy.py`: строгий запрет взаимодействия с исключённым аккаунтом

Важно: авторизация Telegram-бота ещё не подтверждена до проверки реального ответа на /start; одобрения пока не публикуют контент в Instagram.
