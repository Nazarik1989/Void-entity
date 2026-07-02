import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    channel_id: str
    admin_id: int
    db_path: str = "void.db"
    openai_api_key: str | None = None
    openai_model: str = "openai/gpt-5.4"
    openai_image_model: str = "gpt-image-1"
    openai_image_size: str = "1024x1024"
    openai_image_quality: str = "medium"
    scan_limit_per_source: int = 8
    max_scan_results: int = 12


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def get_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    channel_id = os.getenv("CHANNEL_ID", "").strip()
    admin_id = _get_int("ADMIN_ID", 0)

    return Settings(
        bot_token=bot_token,
        channel_id=channel_id,
        admin_id=admin_id,
        db_path=os.getenv("DB_PATH", "void.db"),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "openai/gpt-5.4"),
        openai_image_model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1"),
        openai_image_size=os.getenv("OPENAI_IMAGE_SIZE", "1024x1024"),
        openai_image_quality=os.getenv("OPENAI_IMAGE_QUALITY", "medium"),
        scan_limit_per_source=_get_int("SCAN_LIMIT_PER_SOURCE", 8),
        max_scan_results=_get_int("MAX_SCAN_RESULTS", 12),
    )


settings = get_settings()
