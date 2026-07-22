# VOID v14 laboratory

VOID v14 is an orthogonal, manual experiment inside this repository. It is not an editorial layer and is not imported or called by Telegram/VK schedules, producers, the queue consumer, Voice Hub, or realtime adapters.

## Routes

- Normal messages remain on the existing stable VOID route.
- `/v14 stable <request>` invokes that exact stable route and does not build or call a v14 provider.
- `/v14 experimental <request>` is admin/allowlist-only, runs only v14, and writes metadata only to its separate experimental SQLite file.
- `/v14 hybrid <request>` asks stable VOID for a non-persistent candidate, runs v14 independently, and persists only the final synthesis after Telegram confirms delivery.

No mode becomes a persistent default. Contacts never enter experimental or hybrid mode automatically.

## Boundaries

`VoidV14Config` owns thresholds, timeout, concurrency, token/cost budget, retries, retention, and rounds. Madness Reentry is disabled and `max_rounds=0`. The conflict engine compares typed claims only between distinct agent pairs and classifies COLLAPSE before CONFLICT.

Experimental storage uses WAL and `(user_id, trace_id)` isolation. It stores trace metadata and a synthesis hash, not requests, stable context, response text, credentials, or user messages. The configured path must differ from the stable database.

All automated tests use `FakeLLMProvider`; they perform no network calls. `OpenAIProvider` is lazy and can only be constructed by an explicit experimental/hybrid command. Stable and production publication behavior do not depend on it.
