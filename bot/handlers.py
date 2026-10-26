"""Обработчики Telegram: бизнес-сообщения, оповещения и команды владельца."""

import asyncio
import html
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BusinessConnection,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .config import Config
from .replies import Reply, TemplateStore, choose_followup, choose_reply, is_important
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

# «Человеческий» ритм ответа
TYPING_CHARS_PER_SECOND = (4.0, 7.0)  # скорость набора на телефоне
MAX_TYPING_SECONDS = 7.0
READING_PAUSE_SECONDS = (1.0, 3.0)  # прочитал — подумал — начал печатать
BETWEEN_MESSAGES_SECONDS = (0.5, 1.5)


@dataclass(frozen=True)
class Incoming:
    text: str  # текст или подпись — по нему подбираются шаблоны
    display: str  # как показать сообщение в оповещении
    is_sticker: bool
    message_id: int = 0


def message_text(message: Message) -> str:
    return message.text or message.caption or ""


def incoming_from_message(message: Message) -> Incoming:
    text = message_text(message)
    label = CONTENT_LABELS.get(message.content_type, "") if not message.text else ""
    display = " ".join(p for p in (label, text) if p) or f"[{message.content_type}]"
    return Incoming(text, display, message.sticker is not None, message.message_id)


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
        texts = " / ".join(html.escape(m) for m in reply.messages)
        answer = " + ".join(p for p in (texts, reply.sticker and "[стикер]") if p)
        lines.append(f"🤖 Ответил: {answer}")
    elif status:
        lines.append(f"🤐 Не ответил: {status}")
    return "\n".join(lines).strip()


def create_router(config: Config, storage: Storage, templates: TemplateStore) -> Router:
    router = Router()
    pending: dict[int, asyncio.Task] = {}
    # Сообщения собеседника, пришедшие за время ожидания перед ответом.
    unanswered: dict[int, list[Incoming]] = {}
    # Последний отправленный вариант в каждом чате — чтобы не повторяться подряд.
    last_variant: dict[int, str] = {}

    def is_owner(user_id: int) -> bool:
        return user_id == config.owner_id or storage.is_owner(user_id)

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

    async def show_action(bot: Bot, chat_id: int, conn: Connection, action: str, seconds: float) -> None:
        # Статус «печатает…» в Telegram гаснет через ~5 секунд, поэтому обновляем его.
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while True:
            await bot.send_chat_action(chat_id, action, business_connection_id=conn.id)
            left = end - loop.time()
            if left <= 4.0:
                await asyncio.sleep(max(left, 0))
                return
            await asyncio.sleep(4.0)

    async def send_reply(bot: Bot, chat_id: int, conn: Connection, reply: Reply, read_message_id: int) -> bool:
        """Отвечает как человек: прочитал, подумал, напечатал. Возвращает, ушло ли хоть что-то."""
        if config.mark_as_read and read_message_id:
            try:
                await bot.read_business_message(conn.id, chat_id, read_message_id)
            except TelegramAPIError as e:
                log.debug("Не удалось отметить прочитанным: %s", e)
        await asyncio.sleep(random.uniform(*READING_PAUSE_SECONDS))

        sent = False
        for part in reply.messages:
            # Владелец мог подключиться к разговору в любой момент.
            if why_not_reply(chat_id, conn):
                return sent
            if sent:
                await asyncio.sleep(random.uniform(*BETWEEN_MESSAGES_SECONDS))
            typing_time = min(1 + len(part) / random.uniform(*TYPING_CHARS_PER_SECOND), MAX_TYPING_SECONDS)
            await show_action(bot, chat_id, conn, "typing", typing_time)
            if why_not_reply(chat_id, conn):
                return sent
            await bot.send_message(chat_id, part, business_connection_id=conn.id)
            sent = True
        if reply.sticker:
            if why_not_reply(chat_id, conn):
                return sent
            await show_action(bot, chat_id, conn, "choose_sticker", random.uniform(1.0, 2.5))
            if why_not_reply(chat_id, conn):
                return sent
            await bot.send_sticker(chat_id, reply.sticker, business_connection_id=conn.id)
            sent = True
        return sent

    async def process_later(bot: Bot, message: Message, conn: Connection) -> None:
        chat_id = message.chat.id
        # Ждём: собеседник может дописать ещё сообщения или владелец ответит сам.
        # Задержка немного «плавает», чтобы ответ не приходил секунда в секунду.
        await asyncio.sleep(config.reply_delay_seconds * random.uniform(0.7, 1.5))
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
            now = datetime.now(timezone.utc)
            cooldown = config.reply_cooldown_minutes * 60
            reply = choose_reply(tpl, text, name, now, only_sticker, avoid=last_variant.get(chat_id))
            if reply is not None and storage.sent_recently(chat_id, reply.key, cooldown):
                # Собеседник пишет снова — один раз отвечаем уточнением («я тут, просто занят»),
                # дальше молчим, чтобы не превращаться в спам.
                followup = None
                if reply.key != "sticker" and not storage.sent_recently(chat_id, "followup", cooldown):
                    followup = choose_followup(tpl, name, now, avoid=last_variant.get(chat_id))
                reply = followup
                status = None if followup else "уже отвечал недавно"
            elif reply is None:
                status = "ответ не нужен"

            if reply is not None:
                if not await send_reply(bot, chat_id, conn, reply, items[-1].message_id):
                    return  # владелец подключился к разговору
                storage.mark_sent(chat_id, reply.key)
                if reply.variant:
                    last_variant[chat_id] = reply.variant
                log.info("Ответил в чат %s шаблоном %s", chat_id, reply.key)

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
        if not is_owner(callback.from_user.id):
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
        if not is_owner(message.from_user.id):
            await message.answer(
                "Привет! Этот бот отвечает на сообщения за своего владельца.\n"
                "Чтобы подключить его к своему аккаунту: Настройки → Telegram Business → Чат-боты.\n\n"
                f"Ваш Telegram ID: <code>{message.from_user.id}</code> — если это ваш бот, "
                "укажите его в переменной OWNER_ID.",
                parse_mode="HTML",
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
        if not is_owner(message.from_user.id):
            return
        storage.set_enabled(command.command == "on")
        await message.answer("✅ Автоответы включены" if storage.is_enabled() else "⏸ Автоответы выключены")

    @router.message(private_chat, Command("status"))
    async def cmd_status(message: Message) -> None:
        if not is_owner(message.from_user.id):
            return
        state = "включены" if storage.is_enabled() else "выключены"
        await message.answer(
            f"Автоответы {state}.\nОповещения: {config.notify_mode}.\nШаблоны: {config.replies_file}."
        )

    @router.message(private_chat, Command("ignore", "unignore"))
    async def cmd_ignore(message: Message, command: CommandObject) -> None:
        if not is_owner(message.from_user.id):
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
    async def on_sticker_in_private(message: Message) -> None:
        # ID стикера не секрет — показываем всем, чтобы не зависеть от того, узнал ли бот владельца.
        file_id = message.sticker.file_id
        await message.answer(
            f"ID стикера:\n<code>{file_id}</code>\n\n"
            "Добавьте его в replies.toml, например:\n"
            f'<pre>[on_sticker]\nstickers = ["{file_id}"]</pre>',
            parse_mode="HTML",
        )

    return router
