import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


NOTIFY_MODES = {"all", "replies", "important", "off"}


def _notify_mode(value: str | None) -> str:
    value = (value or "all").strip().lower()
    # Старые значения true/false из прошлых версий .env
    if value in {"1", "true", "yes", "on", "да"}:
        return "all"
    if value in {"0", "false", "no", "нет"}:
        return "off"
    if value not in NOTIFY_MODES:
        raise RuntimeError(f"NOTIFY_OWNER должен быть одним из: {', '.join(sorted(NOTIFY_MODES))}")
    return value


@dataclass(frozen=True)
class Config:
    telegram_token: str
    replies_file: Path
    db_path: Path
    reply_delay_seconds: float
    owner_takeover_minutes: int
    reply_cooldown_minutes: int
    notify_mode: str  # all | replies | important | off

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN (см. .env.example)")
        return cls(
            telegram_token=token,
            replies_file=Path(os.getenv("REPLIES_FILE", "replies.toml")),
            db_path=Path(os.getenv("DB_PATH", "data/bot.sqlite3")),
            reply_delay_seconds=float(os.getenv("REPLY_DELAY_SECONDS", "8")),
            owner_takeover_minutes=int(os.getenv("OWNER_TAKEOVER_MINUTES", "30")),
            reply_cooldown_minutes=int(os.getenv("REPLY_COOLDOWN_MINUTES", "60")),
            notify_mode=_notify_mode(os.getenv("NOTIFY_OWNER")),
        )
