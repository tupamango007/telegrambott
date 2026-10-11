"""Выбор автоответа по шаблонам из replies.toml."""

import logging
import random
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rule:
    keywords: list[str]
    replies: list[str]


@dataclass(frozen=True)
class Templates:
    default: list[str]
    skip: set[str] = field(default_factory=set)
    rules: list[Rule] = field(default_factory=list)
    night_start: int | None = None
    night_end: int | None = None
    night_replies: list[str] = field(default_factory=list)
    timezone: ZoneInfo | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "Templates":
        night = data.get("night") or {}
        tz = data.get("timezone")
        return cls(
            default=_as_list(data.get("default")),
            skip={normalize(s) for s in data.get("skip", [])},
            rules=[
                Rule([normalize(k) for k in _as_list(r.get("keywords"))], _as_list(r.get("replies")))
                for r in data.get("rules", [])
            ],
            night_start=night.get("start"),
            night_end=night.get("end"),
            night_replies=_as_list(night.get("replies")),
            timezone=ZoneInfo(tz) if tz else None,
        )


@dataclass(frozen=True)
class Reply:
    key: str  # какой шаблон сработал: "default", "night" или "rule:N"
    text: str


def _as_list(value) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s+👍]", " ", text)
    return " ".join(text.split())


def is_night(hour: int, start: int | None, end: int | None) -> bool:
    if start is None or end is None:
        return False
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def choose_reply(templates: Templates, text: str, name: str, now: datetime) -> Reply | None:
    """Подбирает ответ на сообщение или возвращает None, если отвечать не нужно."""
    norm = normalize(text)
    if norm and norm in templates.skip:
        return None

    key, variants = "default", templates.default
    for i, rule in enumerate(templates.rules):
        if norm and any(k and k in norm for k in rule.keywords):
            key, variants = f"rule:{i}", rule.replies
            break
    else:
        if templates.timezone is not None:
            now = now.astimezone(templates.timezone)
        if templates.night_replies and is_night(now.hour, templates.night_start, templates.night_end):
            key, variants = "night", templates.night_replies

    if not variants:
        return None
    reply = random.choice(variants).replace("{name}", name).strip()
    return Reply(key, reply) if reply else None


class TemplateStore:
    """Читает replies.toml при каждом обращении; при ошибке в файле оставляет прошлую версию."""

    def __init__(self, path: Path):
        self._path = path
        self._templates = Templates(default=[])

    def get(self) -> Templates:
        try:
            with self._path.open("rb") as f:
                self._templates = Templates.from_dict(tomllib.load(f))
        except FileNotFoundError:
            log.warning("Файл шаблонов %s не найден", self._path)
        except (tomllib.TOMLDecodeError, ValueError, TypeError, AttributeError, KeyError) as e:
            log.error("Ошибка в %s, использую прошлую версию: %s", self._path, e)
        return self._templates
