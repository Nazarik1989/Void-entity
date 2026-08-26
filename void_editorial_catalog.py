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
    "content": "void-content-v2-ai-field-varied-register",
    "visual": "void-darkness-light-material-v1",
    "music": "void-vk-allowlist-full-catalog-lru-v3",
}


DAY_TENSIONS = (
    "demo versus durable use", "capability versus evidence", "speed versus judgment",
    "automation versus permission", "abundance versus taste", "convenience versus its invoice",
    "creative control versus plausible sameness", "assistance versus authority",
    "open access versus concentrated power", "synthetic performance versus consent",
    "new capability versus an old creative limit", "curiosity versus premature certainty",
    "playful experiment versus a fixed workflow", "wider access versus inherited gatekeeping",
)
NIGHT_TENSIONS = (
    "visible versus hidden", "memory versus reconstruction", "care versus control",
    "continuity versus novelty", "private weight versus public appearance",
    "stillness versus the task that remains",
)
DAY_AUTHOR_ROLES = (
    "sharp AI field observer", "adult technology columnist", "curious product skeptic",
    "cultural critic with technical literacy", "deadpan witness to industry theatre",
    "creator watching the tools change", "patient explainer of a non-obvious mechanism",
    "curious builder testing a possibility", "delighted skeptic who can admit when something works",
)
NIGHT_AUTHOR_ROLES = (
    "quiet observer", "custodian of a difficult detail", "patient cultural interpreter",
    "companion at the edge of the light", "archivist of an ordinary human trace",
)
DAY_HUMOR = (
    "none", "warm curiosity with no punchline", "light playful irony",
    "dry adult sarcasm", "one restrained absurd detail", "self-directed irony",
    "one sharp line without clowning", "wry but evidence-led",
)
DAY_EMOTIONAL_ARCS = (
    "curiosity to sharp recognition", "amusement to a serious consequence",
    "hype to a useful mechanism", "confidence to an earned correction",
    "irritation to a practical distinction", "wonder to a bounded conclusion",
    "play to an unexpected use", "appreciation to a clearer limit",
)
DAY_READER_RELATIONS = (
    "think beside the reader without lecturing",
    "let the reader enjoy the absurdity before naming the cost",
    "invite disagreement after making one clear claim",
    "offer a useful possibility without selling it",
    "admire a working result without becoming promotional",
)
DAY_STRUCTURES = (
    "two_people_contrast", "expectation_observation_gap", "rule_and_exception",
    "cold_open_punch", "three_signal_stack", "myth_mechanism_reality",
    "micro_dialogue_then_verdict", "field_note_with_receipt",
    "before_after_without_miracle",
)
DAY_HOOKS = (
    "one blunt comic recognition", "a concrete product behavior",
    "a result that ruins the headline", "a two-line imagined dialogue",
    "one precise number or constraint from the source",
    "an ordinary action made newly strange",
    "a confident industry claim followed by its invoice",
    "a capability that is simply worth looking at",
)
DAY_ENDINGS = (
    "dry verdict with a concrete consequence", "one responsibility made visible",
    "the boring test that still remains", "a practical distinction worth keeping",
    "an open question earned by evidence", "a useful possibility left open",
)
DAY_IMAGERY = (
    "one real device or tool under narrow light",
    "a creator's work surface after use",
    "the physical consequence of an AI decision",
)


