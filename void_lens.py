from __future__ import annotations

VOID_TOPICS = {
    "AI": {
        "keywords": [
            "ai", "artificial intelligence", "openai", "anthropic", "google deepmind",
            "agent", "agents", "automation", "model", "neural", "llm", "chatbot",
            "machine learning", "искусственный интеллект", "нейросеть", "ии"
        ],
        "weight": 4,
    },
    "ATTENTION": {
        "keywords": [
            "attention", "screen time", "feed", "social media", "algorithm", "recommendation",
            "tiktok", "instagram", "youtube", "doomscrolling", "digital wellbeing", "notification",
            "внимание", "лента", "алгоритм", "соцсети", "уведомление"
        ],
        "weight": 4,
    },
    "CONTROL": {
        "keywords": [
            "surveillance", "privacy", "tracking", "data", "policy", "ban", "regulation",
            "lawsuit", "antitrust", "moderation", "platform rules", "контроль", "приватность",
            "данные", "слежка", "регулирование"
        ],
        "weight": 3,
    },
    "HUMAN": {
        "keywords": [
            "work", "job", "workers", "mental health", "loneliness", "identity", "behavior",
            "education", "creator", "relationship", "человек", "люди", "работа", "одиночество",
            "поведение", "образование"
        ],
        "weight": 3,
    },
    "CULTURE": {
        "keywords": [
            "music", "film", "movie", "streaming", "artist", "creator", "media", "game",
            "gaming", "spotify", "netflix", "музыка", "кино", "игры", "культура", "стриминг"
        ],
        "weight": 2,
    },
    "FUTURE": {
        "keywords": [
            "future", "robot", "robots", "interface", "wearable", "device", "startup",
            "space", "biotech", "brain", "computer", "chip", "будущее", "робот", "интерфейс",
            "стартап", "устройство"
        ],
        "weight": 2,
    },
}

BONUS_TERMS = [
    "changes", "shift", "new feature", "launches", "tests", "rolls out", "study", "report",
    "warns", "risk", "impact", "влияет", "меняет", "исследование", "запускает", "тестирует"
]


def analyze_news_item(title: str, summary: str) -> dict:
    text = f"{title} {summary}".lower()

    topic_scores: dict[str, int] = {}
    matched: dict[str, list[str]] = {}

    for topic, meta in VOID_TOPICS.items():
        hits = [kw for kw in meta["keywords"] if kw.lower() in text]
        score = len(hits) * int(meta["weight"])
        topic_scores[topic] = score
        matched[topic] = hits[:8]

    best_frequency = max(topic_scores, key=topic_scores.get)
    score = topic_scores[best_frequency]

    bonus = sum(1 for term in BONUS_TERMS if term in text)
    score += bonus

    if score <= 0:
        best_frequency = "NOISE"

    return {
        "frequency": best_frequency,
        "score": int(score),
        "topic_scores": topic_scores,
        "matched_keywords": matched.get(best_frequency, []),
        "is_signal": score >= 2,
    }


def rubric_for_frequency(frequency: str) -> str:
    if frequency in {"AI", "ATTENTION", "CONTROL", "CULTURE", "FUTURE", "HUMAN"}:
        return f"SIGNAL / {frequency}"
    return "SIGNAL / NOISE"
