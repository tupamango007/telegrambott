"""Обработчики Telegram: бизнес-сообщения, оповещения и команды владельца."""

import asyncio
import html
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BusinessConnection,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .config import Config
from .replies import Reply, TemplateStore, choose_reply, is_important
from .storage import Connection, Storage

log = logging.getLogger(__name__)

CONTENT_LABELS = {
    "sticker": "[стикер]",
    "photo": "[фото]",
    "video": "[видео]",
    "voice": "[голосовое]",
    "video_note": "[кружок]",
    "audio": "[аудио]",
    "document": "[файл]",
    "animation": "[гифка]",
    "location": "[геолокация]",
    "contact": "[контакт]",
}
NOTIFY_PREVIEW_LIMIT = 1500
PAUSE_BUTTON_MINUTES = 60


@dataclass(frozen=True)
class Incoming:
    text: str  # текст или подпись — по нему подбираются шаблоны
    display: str  # как показать сообщение в оповещении
    is_sticker: bool


def message_text(message: Message) -> str:
    return message.text or message.caption or ""


def incoming_from_message(message: Message) -> Incoming:
    text = message_text(message)
    label = CONTENT_LABELS.get(message.content_type, "") if not message.text else ""
    display = " ".join(p for p in (label, text) if p) or f"[{message.content_type}]"
    return Incoming(text, display, message.sticker is not None)


def connection_from_telegram(bc: BusinessConnection) -> Connection:
    can_reply = bc.rights.can_reply if bc.rights is not None else bc.can_reply
    return Connection(
        id=bc.id,
        owner_id=bc.user.id,
        owner_chat_id=bc.user_chat_id,
        can_reply=bool(can_reply),
        is_enabled=bc.is_enabled,
    )


def chat_keyboard(chat_id: int, ignored: bool) -> InlineKeyboardMarkup:
    toggle = (
        InlineKeyboardButton(text="✅ Снова отвечать тут", callback_data=f"unignore:{chat_id}")
        if ignored
        else InlineKeyboardButton(text="🔕 Не отвечать тут", callback_data=f"ignore:{chat_id}")
    )
    pause = InlineKeyboardButton(text=f"⏸ Пауза {PAUSE_BUTTON_MINUTES} мин", callback_data=f"pause:{chat_id}")
    return InlineKeyboardMarkup(inline_keyboard=[[toggle, pause]])


def format_notification(
    sender: str, sender_id: int, items: list[Incoming], reply: Reply | None, status: str | None, important: bool
) -> str:
    who = f'<a href="tg://user?id={sender_id}">{html.escape(sender)}</a>'
    head = f"🚨 <b>Важное</b> от {who}" if important else f"💬 {who}"
    body = "\n".join(i.display for i in items)
    if len(body) > NOTIFY_PREVIEW_LIMIT:
        body = body[:NOTIFY_PREVIEW_LIMIT] + "…"
    lines = [head, html.escape(body), ""]
    if reply is not None:
        answer = " + ".join(p for p in (reply.text and html.escape(reply.text), reply.sticker and "[стикер]") if p)
        lines.append(f"🤖 Ответил: {answer}")
    elif status:
        lines.append(f"🤐 Не ответил: {status}")
    return "\n".join(lines).strip()


