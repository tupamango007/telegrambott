from bot.storage import Connection, Storage


def make_storage() -> Storage:
    return Storage(":memory:")


def test_connection_roundtrip_and_owner():
    s = make_storage()
    conn = Connection("c1", owner_id=42, owner_chat_id=4242, can_reply=True, is_enabled=True)
    s.save_connection(conn)
    assert s.get_connection("c1") == conn
    assert s.get_connection("nope") is None
    assert s.is_owner(42)
    assert not s.is_owner(1)


def test_history_order_and_limit():
    s = make_storage()
    for i in range(5):
        s.add_message(10, "user" if i % 2 == 0 else "assistant", f"m{i}")
    s.add_message(11, "user", "other chat")
    assert s.history(10, 3) == [("user", "m2"), ("assistant", "m3"), ("user", "m4")]


def test_pause_ignore_enabled():
    s = make_storage()
    assert not s.is_paused(1)
    s.pause_chat(1, 60)
    assert s.is_paused(1)
    s.pause_chat(1, -1)
    assert not s.is_paused(1)

    assert not s.is_ignored(1)
    s.set_ignored(1, True)
    assert s.is_ignored(1)

    assert s.is_enabled()
    s.set_enabled(False)
    assert not s.is_enabled()
