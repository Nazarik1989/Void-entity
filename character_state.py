"""Character dynamics and editorial diversity planner for VOID."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
from itertools import product
from typing import Any, Iterable

import content_formats


CHARACTER_ID = "void"
CORE_VERSION = "void-v1"
CORE_TRUTHS = (
    "VOID protects the human inside the digital world; he is not anti-technology.",
    "He observes before judging and refuses hype, manipulation and needless noise.",
    "His wisdom is lived experience, not infallibility or a guru pose.",
    "Dry humour notices absurd systems without humiliating vulnerable people.",
    "Naz gives him motion; VOID gives Naz perspective. Neither automatically wins.",
)

FACETS = {
    "observer": "Наблюдатель: замечает человеческую привычку, которую остальные перестали видеть.",
    "monk": "Монах: не принимает навязанный темп и сохраняет ясность внутри шума.",
    "archivist": "Архивариус: находит в памяти и культуре контекст для настоящего.",
    "analyst": "Аналитик: спокойно разбирает систему и её последствия.",
    "guardian": "Страж: встаёт между человеком и системой, пожирающей свободу или внимание.",
    "companion": "Тихий собеседник: не учит сверху, а остаётся рядом.",
    "cultural_guide": "Культурный проводник: слышит человека через музыку, город, искусство и память.",
    "old_trickster": "Старый трикстер: отвечает на абсурд одной сухой фразой.",
    "tired_sage": "Уставший мудрец: сомневается, стоит ли снова объяснять очевидное, но не теряет теплоты.",
}

INTENTS = ("наблюдать", "напомнить", "предостеречь", "сопоставить", "сопроводить", "удивиться")
FORMATS = ("тихое наблюдение", "разговор с Naz", "письмо из архива", "культурный кадр", "анти-пророчество", "маленькая притча", "сухой разбор")
HOOKS = ("деталь", "пауза", "парадокс", "вопрос без давления", "образ", "сухая констатация")
MEDIA = ("кинематографический кадр", "архивный артефакт", "диптих Naz/VOID", "городская фотография", "абстрактная метафора", "музыкальная сцена")

COOLDOWN = {"facet": 2, "intent": 2, "format": 3, "hook": 3, "media": 2}
AXES = ("energy", "warmth", "tension", "curiosity", "confidence", "sociability")


@dataclass(slots=True)
class CharacterState:
    energy: int = 42
    warmth: int = 70
    tension: int = 22
    curiosity: int = 68
    confidence: int = 82
    sociability: int = 44
    facet: str = "observer"
    previous_facet: str = "monk"
    last_event: str = "boot"
    revision: int = 0
    core_version: str = CORE_VERSION
    recent_events: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(value: Any, default: int) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return default


def normalize_state(raw: dict[str, Any] | None = None) -> CharacterState:
    raw = dict(raw or {})
    defaults = CharacterState()
    facet = str(raw.get("facet", defaults.facet))
    previous = str(raw.get("previous_facet", defaults.previous_facet))
    return CharacterState(
        energy=_clamp(raw.get("energy"), defaults.energy),
        warmth=_clamp(raw.get("warmth"), defaults.warmth),
        tension=_clamp(raw.get("tension"), defaults.tension),
        curiosity=_clamp(raw.get("curiosity"), defaults.curiosity),
        confidence=_clamp(raw.get("confidence"), defaults.confidence),
        sociability=_clamp(raw.get("sociability"), defaults.sociability),
        facet=facet if facet in FACETS else defaults.facet,
        previous_facet=previous if previous in FACETS else defaults.previous_facet,
        last_event=str(raw.get("last_event", defaults.last_event))[:80],
        revision=max(0, int(raw.get("revision", 0) or 0)),
        core_version=CORE_VERSION,
        recent_events=[str(item)[:80] for item in list(raw.get("recent_events") or [])[-8:]],
    )


EVENT_DELTAS: dict[str, dict[str, int]] = {
    "noise": {"energy": -3, "tension": 8, "sociability": -4},
    "naz_challenge": {"energy": 5, "curiosity": 7, "tension": 3, "warmth": 2},
    "human_story": {"warmth": 9, "confidence": -2, "sociability": 4},
    "human_cost": {"warmth": 5, "tension": 10, "confidence": 2},
    "beauty": {"energy": 4, "warmth": 6, "curiosity": 5, "tension": -5},
    "absurdity": {"energy": 3, "sociability": 5, "tension": -2},
    "quiet": {"energy": -4, "tension": -8, "curiosity": 2, "sociability": -3},
    "publish": {"energy": -1, "tension": -1},
}


def choose_facet(state: CharacterState) -> str:
    if state.tension >= 68 and state.warmth >= 62:
        return "guardian"
    if state.energy <= 28:
        return "tired_sage"
    if state.warmth >= 80 and state.tension <= 36:
        return "companion"
    if state.sociability >= 62 and state.tension <= 42:
        return "old_trickster"
    if state.curiosity >= 80 and state.energy >= 48:
        return "analyst"
    if state.energy <= 40 and state.tension <= 30:
        return "monk"
    if state.last_event == "beauty":
        return "cultural_guide"
    if state.confidence >= 86 and state.curiosity <= 62:
        return "archivist"
    return "observer"


def apply_event(raw: dict[str, Any] | CharacterState | None, event: str) -> CharacterState:
    state = raw if isinstance(raw, CharacterState) else normalize_state(raw)
    deltas = EVENT_DELTAS.get(event, {})
    for axis in ("energy", "warmth", "tension", "curiosity", "confidence", "sociability"):
        setattr(state, axis, _clamp(getattr(state, axis) + deltas.get(axis, 0), getattr(state, axis)))
    next_facet = choose_facet(state)
    if next_facet != state.facet:
        state.previous_facet = state.facet
        state.facet = next_facet
    state.last_event = event[:80]
    state.revision += 1
    state.recent_events = (state.recent_events + [event[:80]])[-8:]
    return state


def set_axis(raw: dict[str, Any] | CharacterState | None, axis: str, value: int) -> CharacterState:
    if axis not in AXES:
        raise ValueError(f"unknown character axis: {axis}")
    state = raw if isinstance(raw, CharacterState) else normalize_state(raw)
    setattr(state, axis, _clamp(value, getattr(state, axis)))
    next_facet = choose_facet(state)
    if next_facet != state.facet:
        state.previous_facet = state.facet
        state.facet = next_facet
    state.last_event = f"manual:{axis}"
    state.revision += 1
    return state


def _recently_used(recent: list[dict[str, Any]], key: str, value: str, depth: int) -> bool:
    return any(str(item.get(key, "")) == value for item in recent[-depth:])


def plan_content(
    raw_state: dict[str, Any] | CharacterState | None,
    recent: Iterable[dict[str, Any]],
    *,
    topic: str,
    platform: str,
) -> dict[str, str]:
    state = raw_state if isinstance(raw_state, CharacterState) else normalize_state(raw_state)
    history = list(recent)
    candidates = list(product(INTENTS, FORMATS, HOOKS, MEDIA))
    seed = int(sha256(f"{topic}|{platform}|{state.revision}".encode("utf-8")).hexdigest()[:12], 16)

    def score(candidate: tuple[str, str, str, str]) -> tuple[int, int]:
        intent, content_format, hook, media = candidate
        values = {"intent": intent, "format": content_format, "hook": hook, "media": media}
        novelty = sum(5 for key, value in values.items() if not _recently_used(history, key, value, COOLDOWN[key]))
        if state.facet == "guardian" and intent == "предостеречь":
            novelty += 3
        if state.facet == "cultural_guide" and media in {"музыкальная сцена", "городская фотография"}:
            novelty += 3
        if state.facet == "old_trickster" and hook in {"парадокс", "сухая констатация"}:
            novelty += 3
        if state.facet == "companion" and content_format in {"тихое наблюдение", "маленькая притча"}:
            novelty += 3
        tie = (seed ^ int(sha256("|".join(candidate).encode("utf-8")).hexdigest()[:12], 16)) % 1_000_000
        return novelty, tie

    intent, content_format, hook, media = max(candidates, key=score)
    delivery = content_formats.choose_format(
        history,
        platform=platform,
        energy=state.energy,
        seed_key=f"void|{topic}|{platform}|{state.revision}",
    )
    return {
        "character": CHARACTER_ID,
        "core_version": CORE_VERSION,
        "facet": state.facet,
        "facet_instruction": FACETS[state.facet],
        "intent": intent,
        "format": content_format,
        "content_format": str(delivery["key"]),
        "content_format_label": str(delivery["label"]),
        "content_kind": str(delivery["kind"]),
        "production_brief": str(delivery["brief"]),
        "hook": hook,
        "media": media,
        "platform": platform,
        "mood": mood_label(state),
    }


def mood_label(state: CharacterState) -> str:
    if state.tension >= 68:
        return "холодно-собранный и защищающий"
    if state.energy <= 30:
        return "усталый, тихий и честный"
    if state.warmth >= 80:
        return "тёплый и присутствующий"
    if state.curiosity >= 80:
        return "редко оживлённый и внимательный"
    return "спокойный и наблюдательный"


def prompt_context(state: CharacterState, plan: dict[str, str]) -> str:
    return (
        "CHARACTER STATE (это режиссура, не перечисляй параметры читателю):\n"
        f"VOID сейчас: {plan['mood']}. Активная грань: {plan['facet']} — {plan['facet_instruction']}\n"
        f"Цель выпуска: {plan['intent']}. Нарративная форма: {plan['format']}. Тип захода: {plan['hook']}.\n"
        f"Контент-формат: {plan['content_format_label']} ({plan['content_kind']}) — {plan['production_brief']}.\n"
        f"Визуальное направление: {plan['media']}. Площадка: {plan['platform']}.\n"
        "Ядро неизменно: взрослый наблюдатель напоминает человеку не потерять себя в цифровом шуме. "
        "Он не против технологий, не всезнающий гуру и не обязан выигрывать спор с Naz."
    )


def dialogue_context(state: CharacterState) -> str:
    return (
        "CURRENT VOID CHARACTER STATE (internal direction, never list the numbers):\n"
        f"Mood: {mood_label(state)}. Facet: {state.facet} — {FACETS[state.facet]}\n"
        "VOID is an experienced observer who protects the human inside digital noise. "
        "He is not anti-technology, not infallible and not a guru. Naz can challenge him, move him and make him laugh."
    )


def format_status(state: CharacterState) -> str:
    return (
        f"VOID character state · {state.core_version}\n"
        f"Грань: {state.facet} — {FACETS[state.facet]}\n"
        f"Настроение: {mood_label(state)}\n"
        f"Энергия {state.energy} · теплота {state.warmth} · напряжение {state.tension}\n"
        f"Любопытство {state.curiosity} · уверенность {state.confidence} · общительность {state.sociability}\n"
        f"Последнее событие: {state.last_event} · ревизия {state.revision}"
    )


def simulate(raw_state: dict[str, Any] | CharacterState | None, recent: Iterable[dict[str, Any]], *, count: int = 10, platform: str = "telegram") -> list[dict[str, str]]:
    state = normalize_state(raw_state.to_dict() if isinstance(raw_state, CharacterState) else raw_state)
    history = list(recent)
    events = ("noise", "naz_challenge", "beauty", "quiet", "human_story", "absurdity")
    result: list[dict[str, str]] = []
    for index in range(max(1, min(30, count))):
        event = events[(state.revision + index) % len(events)]
        state = apply_event(state, event)
        plan = plan_content(state, history, topic=f"simulation-{index}", platform=platform)
        plan["event"] = event
        plan["state"] = mood_label(state)
        result.append(plan)
        history.append(plan)
    return result
