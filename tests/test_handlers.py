from datetime import datetime

from aiogram.types import BusinessBotRights, BusinessConnection, Chat, Message, PhotoSize, User

from bot.handlers import connection_from_telegram, message_text

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
