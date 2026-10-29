# Подключение @MyBrand к Meta

Состояние: Instagram-аккаунт создан пользователем. GitHub-проект готов. **Реальная авторизация Meta ещё НЕ выполнена.**

1. Проверь Instagram: публичный профиль, **Профессиональный -> Автор (Creator)**, категория **Цифровой автор (Digital Creator)**.
2. Открой https://developers.facebook.com/apps/ и создай приложение Meta. Добавь **Instagram API with Instagram Login**, не Facebook Login. Для прямого Instagram Login Facebook Page не обязательна.
3. В Instagram API setup подключи собственный аккаунт и прими приглашение тестировщика, если Meta его показывает. Получи Instagram User token с `instagram_business_basic`. Для будущих публикаций отдельно запроси `instagram_business_content_publish`. Наличие инструментов, названия экранов и проверки зависят от статуса приложения.
4. Получи цифровой **Instagram User ID** через Meta Dashboard или авторизованный запрос к `/me`. Убедись, что `username` совпадает с `mybrand`; никнейм сам по себе не является числовым ID.
5. Открой **Settings -> Secrets and variables -> Actions** в приватном GitHub-репозитории. В **Repository secrets** сохрани `IG_USER_ID` и `IG_ACCESS_TOKEN`. В **Variables** добавь `IGM_ENABLE_PUBLISH=false`.
6. Из ChatGPT попроси выполнить команды `status` и `report`: первая проверяет заполнение переменных, вторая делает живой запрос к `graph.instagram.com`. Не передавай токены в чат, Issues, скриншотах или исходниках.
7. Пока авторизация не протестирована, публикации остаются отключены. Даже после включения нужна отдельная команда с `confirm=PUBLISH`. Media URL должен быть доступен по публичному HTTPS.

Direct Instagram Login не даёт доступа к рекламе/тегированию, хотя позволяет работать с собственным контентом. Для рекламных функций в будущем можно отдельно оценить другие Meta API. Автоматизация не гарантирует рост подписчиков и доход.

Документация Meta: https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api
