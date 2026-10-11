from bot.ai import SKIP_MARKER, build_messages, build_system_prompt


def test_build_messages_merges_and_trims():
    history = [
        ("assistant", "старое моё"),
        ("user", "привет"),
        ("user", "ты тут?"),
        ("assistant", "да"),
        ("user", "го гулять"),
    ]
    assert build_messages(history) == [
        {"role": "user", "content": "привет\nты тут?"},
        {"role": "assistant", "content": "да"},
        {"role": "user", "content": "го гулять"},
    ]


def test_build_messages_needs_last_user_turn():
    assert build_messages([("user", "привет"), ("assistant", "ку")]) == []
    assert build_messages([]) == []


def test_system_prompt_contains_persona():
    prompt = build_system_prompt("Меня зовут Тест")
    assert "Меня зовут Тест" in prompt
    assert SKIP_MARKER in prompt
