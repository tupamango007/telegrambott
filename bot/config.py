import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}


@dataclass(frozen=True)
class Config:
    telegram_token: str
    claude_model: str
    claude_effort: str
    persona_file: Path
    db_path: Path
    reply_delay_seconds: float
    owner_takeover_minutes: int
    history_limit: int
    notify_owner: bool

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN (см. .env.example)")
        return cls(
            telegram_token=token,
            claude_model=os.getenv("CLAUDE_MODEL", "claude-opus-5-5"),
            claude_effort=os.getenv("CLAUDE_EFFORT", "low"),
            persona_file=Path(os.getenv("PERSONA_FILE", "persona.md")),
            db_path=Path(os.getenv("DB_PATH", "data/bot.sqlite3")),
            reply_delay_seconds=float(os.getenv("REPLY_DELAY_SECONDS", "8")),
            owner_takeover_minutes=int(os.getenv("OWNER_TAKEOVER_MINUTES", "30")),
            history_limit=int(os.getenv("HISTORY_LIMIT", "40")),
            notify_owner=_bool(os.getenv("NOTIFY_OWNER"), True),
        )
