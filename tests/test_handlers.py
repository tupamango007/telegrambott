from datetime import datetime

from aiogram.types import BusinessBotRights, BusinessConnection, Chat, Message, PhotoSize, User

from bot.handlers import (
    Incoming,
    chat_keyboard,
    connection_from_telegram,
    format_notification,
    incoming_from_message,
    message_text,
)
from bot.replies import Reply

CHAT = Chat(id=1, type="private")


def msg(**kw) -> Message:
    return Message(message_id=1, date=datetime.now(), chat=CHAT, **kw)


def test_message_text():
    assert message_text(msg(text="привет")) == "привет"
    assert message_text(msg(caption="смотри", photo=[PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)])) == "смотри"


def test_connection_from_telegram_uses_rights():
    bc = BusinessConnection(
        id="c1",
        user=User(id=42, is_bot=False, first_name="A"),
        user_chat_id=4242,
        date=datetime.now(),
        is_enabled=True,
        rights=BusinessBotRights(can_reply=True),
    )
    conn = connection_from_telegram(bc)
    assert (conn.owner_id, conn.owner_chat_id, conn.can_reply) == (42, 4242, True)


def test_incoming_labels():
    photo = msg(caption="смотри", photo=[PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)])
    assert incoming_from_message(photo) == Incoming("смотри", "[фото] смотри", False)
    assert incoming_from_message(msg(text="<b>")) == Incoming("<b>", "<b>", False)


def test_format_notification():
    items = [Incoming("это <срочно>", "это <срочно>", False), Incoming("", "[стикер]", True)]
    text = format_notification("Аня & Ко", 7, items, Reply("rule:0", "Скоро", "S1"), None, True)
    assert text.startswith('🚨 <b>Важное</b> от <a href="tg://user?id=7">Аня &amp; Ко</a>')
    assert "это &lt;срочно&gt;\n[стикер]" in text
    assert text.endswith("🤖 Ответил: Скоро + [стикер]")
    quiet = format_notification("Аня", 7, items[:1], None, "уже отвечал недавно", False)
    assert quiet.startswith("💬") and quiet.endswith("🤐 Не ответил: уже отвечал недавно")


def test_chat_keyboard_toggle():
    assert chat_keyboard(-5, False).inline_keyboard[0][0].callback_data == "ignore:-5"
    assert chat_keyboard(5, True).inline_keyboard[0][0].callback_data == "unignore:5"
