# VOID Entity — Replit MVP

VOID — редакционный Telegram-агент:

```text
RSS новости → фильтр сигналов → VOID-оптика → черновик → публикация → кнопка “поймал”
```

Это не генератор цитат. Он берёт реальные инфоповоды и пишет посты через призму человека в цифровом мире: AI, внимание, контроль, культура, будущее, поведение.

## Быстрый запуск в Replit

1. Создай Python Repl.
2. Загрузи все файлы из этого проекта.
3. В `Secrets` добавь:

```env
BOT_TOKEN=токен от BotFather
CHANNEL_ID=@username_твоего_канала
ADMIN_ID=твой_telegram_user_id
OPENAI_API_KEY=опционально
OPENAI_MODEL=openai/gpt-5.4
OPENAI_IMAGE_MODEL=gpt-image-1
OPENAI_IMAGE_SIZE=1024x1024
OPENAI_IMAGE_QUALITY=medium
```

4. Установи зависимости:

```bash
pip install -r requirements.txt
```

5. Запусти:

```bash
python main.py
```

Replit также прочитает `.replit`, где указано `run = "python main.py"`.

## Важно для Telegram

- Бот должен быть добавлен в Telegram-канал как администратор.
- У бота должно быть право публиковать сообщения.
- `CHANNEL_ID` можно задать как `@channel_username`.
- `ADMIN_ID` — это твой числовой Telegram ID.

## Команды

```text
/start — запуск
/help — помощь
/scan — найти свежие инфоповоды
/candidates — показать найденные новости
/draft ID — сделать черновик из новости
/drafts — показать черновики
/preview ID — показать полный черновик
/publish ID — опубликовать черновик в канал
/stats — статистика
```

## Первый сценарий

```text
/scan
```

Бот найдёт новости и покажет список кандидатов:

```text
#12 · AI · score 8
OpenAI ...
Источник: The Verge
```

Создать черновик:

```text
/draft 12
```

Посмотреть:

```text
/preview 1
```

Опубликовать:

```text
/publish 1
```

Под постом будет кнопка:

```text
поймал
```

Реакции сохраняются в SQLite-базу `void.db`.

## AI-режим

Если `OPENAI_API_KEY` задан, бот будет использовать OpenAI Responses API для генерации постов и Images API для 1-2 релевантных картинок к опубликованному посту.

Если ключ не задан, бот всё равно работает: он использует fallback-редактор с шаблонной VOID-оптикой и сухим юмором.

## Файлы

```text
main.py                 основной Telegram-бот
Main.py                 wrapper, если Replit создал файл с большой буквы
config.py               настройки и env
sources.py              RSS-источники
news_collector.py       сбор новостей
void_lens.py            фильтр и классификация сигналов
database.py             SQLite
void_editor.py          AI/fallback редактор постов
prompts/void_editor_prompt.txt  редакционный промпт VOID
requirements.txt        зависимости
.env.example            пример переменных
.replit                 команда запуска
```

## Как расширять дальше

- Добавить `/skip ID` для отклонения новости.
- Добавить `/rewrite ID` для переписывания черновика.
- Добавить Telegram-источники и YouTube RSS.
- Добавить Weekly Observation.
- Добавить частоты аудитории: какие темы чаще “поймали”.
- Добавить автопубликацию только для постов с `publish_score >= 8`.

## Russian mode

VOID теперь старается показывать найденные новости на русском в `/scan` и `/candidates`.
Для перевода нужен `OPENAI_API_KEY` в Replit Secrets. Без ключа RSS-заголовки останутся в языке источника, но бот продолжит работать.

После добавления ключа перезапусти бота:

```bash
python main.py
```

