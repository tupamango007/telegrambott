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
class Section:
    """Набор вариантов ответа: текст и/или стикеры."""

    replies: list[str] = field(default_factory=list)
    stickers: list[str] = field(default_factory=list)

    @classmethod
    def from_value(cls, value) -> "Section":
        # Поддерживаем краткую форму `default = ["текст", ...]` и таблицу с replies/stickers.
        if isinstance(value, dict):
            return cls(_as_list(value.get("replies")), _as_list(value.get("stickers")))
        return cls(_as_list(value))

    def __bool__(self) -> bool:
        return bool(self.replies or self.stickers)


@dataclass(frozen=True)
class Rule:
    keywords: list[str]
    section: Section
    notify: bool = False


@dataclass(frozen=True)
class Templates:
    default: Section = field(default_factory=Section)
    skip: set[str] = field(default_factory=set)
    rules: list[Rule] = field(default_factory=list)
    night: Section = field(default_factory=Section)
    night_start: int | None = None
    night_end: int | None = None
    on_sticker: Section = field(default_factory=Section)
    sticker_chance: float = 0.5
    timezone: ZoneInfo | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "Templates":
        night = data.get("night") or {}
        tz = data.get("timezone")
        return cls(
            default=Section.from_value(data.get("default")),
            skip={normalize(s) for s in data.get("skip", [])},
            rules=[
                Rule(
                    [normalize(k) for k in _as_list(r.get("keywords"))],
                    Section.from_value(r),
                    bool(r.get("notify", False)),
                )
                for r in data.get("rules", [])
            ],
            night=Section.from_value(night),
            night_start=night.get("start"),
            night_end=night.get("end"),
            on_sticker=Section.from_value(data.get("on_sticker") or {}),
            sticker_chance=float(data.get("sticker_chance", 0.5)),
            timezone=ZoneInfo(tz) if tz else None,
        )


@dataclass(frozen=True)
class Reply:
    key: str  # какой шаблон сработал: "default", "night", "sticker" или "rule:N"
    text: str | None = None
    sticker: str | None = None


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


def match_rule(templates: Templates, text: str) -> tuple[int, Rule] | None:
    norm = normalize(text)
    if not norm:
        return None
    for i, rule in enumerate(templates.rules):
        if any(k and k in norm for k in rule.keywords):
            return i, rule
    return None


def is_important(templates: Templates, text: str) -> bool:
    """Сработало ли правило с notify = true — о таком сообщении владелец узнаёт громко."""
    match = match_rule(templates, text)
    return match is not None and match[1].notify


def _build(key: str, section: Section, name: str, sticker_chance: float) -> Reply | None:
    text = random.choice(section.replies).replace("{name}", name).strip() if section.replies else ""
    sticker = None
    if section.stickers and (not text or random.random() < sticker_chance):
        sticker = random.choice(section.stickers)
    if not text and not sticker:
        return None
    return Reply(key, text or None, sticker)


def choose_reply(
    templates: Templates, text: str, name: str, now: datetime, only_sticker: bool = False
) -> Reply | None:
    """Подбирает ответ на сообщение или возвращает None, если отвечать не нужно.

    only_sticker — собеседник прислал только стикер(ы), без текста.
    """
    if only_sticker:
        return _build("sticker", templates.on_sticker, name, templates.sticker_chance)

    norm = normalize(text)
    if norm and norm in templates.skip:
        return None

    if match := match_rule(templates, text):
        i, rule = match
        return _build(f"rule:{i}", rule.section, name, templates.sticker_chance)

    if templates.timezone is not None:
        now = now.astimezone(templates.timezone)
    if templates.night and is_night(now.hour, templates.night_start, templates.night_end):
        return _build("night", templates.night, name, templates.sticker_chance)
    return _build("default", templates.default, name, templates.sticker_chance)


class TemplateStore:
    """Читает replies.toml при каждом обращении; при ошибке в файле оставляет прошлую версию."""

    def __init__(self, path: Path, fallback: Path | None = Path("replies.example.toml")):
        self._path = path
        self._fallback = fallback
        self._templates = Templates()

    def get(self) -> Templates:
        path = self._path
        if not path.exists() and self._fallback is not None and self._fallback.exists():
            # Например, на хостинге, куда залит только репозиторий: берём шаблоны из примера.
            path = self._fallback
        try:
            with path.open("rb") as f:
                self._templates = Templates.from_dict(tomllib.load(f))
        except FileNotFoundError:
            log.warning("Файл шаблонов %s не найден", path)
        except (tomllib.TOMLDecodeError, ValueError, TypeError, AttributeError, KeyError) as e:
            log.error("Ошибка в %s, использую прошлую версию: %s", path, e)
        return self._templates
