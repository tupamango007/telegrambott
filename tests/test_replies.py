import tomllib
from datetime import datetime, timezone
from pathlib import Path

from bot.replies import (
    Reply,
    TemplateStore,
    Templates,
    choose_followup,
    choose_reply,
    greeting,
    is_important,
    is_night,
    normalize,
)

EXAMPLE = Path(__file__).parent.parent / "replies.example.toml"


def templates(**kw) -> Templates:
    data = {
        "default": "Привет, {name}! Занят",
        "skip": ["ок", "спасибо"],
        "rules": [{"keywords": ["срочно"], "replies": "Скоро отвечу", "notify": True}],
        "night": {"start": 23, "end": 8, "replies": "Сплю"},
    }
    data.update(kw)
    return Templates.from_dict(data)


DAY = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
NIGHT = datetime(2026, 10, 11, 2, 0, tzinfo=timezone.utc)


def test_default_with_name():
    assert choose_reply(templates(), "привет", "Аня", DAY).text == "Привет, Аня! Занят"
    assert choose_reply(templates(), "", "Аня", DAY).key == "default"  # фото без подписи


def test_skip_whole_message_only():
    assert choose_reply(templates(), "Ок!", "Аня", DAY) is None
    assert choose_reply(templates(), "Спасибо", "Аня", DAY) is None
    assert choose_reply(templates(), "ок, а когда созвон?", "Аня", DAY).key == "default"


def test_rule_beats_night_and_ignores_case():
    reply = choose_reply(templates(), "Это СРОЧНО", "Аня", NIGHT)
    assert (reply.key, reply.text) == ("rule:0", "Скоро отвечу")


def test_night_reply():
    assert choose_reply(templates(), "привет", "Аня", NIGHT).key == "night"


def test_timezone_shifts_night():
    # 21:00 UTC — это полночь по Москве
    evening_utc = datetime(2026, 10, 11, 21, 0, tzinfo=timezone.utc)
    assert choose_reply(templates(), "привет", "", evening_utc).key == "default"
    assert choose_reply(templates(timezone="Europe/Moscow"), "привет", "", evening_utc).key == "night"


def test_is_night_wraps_midnight():
    assert is_night(23, 23, 8) and is_night(3, 23, 8)
    assert not is_night(8, 23, 8) and not is_night(12, 23, 8)
    assert is_night(14, 13, 15) and not is_night(15, 13, 15)
    assert not is_night(3, None, None)


def test_normalize():
    assert normalize("  Ёлки,   ПАЛКИ!! ") == "елки палки"


def test_example_file_is_valid():
    t = Templates.from_dict(tomllib.loads(EXAMPLE.read_text(encoding="utf-8")))
    assert t.default.replies and t.rules and t.night.replies and t.skip
    assert t.rules[0].notify


def test_store_keeps_last_good_version(tmp_path):
    path = tmp_path / "replies.toml"
    path.write_text('default = "Занят"', encoding="utf-8")
    store = TemplateStore(path)
    assert store.get().default.replies == ["Занят"]
    path.write_text("default = [сломано", encoding="utf-8")
    assert store.get().default.replies == ["Занят"]


def test_store_falls_back_to_example(tmp_path):
    store = TemplateStore(tmp_path / "missing.toml", fallback=EXAMPLE)
    assert store.get().default


def test_sticker_only_section():
    t = templates(default={"stickers": ["S1"]})
    reply = choose_reply(t, "привет", "Аня", DAY)
    assert (reply.text, reply.sticker) == (None, "S1")


def test_sticker_chance():
    always = templates(default={"replies": "Занят", "stickers": ["S1"]}, sticker_chance=1)
    never = templates(default={"replies": "Занят", "stickers": ["S1"]}, sticker_chance=0)
    assert choose_reply(always, "привет", "", DAY).sticker == "S1"
    assert choose_reply(never, "привет", "", DAY).sticker is None
    assert choose_reply(never, "привет", "", DAY).text == "Занят"


def test_incoming_sticker():
    assert choose_reply(templates(), "", "", DAY, only_sticker=True) is None
    t = templates(on_sticker={"stickers": ["S2"]})
    reply = choose_reply(t, "", "", DAY, only_sticker=True)
    assert (reply.key, reply.sticker) == ("sticker", "S2")


def test_is_important():
    assert is_important(templates(), "очень СРОЧНО")
    assert not is_important(templates(), "привет")
    assert not is_important(templates(rules=[{"keywords": ["срочно"], "replies": "x"}]), "срочно")


def test_greeting_placeholder_uses_timezone():
    t = templates(default="{greeting}, {name}!", timezone="Europe/Moscow")
    morning_msk = datetime(2026, 10, 11, 6, 0, tzinfo=timezone.utc)  # 09:00 по Москве
    assert choose_reply(t, "привет", "Аня", morning_msk).text == "Доброе утро, Аня!"
    assert [greeting(h) for h in (8, 13, 20, 2)] == ["Доброе утро", "Добрый день", "Добрый вечер", "Доброй ночи"]


def test_multipart_messages():
    assert Reply("default", "Привет! ||  Сейчас занят || ").messages == ["Привет!", "Сейчас занят"]
    assert Reply("sticker", None, "S").messages == []


def test_avoid_repeating_variant():
    t = templates(default=["A", "B"])
    for _ in range(20):
        assert choose_reply(t, "привет", "", DAY, avoid="A").text == "B"
    # единственный вариант всё равно используется
    assert choose_reply(templates(default="A"), "привет", "", DAY, avoid="A").text == "A"


def test_followup():
    assert choose_followup(templates(), "", DAY) is None
    reply = choose_followup(templates(followup={"replies": "Я тут, {name}"}), "Аня", DAY)
    assert (reply.key, reply.text) == ("followup", "Я тут, Аня")