def create_router(config: Config, storage: Storage, templates: TemplateStore) -> Router:
    router = Router()
    pending: dict[int, asyncio.Task] = {}
    # Сообщения собеседника, пришедшие за время ожидания перед ответом.
    unanswered: dict[int, list[Incoming]] = {}

    async def get_connection(bot: Bot, connection_id: str) -> Connection:
        conn = storage.get_connection(connection_id)
        if conn is None:
            conn = connection_from_telegram(await bot.get_business_connection(connection_id))
            storage.save_connection(conn)
        return conn

    def why_not_reply(chat_id: int, conn: Connection) -> str | None:
        """Причина, по которой бот не отвечает в чате, или None, если отвечать можно."""
        if not conn.is_enabled or not conn.can_reply:
            return "у бота нет права отвечать (Telegram Business)"
        if not storage.is_enabled():
            return "автоответы выключены (/on)"
        if storage.is_ignored(chat_id):
            return "чат в списке «не отвечать»"
        if storage.is_paused(chat_id):
            return "пауза в этом чате"
        return None

    async def send_reply(bot: Bot, chat_id: int, conn: Connection, reply: Reply) -> bool:
        """Отправляет ответ «по-человечески»: сначала статус набора, потом сообщение."""
        if reply.text:
            await bot.send_chat_action(chat_id, "typing", business_connection_id=conn.id)
            await asyncio.sleep(min(1 + len(reply.text) / 25, 4))
            # Пока бот «печатал», владелец мог ответить сам.
            if why_not_reply(chat_id, conn):
                return False
            await bot.send_message(chat_id, reply.text, business_connection_id=conn.id)
        if reply.sticker:
            await bot.send_chat_action(chat_id, "choose_sticker", business_connection_id=conn.id)
            await asyncio.sleep(1)
            if why_not_reply(chat_id, conn):
                return bool(reply.text)
            await bot.send_sticker(chat_id, reply.sticker, business_connection_id=conn.id)
        return True

    async def process_later(bot: Bot, message: Message, conn: Connection) -> None:
        chat_id = message.chat.id
        # Ждём: собеседник может дописать ещё сообщения или владелец ответит сам.
        await asyncio.sleep(config.reply_delay_seconds)
        items = unanswered.pop(chat_id, [])
        if not items or storage.is_paused(chat_id):
            return

        tpl = templates.get()
        text = "\n".join(i.text for i in items if not i.is_sticker)
        only_sticker = all(i.is_sticker for i in items)
        important = is_important(tpl, text)
        name = message.from_user.first_name if message.from_user else ""

        reply: Reply | None = None
        status = why_not_reply(chat_id, conn)
        if status is None:
            reply = choose_reply(tpl, text, name, datetime.now(timezone.utc), only_sticker)
            if reply is None:
                status = "ответ не нужен"
            elif storage.sent_recently(chat_id, reply.key, config.reply_cooldown_minutes * 60):
                reply, status = None, "уже отвечал недавно"
            elif await send_reply(bot, chat_id, conn, reply):
                storage.mark_sent(chat_id, reply.key)
                log.info("Ответил в чат %s шаблоном %s", chat_id, reply.key)
            else:
                return  # владелец подключился к разговору

        mode = config.notify_mode
        if mode == "off" or storage.is_paused(chat_id):
            return
        if not (important or mode == "all" or (mode == "replies" and reply is not None)):
            return
        await bot.send_message(
            conn.owner_chat_id,
            format_notification(
                message.chat.full_name or str(chat_id), chat_id, items, reply, status, important
            ),
            parse_mode="HTML",
            reply_markup=chat_keyboard(chat_id, storage.is_ignored(chat_id)),
            disable_web_page_preview=True,
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
        # Наши собственные ответы приходят обратно как бизнес-сообщения.
        if message.sender_business_bot is not None:
            return

        conn = await get_connection(bot, message.business_connection_id)
        chat_id = message.chat.id

        if message.from_user.id == conn.owner_id:
            # Владелец ответил сам — даём ему вести разговор.
            unanswered.pop(chat_id, None)
            storage.pause_chat(chat_id, config.owner_takeover_minutes * 60)
            if task := pending.pop(chat_id, None):
                task.cancel()
            return

        if message.from_user.is_bot or storage.is_paused(chat_id):
            return

        unanswered.setdefault(chat_id, []).append(incoming_from_message(message))
        if task := pending.pop(chat_id, None):
            task.cancel()
        task = asyncio.create_task(process_later(bot, message, conn))
        pending[chat_id] = task
        task.add_done_callback(lambda t: pending.pop(chat_id, None) if pending.get(chat_id) is t else None)

    # --- кнопки под оповещениями ---

    @router.callback_query(F.data.regexp(r"^(ignore|unignore|pause):-?\d+$"))
    async def on_chat_button(callback: CallbackQuery) -> None:
        if not storage.is_owner(callback.from_user.id):
            await callback.answer()
            return
        action, raw_id = callback.data.split(":")
        chat_id = int(raw_id)
        if action == "pause":
            storage.pause_chat(chat_id, PAUSE_BUTTON_MINUTES * 60)
            answer = f"Бот молчит в этом чате {PAUSE_BUTTON_MINUTES} мин"
        else:
            storage.set_ignored(chat_id, action == "ignore")
            answer = "Бот больше не отвечает в этом чате" if action == "ignore" else "Бот снова отвечает в этом чате"
        if isinstance(callback.message, Message):
            await callback.message.edit_reply_markup(reply_markup=chat_keyboard(chat_id, storage.is_ignored(chat_id)))
        await callback.answer(answer)

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
            "Пришлите мне стикер — я покажу его ID для replies.toml.\n\n"
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
        await message.answer(
            f"Автоответы {state}.\nОповещения: {config.notify_mode}.\nШаблоны: {config.replies_file}."
        )

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

    @router.message(private_chat, F.sticker)
    async def on_owner_sticker(message: Message) -> None:
        if not storage.is_owner(message.from_user.id):
            return
        file_id = message.sticker.file_id
        await message.answer(
            f"ID стикера:\n<code>{file_id}</code>\n\n"
            "Добавьте его в replies.toml, например:\n"
            f'<pre>[on_sticker]\nstickers = ["{file_id}"]</pre>',
            parse_mode="HTML",
        )

    return router
