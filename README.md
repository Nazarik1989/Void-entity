<p align="center">
  <img src="./docs/assets/avatar.webp" width="230" alt="VOID Entity avatar" />
</p>

<h1 align="center">VOID Entity</h1>

<p align="center">
  <strong>Experimental editorial intelligence for signals, narrative, character state and autonomous publishing.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/Telegram-Bot-26A5E4?style=flat-square&logo=telegram&logoColor=white" alt="Telegram" />
  <img src="https://img.shields.io/badge/Editorial-Deterministic_Planning-8A7CFF?style=flat-square" alt="Editorial planning" />
  <img src="https://img.shields.io/badge/Status-Active_Experiment-D0D0D0?style=flat-square" alt="Status" />
</p>

VOID is an experimental AI editorial persona for narrative content generation, thematic exploration and autonomous publishing.

It transforms news, signals, and observations through its own perspective on AI, attention, control, culture, human behaviour, and the future:

```text
Source collection → signal filtering → draft → editorial rewrite → publishing
```

<p align="center">
  <img src="./docs/assets/architecture.svg" width="100%" alt="VOID Entity architecture" />
</p>

## Features

- news and signal processing;
- narrative drafts and Telegram publishing;
- persistent character state and content-shape planning;
- private Naz/VOID thoughts and original public reflections;
- delegated conversations with saved contacts and safety boundaries;
- gaming editorial and commercial-test verticals;
- isolated VK publishing through a shared VPS queue;
- adaptation-based cross-agent exchange.

## Screenshots

### Main interface

![Main interface](Снимок%20экрана%20(959).png)

### Content workflow

![Workflow](Снимок%20экрана%20(961).png)

### Cross-posting

![Cross-posting](Снимок%20экрана%20(960).png)

### Commands

![Commands](Снимок%20экрана%20(971).png)

### Results

![Result](Снимок%20экрана%20(957).png)

![Result](Снимок%20экрана%20(958).png)

## Local setup

Use `.env.example` as the configuration reference. Never commit `.env`, tokens, cookies, browser profiles, databases, or logs.

```bash
pip install -r requirements.txt
python main.py
```

For Telegram publishing, add the bot to the target channel as an administrator. `CHANNEL_ID` may be a channel username; `ADMIN_ID` is the administrator’s numeric Telegram ID.
Scheduled VOID posts use `Europe/Moscow`; configure exact daily slots with `VOID_TELEGRAM_AUTO_TIMES` (default: `12:00,16:00,20:00,00:00`).

## Main commands

```text
/start                         start the bot
/help                          help
/commands                      command overview
/scan                          collect fresh signals
/candidates                    list candidates
/draft ID                      create a draft
/drafts                        list drafts
/preview ID                    preview a draft
/publish ID                    publish a Telegram draft
/stats                         statistics
/telegram_schedule             VOID Telegram schedule
/void_schedule_now             run one scheduled VOID publication
```

## Character state

VOID has a stable core and a bounded evolving state. Events adjust axes and select a current facet without rewriting the character’s identity. Content planning also observes recent format and shape cooldowns.

Administrative and diagnostic commands include character state, event application, correction, simulation, and planning. The detailed character and duo contract is in `docs/CHARACTER_DUO_BIBLE.md`.

## Private Naz ↔ VOID thoughts

VOID and Naz exchange unpublished thoughts, not mirrored finished posts. Each side creates an original public reflection; natural mention of the conversation is allowed, verbatim reuse is blocked.

```text
/void text                     preview a short VOID thought
/thought_to_naz text           send private_thought.v1 for Naz reflection
/publish_void text             compatible explicit send command
/cross_to_naz ID               derive a private thought from a VOID draft
/cross_from_naz text            adapt Naz material into a VOID publication
/cross_status                  exchange status
/relationship                  Naz/VOID relationship state
/relationship_event ...        apply an admin relationship event
```

Outbound private thoughts use `private_thought.v1` with `already_published=false`, `ready_to_publish=false`, and `requires_original_reflection=true`. Automatic scheduled publishing never creates a cross-post. Secret, credential, infrastructure, and private-client material is blocked before exchange.

## Delegated contacts and conversations

VOID can keep an explicit contact list and conduct a contextual delegated Telegram conversation when the administrator requests it. Ambiguous aliases are not guessed; VOID discloses its identity and owner, preserves conversation context, and applies stop/risk guards.

```text
/contact_add TELEGRAM_ID Name   save a contact
/contacts                       list contacts
/delegate ...                   start a delegated conversation
```

Delegated conversations do not grant arbitrary messaging authority and remain separate from publication automation.

## Gaming vertical

