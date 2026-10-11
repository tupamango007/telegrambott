import tomllib
from datetime import datetime, timezone
from pathlib import Path

from bot.replies import TemplateStore, Templates, choose_reply, is_night, normalize

EXAMPLE = Path(__file__).parent.parent / "replies.example.toml"


def templates(**kw) -> Templates:
    data = {
        "default": "Привет, {name}! Занят",
        "skip": ["ок", "спасибо"],
        "rules": [{"keywords": ["срочно"], "replies": "Скоро отвечу"}],
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
    assert t.default and t.rules and t.night_replies


def test_store_keeps_last_good_version(tmp_path):
    path = tmp_path / "replies.toml"
    path.write_text('default = "Занят"', encoding="utf-8")
    store = TemplateStore(path)
    assert store.get().default == ["Занят"]
    path.write_text("default = [сломано", encoding="utf-8")
    assert store.get().default == ["Занят"]


def test_store_falls_back_to_example(tmp_path):
    store = TemplateStore(tmp_path / "missing.toml", fallback=EXAMPLE)
    assert store.get().default
