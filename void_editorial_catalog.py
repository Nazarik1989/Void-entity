"""VOID catalog adapter for Editorial Orchestrator v1; contains no selectors."""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping

import character_state as void_character
from editorial_orchestrator import EditorialContext, EditorialRubric, EditorialSource
from void_core import (
    MEANING_CARDS,
    MODE_SEMANTIC_THEMES,
    NARRATIVE_SHAPES,
    RUBRIC_SCHEDULE,
    SCENE_AXES,
    SEMANTIC_THEMES,
    TELEGRAM_VOID_SCHEDULE,
)


POLICY_VERSIONS = {
    "content": "void-content-v1-orchestrated",
    "visual": "void-darkness-light-material-v1",
    "music": "void-vk-allowlist-last8-v1",
}


BASE_POOLS: dict[str, tuple[str, ...]] = {
    "thesis_direction": tuple(str(card["thought"]) for cards in MEANING_CARDS.values() for card in cards),
    "epistemic_state": (
        "observed concrete detail", "bounded interpretation", "documented source signal",
        "honest uncertainty", "remembered detail with acknowledged limits",
    ),
    "tension": (
        "visible versus hidden", "use versus display", "memory versus reconstruction",
        "care versus control", "continuity versus novelty", "attention versus habit",
        "private weight versus public appearance",
    ),
    "semantic_theme": tuple(SEMANTIC_THEMES),
    "semantic_card": tuple(str(card["key"]) for cards in MEANING_CARDS.values() for card in cards),
    "facet": tuple(void_character.FACETS),
    "author_role": (
        "quiet observer", "custodian of a difficult detail", "skeptical witness",
        "patient cultural interpreter", "guardian of human proportion",
    ),
    "emotional_arc": (
        "distance to recognition", "stillness to precise tension", "uncertainty to honest proportion",
        "weight to a restrained opening", "familiarity to changed meaning",
    ),
    "reader_relation": (
        "stand beside the reader in silence", "offer an observation rather than a lesson",
        "trust the reader to complete the thought", "name complexity without simplifying the reader",
    ),
    "structure": tuple(str(item["key"]) for item in NARRATIVE_SHAPES),
    "hook": (
        "one physical detail", "a threshold already in use", "a quiet contradiction",
        "an object whose meaning changed", "an exact hour and action", "a restrained question",
    ),
    "ending": (
        "partial revelation", "substantially changed conclusion", "open but precise observation",
        "return to the opening object", "one responsibility made visible",
    ),
    "energy": ("very low", "low", "measured"),
    "seriousness": ("restrained", "serious", "quietly grave"),
    "tempo": ("slow", "measured", "deliberate"),
    "length": ("700-950 characters", "850-1150 characters", "1000-1400 characters"),
    "humor": ("none", "dry and minimal", "one quiet absurdity"),
    "imagery": (
        "darkness concealing real space", "one narrow source of light", "tactile material and traces of use",
        "visible-hidden boundary", "reflection in black glass or water", "one central meaningful object",
    ),
    "visual_mode": ("canonical VOID still", "object study", "threshold scene", "MATERIAL sequence"),
    "visual_subject_direction": tuple(str(item["instruction"]) for item in SCENE_AXES),
    "visual_relation": (
        "the light reveals only the physical fact that carries the thesis",
        "the object's wear makes the plan's claim about memory visible",
        "the threshold embodies the tension between what is known and hidden",
        "the same concrete action connects the text's scene and visual subject",
        "the reflection shows the changed relation without adding a new character",
    ),
    "track_tags": (
        "calm,dark,reflective", "ambient,calm,memory", "city,dark,measured",
        "future,restrained,focus", "material,calm,organic",
    ),
}


_ALL_RUBRIC_NAMES = tuple(
    dict.fromkeys(str(row["name"]) for row in (*RUBRIC_SCHEDULE, *TELEGRAM_VOID_SCHEDULE))
)
PERSONA_POOL_SIZES: dict[str, int] = {
    axis: len(tuple(dict.fromkeys(values)))
    for axis, values in BASE_POOLS.items()
}
PERSONA_POOL_SIZES.update(
    {
        "rubric": len(_ALL_RUBRIC_NAMES),
        "source_ref": len(_ALL_RUBRIC_NAMES),
        "content_format": 2,
        "production_mode": 2,
    }
)


SEMANTIC_CARDS = {
    theme: tuple(str(card["key"]) for card in MEANING_CARDS.get(theme, ()))
    for theme in SEMANTIC_THEMES
}