BASE_POOLS: dict[str, tuple[str, ...]] = {
    "thesis_direction": tuple(str(card["thought"]) for cards in MEANING_CARDS.values() for card in cards),
    "epistemic_state": (
        "observed concrete detail", "bounded interpretation", "documented source signal",
        "honest uncertainty", "remembered detail with acknowledged limits",
    ),
    "tension": DAY_TENSIONS + NIGHT_TENSIONS,
    "semantic_theme": tuple(SEMANTIC_THEMES),
    "semantic_card": tuple(str(card["key"]) for cards in MEANING_CARDS.values() for card in cards),
    "facet": tuple(void_character.FACETS),
    "author_role": DAY_AUTHOR_ROLES + NIGHT_AUTHOR_ROLES,
    "emotional_arc": (
        "curiosity to sharp recognition", "amusement to a serious consequence",
        "hype to a useful mechanism", "confidence to an earned correction",
        "irritation to a practical distinction", "wonder to a bounded conclusion",
        "play to an unexpected use", "appreciation to a clearer limit",
        "night stillness to precise tension", "distance to recognition",
        "familiarity to changed meaning",
    ),
    "reader_relation": (
        "think beside the reader without lecturing", "share a field note rather than a lesson",
        "let the reader enjoy the absurdity before naming the cost",
        "trust the reader with technical nuance", "invite disagreement after making one clear claim",
        "stand beside the reader without demanding a conclusion",
        "offer a useful possibility without selling it",
        "admire a working result without becoming promotional",
    ),
    "structure": tuple(str(item["key"]) for item in NARRATIVE_SHAPES),
    "hook": (
        "one blunt comic recognition", "a concrete product behavior", "a result that ruins the headline",
        "a two-line imagined dialogue", "one precise number or constraint from the source",
        "an ordinary action made newly strange", "a confident industry claim followed by its invoice",
        "a capability that is simply worth looking at", "one night physical detail",
        "an exact hour and physical action", "an object whose meaning changed",
        "a restrained question",
    ),
    "ending": (
        "dry verdict with a concrete consequence", "one responsibility made visible",
        "the boring test that still remains", "return to the hook with its meaning reversed",
        "a practical distinction worth keeping", "an open question earned by evidence",
        "a useful possibility left open", "quiet return to the opening object",
        "partial revelation", "open but precise observation",
    ),
    "energy": ("low", "measured", "alive", "driven"),
    "seriousness": ("light", "balanced", "serious", "quietly weighty"),
    "tempo": ("slow", "measured", "brisk", "punchy"),
    "length": ("280-450 characters", "500-750 characters", "800-1100 characters", "1100-1500 characters"),
    "humor": DAY_HUMOR,
    "imagery": (
        "one real device or tool under narrow light", "a creator's work surface after use",
        "the physical consequence of an AI decision", "tactile material and traces of use",
        "a screen reflection without readable interface text", "one central meaningful object",
        "darkness concealing a real studio, laboratory or room",
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
        "future,energy,tension", "future,calm", "energy,warm", "energy,night",
        "calm,warm", "dark,calm,night", "dark,melancholy,calm",
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
SEMANTIC_CARD_THESES = {
    str(card["key"]): str(card["thought"])
    for cards in MEANING_CARDS.values()
    for card in cards
}


MODE_TRACK_TAGS = {
    "frequency": "energy,night,warm",
    "future": "future,energy,tension",
    "material": "dark,calm,melancholy",
    "midnight": "night,dark,calm",
    "vault": "melancholy,dark,calm",
    "observation": "warm,calm,tension",
    "signal": "future,energy,tension",
    "news": "future,energy,tension",
}


NIGHT_MODES = frozenset({"midnight", "vault"})
NIGHT_CONSTRAINTS: dict[str, tuple[str, ...]] = {
    "tension": NIGHT_TENSIONS,
    "author_role": NIGHT_AUTHOR_ROLES,
    "emotional_arc": (
        "night stillness to precise tension", "distance to recognition",
        "familiarity to changed meaning",
    ),
    "reader_relation": (
        "stand beside the reader without demanding a conclusion",
        "share a field note rather than a lesson",
        "trust the reader with technical nuance",
    ),
    "structure": (
        "scene_tension_reversal", "object_biography", "same_detail_before_after",
        "open_observation", "process_anatomy",
    ),
    "hook": (
        "one night physical detail", "an exact hour and physical action",
        "an object whose meaning changed", "a restrained question",
    ),
    "ending": (
        "quiet return to the opening object", "partial revelation",
        "open but precise observation", "return to the hook with its meaning reversed",
    ),
    "energy": ("low", "measured"),
    "seriousness": ("serious", "quietly weighty"),
    "tempo": ("slow", "measured"),
    "length": ("700-1000 characters", "950-1300 characters"),
    "humor": ("none", "one restrained absurd detail"),
    "imagery": (
        "tactile material and traces of use", "one central meaningful object",
        "darkness concealing a real studio, laboratory or room",
        "a screen reflection without readable interface text",
    ),
}
DAY_CONSTRAINTS: dict[str, tuple[str, ...]] = {
    "tension": DAY_TENSIONS,
    "author_role": DAY_AUTHOR_ROLES,
    "emotional_arc": DAY_EMOTIONAL_ARCS,
    "reader_relation": DAY_READER_RELATIONS,
    "structure": DAY_STRUCTURES,
    "hook": DAY_HOOKS,
    "ending": DAY_ENDINGS,
    "energy": ("measured", "alive", "driven"),
    "seriousness": ("light", "balanced", "serious"),
    "tempo": ("measured", "brisk", "punchy"),
    "length": (
        "280-450 characters", "500-750 characters",
        "800-1100 characters", "1100-1500 characters",
    ),
    "humor": DAY_HUMOR,
    "imagery": DAY_IMAGERY,
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
            "track_tags": (
                str(row.get("track_tags") or MODE_TRACK_TAGS.get(mode, "calm,dark")),
            ),
        }
        constraints.update(NIGHT_CONSTRAINTS if mode in NIGHT_MODES else DAY_CONSTRAINTS)
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
                weight=max(1, int(row.get("weight", 1) or 1)),
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
            semantic_themes=tuple(
                str(item) for item in row.get("semantic_themes", ()) if str(item)
            ),
            weight=max(1, int(row.get("weight", 1) or 1)),
        )
        for index, row in enumerate(source_rows)
    )
    preferred_energy = "alive" if character.energy >= 45 else "measured"
    explicit_sizes = dict(PERSONA_POOL_SIZES)
    explicit_sizes.update({
        str(axis): int(size) for axis, size in (persona_pool_sizes or {}).items()
    })
    return EditorialContext(
        persona="void", platform=platform, slot=slot, seed=seed,
        sources=sources, rubrics=tuple(rubrics), pools=BASE_POOLS,
        persona_pool_sizes=explicit_sizes,
        semantic_cards=SEMANTIC_CARDS,
        semantic_card_theses=SEMANTIC_CARD_THESES,
        published_history=tuple(published_history),
        preferred={
            "facet": character.facet,
            "energy": preferred_energy,
        },
        policy_versions=POLICY_VERSIONS, crosspost_plan_id=crosspost_plan_id,
    )


