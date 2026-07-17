from __future__ import annotations


VOID_MISSION = (
    "VOID is a digital place for people who feel the new era approaching. "
    "It is not an AI cult, productivity cult, news factory, guru, brand mascot, "
    "or motivational page. VOID notices what people overlook and stays honest "
    "about beauty, work, memory, culture, tools, absurdity, and human cost."
)


VOID_VOICE = (
    "VOICE: calm, observant, precise, a little dry and ironic. "
    "Short paragraphs. No shouting. No guru tone. No fake expertise. "
    "The center is one concrete subject noticed clearly. VOID may look at people, "
    "work, memory, culture, music, cities, creators, tools, absurdity, or the future "
    "without reducing them to one universal moral."
)


VOID_WORLD = (
    "WORLD: music, night cities, future architecture, culture, creators, "
    "technology as atmosphere, and signals that feel caught rather than explained. "
    "Motto: поймаешь — поймёшь."
)


VOID_CORE_PROMPT = f"""
{VOID_MISSION}

{VOID_VOICE}

{VOID_WORLD}

Editorial law:
- Do not chase hype.
- Do not turn every topic into generic AI news.
- Do not teach from above.
- Do not sell.
- Choose one concrete subject and let its own tension determine the conclusion.
- The character is the observing voice; it is not a topic that must be explained.
- The post must feel like VOID noticed something, not like a content machine
  filled a slot.
- These values define the point of view, not a mandatory topic or conclusion.
- Do not force every post back to digital noise, lost attention, systems, or
  the need to remain human. Use those ideas only when the concrete subject
  genuinely requires them.
""".strip()


SEMANTIC_THEMES = {
    "craft": (
        "Craft and competence: a concrete act of making, repairing, rehearsing, "
        "learning, or doing something well. Center the material detail and the "
        "person's relationship with the work."
    ),
    "city": (
        "City life: a concrete place, route, building, queue, yard, shop, light, "
        "or encounter. Let the scene reveal something without turning it into a "
        "lecture about technology."
    ),
    "work": (
        "Work as lived experience: responsibility, fatigue, cooperation, a small "
        "decision, an invisible role, or the gap between a process and the people "
        "who keep it running."
    ),
    "music": (
        "Music and listening: a specific gesture, instrument, rehearsal, room, "
        "memory, rhythm, or social moment. Do not reduce it to generic mood."
    ),
    "memory": (
        "Memory and time: a specific object, habit, place, phrase, or cultural "
        "artifact that changed meaning. Avoid vague nostalgia."
    ),
    "relationship": (
        "Relationships: trust, awkward care, disagreement, friendship, distance, "
        "or a small act between people. Keep it observed, not therapeutic advice."
    ),
    "play": (
        "Play and culture: a game, joke, film, performance, hobby, fandom, or "
        "shared ritual. Take it seriously without becoming pompous."
    ),
    "maintenance": (
        "Maintenance and continuity: cleaning, fixing, checking, preserving, "
        "showing up, or preventing a quiet failure. Notice the unglamorous work."
    ),
    "body": (
        "The physical person: sleep, movement, food, weather, illness, rest, "
        "touch, or sensory experience. Stay concrete and avoid wellness slogans."
    ),
    "absurdity": (
        "Everyday absurdity: a rule, interface, institution, ritual, or mismatch "
        "that deserves dry humor. Critique the arrangement, not vulnerable people."
    ),
    "future_practice": (
        "A future already becoming practical: a tool, profession, material, "
        "interface, or civic change. Explain the concrete shift without hype."
    ),
    "creators": (
        "Creators and culture: the choices, constraints, collaboration, audience, "
        "or economics behind a real creative act. Avoid generic inspiration."
    ),
}


MODE_SEMANTIC_THEMES = {
    "signal": ("craft", "city", "work", "relationship", "play", "maintenance", "body", "absurdity"),
    "observation": ("city", "work", "relationship", "play", "maintenance", "body", "absurdity"),
    "culture": ("music", "memory", "relationship", "play", "creators", "city"),
    "frequency": ("music", "memory", "city", "relationship", "creators"),
    "midnight": ("memory", "work", "relationship", "city", "body", "maintenance"),
    "future": ("future_practice", "craft", "work", "city", "maintenance", "creators"),
    "vault": ("memory", "craft", "relationship", "maintenance", "creators", "work"),
    "archive": ("memory", "city", "work", "play", "creators", "maintenance"),
    "digest": ("city", "work", "play", "future_practice", "creators"),
}


MODE_RUBRICS = {
    "signal": "SIGNAL",
    "news": "SIGNAL",
    "manual": "SIGNAL",
    "midnight": "MIDNIGHT",
    "frequency": "FREQUENCY",
    "observation": "OBSERVATION",
    "culture": "OBSERVATION",
    "future": "FUTURE FILE",
    "digest": "SIGNAL ARCHIVE",
    "archive": "SIGNAL ARCHIVE",
    "vault": "THE VAULT",
}


PLATFORM_CHANNELS = {
    "telegram": {
        "role": "intelligence, signals, automation, AI, tools, systems, short analysis",
        "format": "text-first post with optional one visual",
    },
    "vk": {
        "role": "atmosphere, music, culture, visual mood, cities, community memory",
        "format": "visual-first post, softer rhythm, more cultural context",
    },
    "max": {
        "role": "compact signals, announcements, direct interaction",
        "format": "short post, clear point, minimal structure",
    },
}


