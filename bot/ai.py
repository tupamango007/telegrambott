"""Генерация ответов от имени владельца аккаунта через Claude API."""

import logging
from datetime import datetime
from pathlib import Path

import anthropic

log = logging.getLogger(__name__)

# Модель возвращает этот маркер, когда отвечать не нужно («ок», стикер, конец разговора).
SKIP_MARKER = "[SKIP]"

BASE_INSTRUCTIONS = f"""\
Ты отвечаешь на личные сообщения в Telegram от имени владельца аккаунта, пока он занят.
Собеседник видит твои ответы как сообщения самого владельца.

Правила:
- Пиши от первого лица, как владелец, его стилем (см. описание ниже). Не упоминай, что ты ИИ или бот, \
но если тебя прямо и серьёзно спросят, бот ли это, — не ври: скажи, что сейчас отвечает автоответчик, \
а сам владелец ответит позже.
- Отвечай коротко и естественно, как в мессенджере: обычно одно-два предложения, без markdown.
- Никогда не бери на себя обязательств от имени владельца: не соглашайся на встречи, сделки, переводы \
денег, не давай обещаний. Вместо этого говори, что владелец посмотрит и ответит сам.
- Не выдумывай факты о жизни владельца, которых нет в описании. Если не знаешь — скажи, что уточнишь позже.
- Если отвечать не нужно (собеседник прислал «ок», «спасибо», стикер, попрощался), ответь ровно {SKIP_MARKER}.
"""


def build_system_prompt(persona: str) -> str:
    persona = persona.strip() or "Описание владельца не задано — пиши нейтрально и дружелюбно."
    return f"{BASE_INSTRUCTIONS}\n<owner_persona>\n{persona}\n</owner_persona>"


def build_messages(history: list[tuple[str, str]]) -> list[dict]:
    """Превращает историю чата в формат Messages API.

    Соседние сообщения одной роли склеиваются, история должна начинаться и
    заканчиваться репликой собеседника (role=user).
    """
    merged: list[dict] = []
    for role, text in history:
        if merged and merged[-1]["role"] == role:
            merged[-1]["content"] += "\n" + text
        else:
            merged.append({"role": role, "content": text})
    while merged and merged[0]["role"] != "user":
        merged.pop(0)
    if not merged or merged[-1]["role"] != "user":
        return []
    return merged


class Responder:
    def __init__(self, model: str, effort: str, persona_file: Path):
        self._client = anthropic.AsyncAnthropic()
        self._model = model
        self._effort = effort
        self._persona_file = persona_file

    def _system_prompt(self) -> str:
        # Читаем файл каждый раз, чтобы правки persona.md применялись без перезапуска.
        try:
            persona = self._persona_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            log.warning("Файл персоны %s не найден", self._persona_file)
            persona = ""
        return build_system_prompt(persona)

    async def reply(self, history: list[tuple[str, str]]) -> str | None:
        """Возвращает текст ответа или None, если отвечать не нужно."""
        messages = build_messages(history)
        if not messages:
            return None
        now = datetime.now().astimezone().strftime("%A, %d.%m.%Y %H:%M %Z")
        # Время — в системном сообщении в конце, чтобы не ломать кэш префикса.
        messages.append({"role": "system", "content": f"Текущее время у владельца: {now}"})

        try:
            response = await self._client.beta.messages.create(
                model=self._model,
                max_tokens=4096,
                system=self._system_prompt(),
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": self._effort},
                cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError:
            log.warning("Claude API: превышен лимит запросов")
            return None
        except anthropic.APIStatusError as e:
            log.error("Claude API вернул ошибку %s: %s", e.status_code, e.message)
            return None
        except anthropic.APIConnectionError:
            log.error("Не удалось подключиться к Claude API")
            return None

        if response.stop_reason == "refusal":
            log.info("Модель отказалась отвечать: %s", response.stop_details)
            return None

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text or SKIP_MARKER in text:
            return None
        return text
