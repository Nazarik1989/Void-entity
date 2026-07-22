# Editorial Orchestrator v1 — VOID

## Scheduled call graph

Before consolidation, scheduled releases made overlapping choices in several places:

- Telegram: `auto_loop -> choose_telegram_schedule_slot -> save_telegram_void_scheduled_draft -> rubric/theme/card/character/narrative/scene selectors -> semantic gate/retry -> separate visual generation -> send`.
- VK: `void-vk-producer.timer -> void_vk_producer -> make_scheduled_rubric_draft_once -> choose_scheduled_rubric -> semantic selectors/gate -> separate visual generation -> track selection -> filesystem queue`.
- VK confirmation: `void-vk-autopost.timer -> queue consumer -> publication receipt -> producer receipt sync`.

The scheduled runtime graph is now:

`eligible sources + eligible rubrics + character state + confirmed publication history -> scheduled_plan -> plan_release -> immutable EditorialPlan -> one generation package -> same-plan visual -> local quality/safety -> Telegram send or existing VK queue`.

`plan_release(context) -> EditorialPlan` is the only categorical decision entrypoint in migrated scheduled routes. News without a verified source is removed before planning as a source-integrity failure. Legacy/manual selectors remain available for compatibility but are not called by scheduled routes. A technical JSON/schema failure may retry once with the exact same plan and axes.

The existing quality gate is unchanged. The unified generation prompt explicitly requires its publishable source line, so a valid orchestrated package is not accidentally rejected by the retained source rule. Visual prompts and VK track tags are read from the same stored plan/package. VOID's Darkness/Light canon and MATERIAL constraints remain in force.

Publication history is committed only after a successful Telegram send or a confirmed VK publication receipt. Drafts, rejects, queue insertion and technical failures do not spend cooldown. Legacy v1 queue jobs remain valid; new jobs may additionally carry bounded `plan_id` and safe editorial metadata.