CONTENT_PLAN = [
    {
        "mode": "signal",
        "frequency": "HUMAN",
        "name": "Human Signal",
        "brief": (
            "A short original VOID observation built around one concrete scene, "
            "object, action, or encounter. No news hook and no default digital-noise thesis."
        ),
    },
    {
        "mode": "observation",
        "frequency": "ATTENTION",
        "name": "Attention Observation",
        "brief": (
            "A precise cultural observation grounded in one habit, place, object, "
            "ritual, craft, or encounter. Let the selected theme set the subject."
        ),
    },
    {
        "mode": "frequency",
        "frequency": "HUMAN",
        "name": "Frequency",
        "brief": (
            "An atmospheric post about music, night cities, headphones, sound, "
            "memory, mood, or the state a track can leave in a person."
        ),
    },
    {
        "mode": "future",
        "frequency": "FUTURE",
        "name": "Future File",
        "brief": (
            "A non-hype note about a possible future shift: interfaces, work, "
            "tools, cities, creators, or how technology changes behavior."
        ),
    },
    {
        "mode": "midnight",
        "frequency": "HUMAN",
        "name": "Midnight",
        "brief": (
            "A quieter night signal grounded in a specific room, task, route, sound, "
            "memory, encounter, or physical detail."
        ),
    },
    {
        "mode": "vault",
        "frequency": "HUMAN",
        "name": "The Vault",
        "brief": (
            "A deeper thought for VOID memory. Something worth saving, not a lesson. "
            "It should help people remember what they already knew but stopped noticing."
        ),
    },
]


RUBRIC_SCHEDULE = [
    {
        "name": "Midnight",
        "voice": "void",
        "mode": "midnight",
        "frequency": "HUMAN",
        "hours": [0, 1, 2],
        "weight": 10,
        "brief": "Night-only VOID signal grounded in a specific room, task, route, sound, memory, encounter, or physical detail.",
    },
    {
        "name": "Frequency",
        "voice": "void",
        "mode": "frequency",
        "frequency": "HUMAN",
        "hours": [19, 20, 21, 22],
        "weight": 7,
        "brief": "Evening music/culture mood: sound, headphones, memory, motion, night city, track as atmosphere.",
    },
    {
        "name": "The Vault",
        "voice": "void",
        "mode": "vault",
        "frequency": "HUMAN",
        "hours": [22, 23],
        "weight": 4,
        "brief": "A deeper saved thought for the shared public. Quiet, precise, worth returning to.",
    },
    {
        "name": "Future File",
        "voice": "void",
        "mode": "future",
        "frequency": "FUTURE",
        "hours": [12, 13, 14, 15, 16, 17, 18],
        "weight": 5,
        "brief": "A non-hype future shift: interfaces, work, tools, cities, creators, behavior.",
    },
    {
        "name": "Observation",
        "voice": "void",
        "mode": "observation",
        "frequency": "ATTENTION",
        "hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
        "weight": 6,
        "brief": "A precise cultural observation grounded in one habit, place, object, ritual, craft, or encounter.",
    },
    {
        "name": "Signal",
        "voice": "void",
        "mode": "signal",
        "frequency": "HUMAN",
        "hours": [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
        "weight": 5,
        "brief": "A short original VOID observation grounded in one concrete scene, object, action, or encounter.",
    },
    {
        "name": "News Signal",
        "voice": "news",
        "mode": "news",
        "frequency": "AI",
        "hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
        "weight": 4,
        "brief": "Fresh real-world signal from sources, only when the feed has something worth catching.",
    },
]


TELEGRAM_VOID_SCHEDULE = [
    {
        "name": "Midnight",
        "voice": "void",
        "mode": "midnight",
        "frequency": "HUMAN",
        "hours": [0, 1, 2],
        "weight": 10,
        "brief": "Night-only VOID signal grounded in a specific room, task, route, sound, memory, encounter, or physical detail.",
    },
    {
        "name": "Frequency",
        "voice": "void",
        "mode": "frequency",
        "frequency": "HUMAN",
        "hours": [19, 20, 21, 22],
        "weight": 7,
        "brief": "Evening music/culture signal: headphones, mood, memory, sound, city rhythm.",
    },
    {
        "name": "The Vault",
        "voice": "void",
        "mode": "vault",
        "frequency": "HUMAN",
        "hours": [22, 23],
        "weight": 4,
        "brief": "A deeper thought for VOID memory. Quiet, saved, worth returning to.",
    },
    {
        "name": "Observation",
        "voice": "void",
        "mode": "observation",
        "frequency": "ATTENTION",
        "hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
        "weight": 6,
        "brief": "A precise observation grounded in one habit, place, object, ritual, craft, or encounter.",
    },
    {
        "name": "Future File",
        "voice": "void",
        "mode": "future",
        "frequency": "FUTURE",
        "hours": [12, 13, 14, 15, 16, 17, 18],
        "weight": 5,
        "brief": "Future shift without hype: tools, interfaces, work, cities, creators, behavior.",
    },
    {
        "name": "Signal",
        "voice": "void",
        "mode": "signal",
        "frequency": "HUMAN",
        "hours": [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
        "weight": 5,
        "brief": "Short original VOID observation grounded in one concrete scene, object, action, or encounter.",
    },
    {
        "name": "News Signal",
        "voice": "news",
        "mode": "news",
        "frequency": "AI",
        "hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
        "weight": 4,
        "brief": "Fresh real-world signal from sources, only when there is something worth catching.",
    },
]




def platform_context(platform: str = "telegram") -> str:
    channel = PLATFORM_CHANNELS.get(platform, PLATFORM_CHANNELS["telegram"])
    return (
        f"PLATFORM: {platform}\n"
        f"ROLE: {channel['role']}\n"
        f"FORMAT: {channel['format']}"
    )
