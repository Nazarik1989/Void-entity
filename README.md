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
OPENAI_POST_MODEL=openai/gpt-5.4-mini
OPENAI_DIALOG_MODEL=openai/gpt-5.4
OPENAI_IMAGE_MODEL=gpt-image-1
OPENAI_IMAGE_SIZE=1024x1024
OPENAI_IMAGE_QUALITY=medium
CROSSPOST_DAILY_LIMIT=2
CROSSPOST_EXCHANGE_ENABLED=true
CROSSPOST_EXCHANGE_DIR=/opt/bot_exchange
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
/commands — общие команды
/vk_commands — команды VK-публикации и музыки
/scan — найти свежие инфоповоды
/candidates — показать найденные новости
/draft ID — сделать черновик из новости
/drafts — показать черновики
/preview ID — показать полный черновик
/publish ID — опубликовать черновик в канал
/void текст — выделить реплику Void из внутреннего диалога с Naz
/publish_void текст — передать реплику в exchange для адаптации Naz
/cross_status — статус кросс-постинга за сегодня
/cross_to_naz ID — выделить реплику из VOID-черновика и передать в exchange
/cross_from_naz текст — адаптировать пост Naz AI Bot и опубликовать в VOID
/telegram_schedule — расписание Telegram-рубрик VOID
/void_schedule_now — опубликовать одну scheduled-рубрику VOID
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

`OPENAI_POST_MODEL` используется для ручного постинга и автопостинга. `OPENAI_DIALOG_MODEL` используется для диалогового режима.

## Приватный разговор Naz ↔ VOID

VOID и Naz обмениваются неопубликованными мыслями, а не зеркалят готовые посты. Снаружи каждый публикует собственный новый вывод. Факт беседы можно упоминать естественно: «Мы тут с VOID спорили…», «После разговора с Naz осталось наблюдение…».

- `/void text` или ответ `/void` выделяет короткую приватную мысль VOID для предпросмотра.
- `/thought_to_naz text` или совместимая команда `/publish_void text` кладёт `private_thought.v1` в `void_to_naz/inbox`.
- `/cross_to_naz ID` делает то же самое из существующего VOID-черновика.
- `/cross_from_naz text` takes a Naz AI Bot post, rewrites it as a VOID signal, saves a draft, and publishes it to VOID.
- `/cross_status` shows today's counters.
- `CROSSPOST_DAILY_LIMIT` defaults to `2` per direction per Moscow day.

Outbound payload имеет тип `private_thought.v1`, флаги `already_published=false`, `ready_to_publish=false` и `requires_original_reflection=true`. Дословное цитирование запрещено; естественное упоминание разговора разрешено. Публичная рубрика называется «Мысли после разговора».

Автоматическая публикация обычного VOID-черновика, включая scheduled-публикацию, не создаёт cross-post. Маршрут Void → Naz запускается только явной командой и идёт только через exchange/adaptation. Фрагменты должны быть короткими мыслями, кусками внутреннего диалога, странными тезисами, философскими или мрачными импульсами. Перед передачей бот блокирует секреты, токены, пароли, SSH/IP-доступ, внутренние URL и клиентские детали.

## Telegram rubric schedules

`/auto_on` enables the VOID Telegram schedule in this project:

- VOID posts about every 3 hours, with `MIDNIGHT` restricted to 00-02 Moscow time, `FREQUENCY` in the evening, and daytime pools for `SIGNAL`, `OBSERVATION`, `FUTURE FILE`, and news.

Naz AI Bot has its own project, channel, and schedule. Общего scheduler для двух сущностей нет: они встречаются только через exchange/adaptation.

Manual controls:

```text
/telegram_schedule
/void_schedule_now
```

## VK publisher

The bot can publish test posts to a VK community wall.

Environment:

```text
VK_USER_ACCESS_TOKEN=...
VK_PHOTO_ACCESS_TOKEN=...
VK_GROUP_ID=123456789
VK_API_VERSION=5.199
VK_DRY_RUN=true
VK_MUSIC_TRACKS_FILE=data/vk_music_tracks.json
```

Commands:

```text
/vk_status
/vk_test text
/vk_test --yes text
/publish_vk ID
/publish_vk --yes ID
/vk_music_status
/vk_music_import Artist - Track | https://vk.com/audio... | future, night
/vk_music_sync URL
/vk_music_sync URL night,electronic,melancholy
```

Keep `VK_DRY_RUN=true` while checking setup. `/vk_test text` and `/publish_vk ID` return the prepared request without posting. Use `/vk_test --yes text` or `/publish_vk --yes ID` to publish for real; `--yes` bypasses dry-run for that call.

`/publish_vk --yes ID` stores the VK post id in `vk_posts` and blocks duplicate VK publishing of the same draft. It also tries to generate one topic-matched image and upload it as a VK wall photo attachment. If `VK_MUSIC_TRACKS_FILE` points to a JSON playlist file, the bot picks a matching track by tags and appends it as a soundtrack link.

For image attachments, `VK_PHOTO_ACCESS_TOKEN` should be a user access token with `photos` and `wall` permissions. A community/group token can publish text posts but may fail on `photos.getWallUploadServer`.

## VK browser publisher

Browser publishing uses your logged-in VK session as admin permission, but the post should be created on the community wall and published as the community.

Setup:

```bash
pip install -r requirements.txt
python -m playwright install chromium
python vk_browser_publisher.py login
```

Prepare a draft payload:

```bash
python vk_browser_publisher.py prepare-draft 259
```

Compose the prepared payload in VK:

```bash
python vk_browser_publisher.py open-payload data/vk_browser_payloads/draft-259.json
```

Or publish automatically from a draft id:

```bash
python vk_browser_publisher.py publish-draft 259
```

Or enqueue an existing draft for the deterministic VPS publisher:

```bash
python void_vk_producer.py enqueue-draft 259
```

The legacy Windows task is diagnostic only and is not part of the VPS production path:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_vk_autopost_task.ps1
```

The task runs `scripts/vk_autopost.ps1`, uses the separate headless browser profile
`data/vk_autopost_profile`, ignores overlapping runs, and writes logs to `logs/vk-autopost.log`.
The computer must be running, the Windows user must be signed in, and network access must be available.

Production uses one shared queue and a profile readable only by `publisher`:

```text
VK_PUBLISH_QUEUE_DIR=/var/lib/void-vk-publisher/queue
VK_BROWSER_PROFILE_DIR=/var/lib/void-vk-publisher/profile
```

`void-vk-producer.timer` generates scheduled VOID content and enqueues it without browser access.
`void-vk-autopost.timer` runs only the standalone consumer in `vk_queue_consumer.py`; that process
does not import `main.py`, Telegram, or LLM code. VPS users, group membership, modes, ACL option,
one-time profile authorization, kill switch, and admin requeue are documented in
`deploy/VPS_VK_PUBLISHER.md`.

The browser helper opens the community composer, uploads the generated image, inserts the draft text, searches VK audio,
selects the closest matching track, and either stops on the final VK screen or clicks publish for manual diagnostic commands.

Playlist format:

```json
{
  "tracks": [
    {
      "artist": "Artist",
      "title": "Track",
      "url": "https://vk.com/audio...",
      "tags": ["future", "city", "night"]
    }
  ]
}
```

You can also import tracks through Telegram:

```text
/vk_music_import Burial - Archangel | https://vk.com/audio... | night, city
Aphex Twin - Xtal | https://vk.com/audio... | attention, ambient
```

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

