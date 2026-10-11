"""Обработчики Telegram: бизнес-сообщения и команды владельца."""

import asyncio
import html
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BusinessConnection, Message

from .ai import Responder
from .config import Config
from .storage import Connection, Storage

log = logging.getLogger(__name__)

CONTENT_LABELS = {
    "sticker": "[стикер]",
    "photo": "[фото]",
    "video": "[видео]",
    "voice": "[голосовое сообщение]",
    "video_note": "[видеосообщение]",
    "audio": "[аудио]",
    "document": "[файл]",
    "animation": "[гифка]",
    "location": "[геолокация]",
    "contact": "[контакт]",
}


def message_text(message: Message) -> str:
    body = message.text or message.caption or ""
    label = CONTENT_LABELS.get(message.content_type, "") if not message.text else ""
    return " ".join(p for p in (label, body) if p) or f"[{message.content_type}]"


def connection_from_telegram(bc: BusinessConnection) -> Connection:
    can_reply = bc.rights.can_reply if bc.rights is not None else bc.can_reply
    return Connection(
        id=bc.id,
        owner_id=bc.user.id,
        owner_chat_id=bc.user_chat_id,
        can_reply=bool(can_reply),
        is_enabled=bc.is_enabled,
    )


def create_router(config: Config, storage: Storage, responder: Responder) -> Router:
    router = Router()
    pending: dict[int, asyncio.Task] = {}

    async def get_connection(bot: Bot, connection_id: str) -> Connection:
        conn = storage.get_connection(connection_id)
        if conn is None:
            conn = connection_from_telegram(await bot.get_business_connection(connection_id))
            storage.save_connection(conn)
        return conn

    def should_reply(chat_id: int, conn: Connection) -> bool:
        return (
            conn.is_enabled
            and conn.can_reply
            and storage.is_enabled()
            and not storage.is_ignored(chat_id)
            and not storage.is_paused(chat_id)
        )

    async def reply_later(bot: Bot, message: Message, conn: Connection) -> None:
        chat_id = message.chat.id
        # Ждём: собеседник может дописать ещё сообщения или владелец ответит сам.
        await asyncio.sleep(config.reply_delay_seconds)
        if not should_reply(chat_id, conn):
            return

        await bot.send_chat_action(chat_id, "typing", business_connection_id=conn.id)
        text = await responder.reply(storage.history(chat_id, config.history_limit))
        if text is None or not should_reply(chat_id, conn):
            return

        await bot.send_message(chat_id, text, business_connection_id=conn.id)
        storage.add_message(chat_id, "assistant", text)
        log.info("Ответил в чат %s", chat_id)

        if config.notify_owner:
            who = html.escape(message.chat.full_name or str(chat_id))
            await bot.send_message(
                conn.owner_chat_id,
                f"🤖 Ответил за вас в чате <b>{who}</b> (<code>{chat_id}</code>):\n\n{html.escape(text)}",
                parse_mode="HTML",
            )

    @router.business_connection()
    async def on_business_connection(bc: BusinessConnection, bot: Bot) -> None:
        conn = connection_from_telegram(bc)
        storage.save_connection(conn)
        if not conn.is_enabled:
            status = "Бот отключён от вашего аккаунта."
        elif conn.can_reply:
            status = "Бот подключён и будет отвечать на сообщения за вас. /help — команды."
        else:
            status = "Бот подключён, но без права отвечать. Включите «Отвечать на сообщения» в настройках Telegram Business."
        await bot.send_message(conn.owner_chat_id, status)

    @router.business_message()
    async def on_business_message(message: Message, bot: Bot) -> None:
        if message.business_connection_id is None or message.from_user is None:
            return
        # Наши собственные ответы приходят обратно как бизнес-сообщения — они уже в истории.
        if message.sender_business_bot is not None:
            return

        conn = await get_connection(bot, message.business_connection_id)
        chat_id = message.chat.id
        text = message_text(message)

        if message.from_user.id == conn.owner_id:
            # Владелец ответил сам — запоминаем и даём ему вести разговор.
            storage.add_message(chat_id, "assistant", text)
            storage.pause_chat(chat_id, config.owner_takeover_minutes * 60)
            if task := pending.pop(chat_id, None):
                task.cancel()
            return

        if message.from_user.is_bot:
            return

        storage.add_message(chat_id, "user", text)
        if not should_reply(chat_id, conn):
            return

        if task := pending.pop(chat_id, None):
            task.cancel()
        task = asyncio.create_task(reply_later(bot, message, conn))
        pending[chat_id] = task
        task.add_done_callback(lambda t: pending.pop(chat_id, None) if pending.get(chat_id) is t else None)

    # --- команды владельца в личном чате с ботом ---

    private_chat = F.chat.type == "private"

    @router.message(private_chat, Command("start", "help"))
    async def cmd_help(message: Message) -> None:
        if not storage.is_owner(message.from_user.id):
            await message.answer(
                "Привет! Этот бот отвечает на сообщения за своего владельца.\n"
                "Чтобы подключить его к своему аккаунту: Настройки → Telegram Business → Чат-боты."
            )
            return
        await message.answer(
            "Команды:\n"
            "/on — включить автоответы\n"
            "/off — выключить автоответы\n"
            "/status — текущее состояние\n"
            "/ignore <code>chat_id</code> — не отвечать в этом чате\n"
            "/unignore <code>chat_id</code> — снова отвечать в этом чате\n\n"
            "Если вы сами напишете в чате, бот замолчит там на "
            f"{config.owner_takeover_minutes} мин.",
            parse_mode="HTML",
        )

    @router.message(private_chat, Command("on", "off"))
    async def cmd_toggle(message: Message, command: CommandObject) -> None:
        if not storage.is_owner(message.from_user.id):
            return
        storage.set_enabled(command.command == "on")
        await message.answer("✅ Автоответы включены" if storage.is_enabled() else "⏸ Автоответы выключены")

    @router.message(private_chat, Command("status"))
    async def cmd_status(message: Message) -> None:
        if not storage.is_owner(message.from_user.id):
            return
        state = "включены" if storage.is_enabled() else "выключены"
        await message.answer(f"Автоответы {state}. Модель: {config.claude_model}.")

    @router.message(private_chat, Command("ignore", "unignore"))
    async def cmd_ignore(message: Message, command: CommandObject) -> None:
        if not storage.is_owner(message.from_user.id):
            return
        try:
            chat_id = int((command.args or "").strip())
        except ValueError:
            await message.answer(f"Укажите id чата: /{command.command} 123456789")
            return
        ignored = command.command == "ignore"
        storage.set_ignored(chat_id, ignored)
        await message.answer(f"Чат {chat_id}: " + ("бот не отвечает" if ignored else "бот снова отвечает"))

    return router