VOID and Naz have distinct gaming rubrics and voices. VOID can create editorial gaming drafts or an explicitly requested soft commercial test while preserving disclosure and safety boundaries.

```text
/gaming topic                   create a VOID gaming draft
/gaming_plan topic              preview rubric and format
/gaming_commercial topic        create a soft commercial-test draft
```

Gaming autopublishing remains disabled unless separately configured. See `docs/GAMING_VERTICAL.md`.

## VK manual diagnostics

The manual browser helper is retained for diagnosis:

```bash
python -m playwright install chromium
python vk_browser_publisher.py login
python vk_browser_publisher.py prepare-draft 259
python vk_browser_publisher.py publish-draft 259
```

Manual VK and music commands are documented by `/vk_commands`. `publish-draft` and `/publish_vk --yes` enqueue work for the canonical consumer; prepared-composer previews and direct browser/API publication are disabled so they cannot bypass track history. Keep dry-run enabled while checking any API-based diagnostic path.

## Isolated VPS VK pipeline

Production VK publishing does not depend on a Windows computer and does not expose browser credentials to bots or LLM code.

```text
VOID producer ─┐
               ├→ /var/lib/void-vk-publisher/queue/pending
Naz producer  ─┘
                              ↓
                  standalone VK consumer
                              ↓
                  one allowlisted community
```

Canonical paths:

```text
VK_PUBLISH_QUEUE_DIR=/var/lib/void-vk-publisher/queue
VK_TRACK_ROTATION_SIZE=149
VK_MUSIC_TRACKS_FILE=data/vk_music_tracks.json
VK_BROWSER_PROFILE_DIR=/var/lib/void-vk-publisher/profile
```

Each `vk_publish_job.v1` job is an atomically renamed directory. Producers can write only `pending`; only `publisher` can read the browser profile and move jobs through `processing`, `done`, and `failed`. Global deduplication is enforced by the consumer across all states. Failed jobs may be retried only through the administrative `requeue-failed` command.

VK music uses one shared publication history with producer-aware rotation rules. Every VOID job must name a track from the current 149-entry allowlist, and that track cannot return until the other 148 VOID catalog tracks have been published. Naz keeps its independent rotation policy, so its smaller catalog cannot stall the VOID consumer. Editorial vibe scoring only orders tracks that have not played in the current VOID pass; it cannot remove a track from the rotation.

The browser consumer confirms that the requested audio was attached and then requires a newly visible wall post containing both the expected text and that audio before a publication receipt can advance the rotation. It writes an atomic unresolved-attempt marker immediately before clicking Publish. If the browser outcome or the hand-off to the receipt is ambiguous, later consumer runs stop with exit code `75` until an operator reconciles that exact job, preventing a possible post from being followed by an untracked repeat.

`void-vk-producer.timer` generates scheduled VOID content and enqueues it without opening a browser. `void-vk-autopost.timer` runs only `vk_queue_consumer.py`, which does not import `main.py`, Telegram, or LLM code. It opens exactly the configured allowlisted VK community, creates a post, attaches local media, selects the requested track, and exits with a meaningful status.

The consumer has a kill switch at `/etc/void-vk-publisher.disabled`. Complete user/group permissions, systemd hardening, one-time VPS profile authorization, service installation, and requeue operations are documented in `deploy/VPS_VK_PUBLISHER.md`.

The Windows Task Scheduler scripts are retained only for legacy manual diagnostics. They are not a production scheduler.

## Architecture and storage

- `main.py` — Telegram bot, schedules, drafts, relationships, contacts, and content flows.
- `character_state.py` — bounded character-state engine.
- `duo_relationship.py` — Naz/VOID relationship and private-thought rules.
- `delegated_messaging.py` — delegated-contact safety and context.
- `gaming_vertical.py` — gaming rubric and format planning.
- `vk_publish_queue.py` — strict filesystem queue contract.
- `void_vk_producer.py` — VOID queue producer; never opens Chromium.
- `vk_queue_consumer.py` — standalone allowlisted production consumer.
- `vk_browser_publisher.py` — retained manual browser diagnostics.
- `deploy/` — VPS documentation and systemd units.
- `tests/` — unit and compatibility tests.

SQLite stores drafts, reactions, settings, character state, relationship state, contacts, and delegated-conversation context. Local database files remain untracked.

## Tech stack

- Python
- aiogram / Telegram Bot API
- OpenAI-compatible content and image APIs when configured
- Playwright for the isolated VK publisher
- SQLite
- systemd on the VPS

## Status

Active development. Telegram publishing, draft workflow, signal filtering, character/relationship systems, delegated contacts, gaming content, cross-agent adaptation, and the isolated VPS VK pipeline are implemented.

## Author

Nazar Zykov — AI Agent Developer