MODE_TRACK_TAGS = {
    "frequency": "music,dark,reflective",
    "future": "future,restrained,focus",
    "material": "material,calm,organic",
    "midnight": "ambient,dark,calm",
    "vault": "memory,dark,reflective",
    "observation": "calm,city,reflective",
    "news": "focus,measured,future",
}


def rubric_key(name: str) -> str:
    return hashlib.sha256(str(name).encode("utf-8")).hexdigest()[:12]


def build_context(
    *, platform: str, slot: str, seed: str,
    rubric_rows: Iterable[Mapping[str, Any]], source_rows: Iterable[Mapping[str, Any]],
    published_history: Iterable[Mapping[str, Any]], character: void_character.CharacterState,
    crosspost_plan_id: str = "",
    persona_pool_sizes: Mapping[str, int] | None = None,
) -> EditorialContext:
    rubrics: list[EditorialRubric] = []
    for row in rubric_rows:
        name = str(row.get("name") or "VOID release")
        key = str(row.get("key") or rubric_key(name))
        mode = str(row.get("mode") or "signal")
        themes = tuple(MODE_SEMANTIC_THEMES.get(mode, SEMANTIC_THEMES))
        material = mode == "material" or "material" in name.casefold() or "матери" in name.casefold()
        constraints: dict[str, tuple[str, ...]] = {
            "semantic_theme": themes,
            "track_tags": (str(row.get("track_tags") or MODE_TRACK_TAGS.get(mode, "calm,dark,reflective")),),
            "length": (("700-1100 characters",) if platform == "telegram" else ("700-1400 characters",)),
        }
        if str(row.get("voice") or "void") == "news":
            constraints["epistemic_state"] = ("documented source signal",)
        if material:
            constraints.update(
                {
                    "visual_mode": ("MATERIAL sequence",),
                    "imagery": ("tactile material and traces of use",),
                    "visual_subject_direction": ("Use one stone, wood, leather, paper, fabric, smoked-glass or blackened-metal object with visible time and use; no invented person.",),
                }
            )
        rubrics.append(
            EditorialRubric(
                key=key, name=name, mode=mode,
                purpose=str(row.get("brief") or "reveal one weighted observation without explaining everything"),
                constraints=constraints,
            )
        )
    sources = tuple(
        EditorialSource(
            source_ref=str(row.get("source_ref") or f"catalog:{index}"),
            topic=str(row.get("topic") or "one concrete object, action or encounter"),
            source_type=str(row.get("source_type") or "catalog"),
            rubric_keys=tuple(str(item) for item in row.get("rubric_keys", ())),
            safe_facts=tuple(str(item)[:500] for item in row.get("safe_facts", ()) if str(item).strip()),
            source_verified=bool(row.get("source_verified", False)),
            concrete_action=bool(row.get("concrete_action", False)),
            visualizable_process=bool(row.get("visualizable_process", False)),
            causal_bits=max(0, int(row.get("causal_bits", 0) or 0)),
            real_result=bool(row.get("real_result", False)),
            contains_secrets=bool(row.get("contains_secrets", False)),
            contains_private_data=bool(row.get("contains_private_data", False)),
        )
        for index, row in enumerate(source_rows)
    )
    preferred_energy = "very low" if character.energy <= 38 else "low"
    explicit_sizes = dict(PERSONA_POOL_SIZES)
    explicit_sizes.update({
        str(axis): int(size) for axis, size in (persona_pool_sizes or {}).items()
    })
    return EditorialContext(
        persona="void", platform=platform, slot=slot, seed=seed,
        sources=sources, rubrics=tuple(rubrics), pools=BASE_POOLS,
        persona_pool_sizes=explicit_sizes,
        semantic_cards=SEMANTIC_CARDS, published_history=tuple(published_history),
        preferred={"facet": character.facet, "energy": preferred_energy},
        policy_versions=POLICY_VERSIONS, crosspost_plan_id=crosspost_plan_id,
    )


def persona_direction(character: void_character.CharacterState) -> str:
    return (
        f"VOID observes before speaking. Current facet: {character.facet} — "
        f"{void_character.FACETS[character.facet]} Current mood: {void_character.mood_label(character)}. "
        "He is mature but not infallible, protective of human proportion without becoming anti-technology. "
        "Write with quiet precision, concrete evidence and room for uncertainty. Preserve the canonical "
        "Darkness, and somewhere within it — light visual identity and MATERIAL rules."
    )
