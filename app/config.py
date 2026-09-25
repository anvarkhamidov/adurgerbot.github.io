import os
from dataclasses import dataclass
from pathlib import Path

# Telegram Bot API limits for uploads made by bots.
CLOUD_API_LIMIT_MB = 50
LOCAL_API_LIMIT_MB = 2000


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _ids(name: str) -> frozenset[int]:
    raw = os.getenv(name, "")
    return frozenset(int(part) for part in raw.replace(" ", "").split(",") if part)


@dataclass(frozen=True)
class Config:
    bot_token: str
    bot_api_url: str
    allowed_users: frozenset[int]
    download_dir: Path
    cookies_file: Path | None
    max_concurrent: int
    download_attempts: int
    max_file_bytes: int
    stale_hours: int

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise SystemExit("BOT_TOKEN is not set")

        api_url = os.getenv("BOT_API_URL", "").strip().rstrip("/")
        default_limit = LOCAL_API_LIMIT_MB if api_url else CLOUD_API_LIMIT_MB

        cookies = Path(os.getenv("COOKIES_FILE", "/data/cookies.txt"))

        return cls(
            bot_token=token,
            bot_api_url=api_url,
            allowed_users=_ids("ALLOWED_USERS"),
            download_dir=Path(os.getenv("DOWNLOAD_DIR", "/data/downloads")),
            cookies_file=cookies if cookies.is_file() else None,
            max_concurrent=max(1, _int("MAX_CONCURRENT", 2)),
            download_attempts=max(1, _int("DOWNLOAD_ATTEMPTS", 5)),
            max_file_bytes=_int("MAX_FILE_MB", default_limit) * 1024 * 1024,
            stale_hours=max(1, _int("STALE_HOURS", 24)),
        )
