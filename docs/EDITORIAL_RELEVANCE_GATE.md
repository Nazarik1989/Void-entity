# VOID publication contract

The publication pipeline applies one precedence order: security/access, schedule/type, VOID persona, current immutable brief, VOID visual bible, music allowlist/shared last-eight rotation, then creative variation.

Scheduled Telegram and VK create `editorial_policy.ContentBrief` before text generation. It records the typed source, scheduled rubric, thesis, context, visual subject, visual relation, people policy, visual version, and music requirement. Text generation, semantic duplicate review, text relevance review, image prompting, image semantic QA, and VK job metadata all use that same brief.

The audit found these divergence points:

- scheduled text and image subjects were selected from separate rotations;
- a failed news slot could silently switch to a different non-news rubric;
- image failure could use an arbitrary article image or permit a text-only scheduled post;
- the VK producer could queue a draft before any image semantic QA;
- `void_editor.py` and `prompts/void_editor_prompt.txt` were unused competing prompt sources;
- publication jobs had no persona/editorial/visual policy versions.

Scheduled publication now keeps its original rubric, has no source-image or text-only fallback, validates generated images literally against the brief, and deletes an unpublished scheduled draft if required media cannot pass. The validator only accepts or rejects and returns a reason code. Existing `vk_publish_job.v1` jobs remain readable; new automated jobs use `v2` safe metadata.

No prompt, private post, secret, environment value, or private-memory content is emitted in structured logs or queue metadata.
