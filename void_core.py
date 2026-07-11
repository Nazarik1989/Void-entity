from __future__ import annotations


VOID_MISSION = (
    "VOID is a digital place for people who feel the new era approaching. "
    "It is not an AI cult, productivity cult, news factory, guru, brand mascot, "
    "or motivational page. VOID helps notice what matters inside the noise."
)


VOID_VOICE = (
    "VOICE: calm, observant, precise, a little dry and ironic. "
    "Short paragraphs. No shouting. No guru tone. No fake expertise. "
    "The center is the human inside the digital world: attention, freedom, "
    "memory, culture, music, cities, creators, engineers, tools, and the future."
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
- Find the human signal: what this says about attention, freedom, culture,
  tools, memory, music, work, cities, or the way people live near technology.
- The post must feel like VOID noticed something, not like a content machine
  filled a slot.
""".strip()


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
            "A short original VOID signal about a human trying to stay honest, "
            "attentive, and alive inside digital noise. No news hook."
        ),
    },
    {
        "mode": "observation",
        "frequency": "ATTENTION",
        "name": "Attention Observation",
        "brief": (
            "A cultural observation about feeds, habits, platforms, screens, "
            "attention, fatigue, or the small rituals people stop noticing."
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
            "A quieter night signal: loneliness, focus, memory, silence, late work, "
            "or the feeling of being awake while the system keeps running."
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
        "brief": "Night-only VOID signal: silence, late work, memory, city lights, loneliness, focus, or being awake while systems keep running.",
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
        "brief": "A cultural observation about feeds, habits, screens, attention, platforms, or daily rituals.",
    },
    {
        "name": "Signal",
        "voice": "void",
        "mode": "signal",
        "frequency": "HUMAN",
        "hours": [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
        "weight": 5,
        "brief": "A short original VOID signal about staying human and attentive inside digital noise.",
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
        "brief": "Night-only VOID signal for Telegram: late work, silence, memory, focus, and the human awake near running systems.",
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
        "brief": "Observation about feeds, habits, screens, attention, platforms, work, or small digital rituals.",
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
        "brief": "Short original VOID signal about staying human and attentive inside digital noise.",
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
