# MYBRAND — строгие визуальные пресеты v1.1

Дизайн всех обложек контролируется программными правилами: при любом новом посте ИИ формирует **только текст, сценарий и структуру**. HTML/CANVAS-компоновка, цвет, фон, поля, аватар S, размеры и ритм — из закреплённых пресетов. Это предотвращает бесконечную смену визуального стиля и «типичные ИИ-постеры».

**Реальная реализация:** https://github.com/YOUR_GITHUB_LOGIN/assistant-bot/tree/main/cover-studio
**Запуск в Telegram:** https://your-app.vercel.app/cover-studio/ (бот @your_bot)
**Статус:** шесть вариантов фона готовы в бесплатном Canvas-рендерере; описания для ИИ-генераторов сохраняются рядом с ними; фактическая автоматическая генерация внешних фото/артов из этих промптов не подключена.

## Нерушимые правила бренда

1. **Одна айдентика:** почти чёрный графит `#101215`, молочный `#F1ECE3`, оранжевый `#F2AA4C`; для сериалов допустим холодный `#90AFB9`.
2. **Один крупный заголовок:** предпочтительно до 6 слов, не более 3–4 коротких строк. Никакого мелкого текста, тяжёлых теней или объёмных букв.
3. **Фон — подложка, не самостоятельный постер:** минимум деталей, пустота под текст, сохранение высокого контраста.
4. **Аватар:** только утверждённая актуальная оранжевая кинобобина с буквой S, небольшая и одинаково размещённая. Не придумывать новый логотип на каждой публикации.
5. **Форматы:** Reel 1080 × 1920, карусель 1080 × 1350, квадрат 1080 × 1080. Перед выпуском просматривать кадрирование сетки.
6. **Никакого чужого IP:** персонажи, кадры фильмов, студийные логотипы, плакаты, фотографии актёров — только при необходимом правовом основании.
7. **Проверка:** сперва черновик в Telegram на одобрение, никакой автоматической отправки в Instagram до решения владельца.

## Обязательная базовая часть каждого промпта на фон

> Original abstract cinematic background plate only. Near-black matte charcoal, restrained analog film texture, subtle off-center accent in muted amber (cinema) or desaturated steel blue (series). Keep the central 75 percent uncluttered with dark negative space for an overlay. Elegant editorial magazine restraint. Absolutely NO text, lettering, logo, watermark, actors, faces, recognisable movie imagery, busy scenery, glowing CGI, oversaturated colors or glossy 3D. Composition for portrait 9:16 for Reels or 4:5 for carousel; do not render the caption, brand or typography.

### 01 · Чистый графит
Код `noir`. Тон `#101215`, едва заметная виньетка. Универсальный фон для кино и коротких тезисов.

**Промпт-добавка:** Minimal dark graphite matte background, extremely subtle natural vignette, almost solid charcoal, very fine soft analog film texture, no discernible objects, no shapes, high readability, slight amber reflected ambient at far right edge.

### 02 · Луч проектора
Код `projector`. Тот же графит, справа очень мягкий приглушённый янтарный свет. Для закадровых наблюдений.

**Промпт-добавка:** Restrained cinematic projector side light in deep charcoal studio, a single barely visible soft amber light beam grazing the far right edge, sparse tiny airborne dust, broad empty dark foreground, deep restrained shadows, sophisticated film magazine design.

### 03 · Контур кадра
Код `frame`. Два тонких угла рамки справа, почти чёрная подложка. Для разбора киноязыка.

**Промпт-добавка:** Geometric minimalist editorial photo-frame abstraction, just two fine muted warm grey corner lines on far right of nearly black matte background, subtle photographic grain, clean empty central negative space, no image inside the frame.

### 04 · Матовая плёнка
Код `grain`. Почти монохромная фотобумага с едва различимым зерном. Для серьёзных материалов.

**Промпт-добавка:** Monochromatic near-black matte photographic paper and fine analog film grain texture, dark grey subtle variation, absolutely minimal, no scratches, no dust clusters, no discernible imagery, refined independent cinema publication feel.

### 05 · Сериальный нуар
Код `blue`. Сине-графитовый фон, слабые вертикальные линии справа. Для сериалов и персонажей.

**Промпт-добавка:** Dark cold-blue graphite cinema background, near-invisible thin vertical guide lines at far right, desaturated steel-blue ambient rim light, matte shadows and restrained subtle analog grain, clean deep-dark typography negative space.

### 06 · Геометрия киноплёнки
Код `reel`. Строгая уплощённая геометрия киноплёнки справа. Для подборок, навигации и авторских экспериментальных материалов.

**Промпт-добавка:** Very subtle abstract film strip motif confined to extreme right edge of nearly black background, flat charcoal design with occasional muted amber edge reflections, original minimal editorial vector-meets-film texture, absolutely no literal film imagery or readable markings.

## ИИ-конвейер

1. В Telegram выбери «Конструктор обложек».
2. В секции «ИИ · Сборка по пресету» выбери один из 6 редакционных типов: мнение, сериал, деталь, кино-карусель, сериал-карусель, подборка.
3. В меню «Строгий стиль / фон» выбери один из 6 вариантов, при желании нажми **Применить ко всем слайдам**.
4. Вставь тему; ИИ готовит заголовки, подпись и сценарий Reel (пока нужен `GROQ_API_KEY` в защищённых переменных Vercel).
5. Конструктор рендерит подложки локально без расхода токенов и без генерации изображений. Если нужен художественный фон, нажми «Копировать промпт фона», создай изображение отдельно из законного источника и загрузи его в редактор.
6. Экспортируй PNG или ZIP, сохрани исходник JSON. Передача готовых файлов в очередь IGM через Telegram ещё требует интеграции; не заявлять о её наличии.

## Границы автогенерации

Программа не имеет права утверждать, что факты и авторские права проверены, только потому, что сгенерировала текст. Сценарий Reel ещё не является готовым видеороликом. Не подменять логотип новым знаком и не публиковать автоматически.