AI_EDITORIAL_MODES = frozenset(
    {"signal", "observation", "frequency", "future", "news", "culture", "archive", "digest"}
)


def persona_direction(
    character: void_character.CharacterState,
    mode: str = "",
) -> str:
    if mode in NIGHT_MODES:
        scheduled_direction = (
            "This is the deliberately slower night register. Do not force an AI subject, product, "
            "industry mechanism, joke, list, or verdict into it. Let the selected human, cultural, "
            "material, or remembered detail carry the thought."
        )
    elif mode in AI_EDITORIAL_MODES:
        scheduled_direction = (
            "For this scheduled day or evening release, the whole AI field is the main editorial "
            "territory: models, agents, video, images, audio, voice, devices, science, creators, "
            "products, law, labor, and power. Make the selected subject concrete and keep evidence "
            "separate from inference. Vary length and structure across the feed. Do not default to "
            "sadness or a warning: curiosity, useful appreciation, play, and a straight register are "
            "as valid as adult sarcasm when the selected axes call for them."
        )
    else:
        scheduled_direction = (
            "Follow the selected subject and register without forcing it into an AI news angle."
        )
    return (
        f"VOID observes before speaking. Current facet: {character.facet} — "
        f"{void_character.FACETS[character.facet]} Current mood: {void_character.mood_label(character)}. "
        "He is an adult participant in the current era, technically curious rather than anti-technology, "
        "mature but not infallible. Use sarcasm or irony only when the selected humor axis asks for it, "
        "never clowning or punching down. "
        f"{scheduled_direction} "
        "Preserve the canonical Darkness, and somewhere within it — light visual identity and MATERIAL rules."
    )
