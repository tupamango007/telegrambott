"""SQLite-хранилище: бизнес-подключения, история чатов и настройки."""

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    id           TEXT PRIMARY KEY,
    owner_id     INTEGER NOT NULL,
    owner_chat_id INTEGER NOT NULL,
    can_reply    INTEGER NOT NULL,
    is_enabled   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id  INTEGER NOT NULL,
    role     TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text     TEXT NOT NULL,
    ts       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_chat ON messages (chat_id, id);
CREATE TABLE IF NOT EXISTS chats (
    chat_id      INTEGER PRIMARY KEY,
    paused_until REAL NOT NULL DEFAULT 0,
    ignored      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Connection:
    id: str
    owner_id: int
    owner_chat_id: int
    can_reply: bool
    is_enabled: bool


class Storage:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    # --- подключения ---

    def save_connection(self, conn: Connection) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO connections VALUES (?, ?, ?, ?, ?)",
                (conn.id, conn.owner_id, conn.owner_chat_id, int(conn.can_reply), int(conn.is_enabled)),
            )

    def get_connection(self, connection_id: str) -> Connection | None:
        row = self._db.execute(
            "SELECT id, owner_id, owner_chat_id, can_reply, is_enabled FROM connections WHERE id = ?",
            (connection_id,),
        ).fetchone()
        if row is None:
            return None
        return Connection(row[0], row[1], row[2], bool(row[3]), bool(row[4]))

    def is_owner(self, user_id: int) -> bool:
        row = self._db.execute("SELECT 1 FROM connections WHERE owner_id = ? LIMIT 1", (user_id,)).fetchone()
        return row is not None

    # --- история ---

    def add_message(self, chat_id: int, role: str, text: str) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO messages (chat_id, role, text, ts) VALUES (?, ?, ?, ?)",
                (chat_id, role, text, time.time()),
            )

    def history(self, chat_id: int, limit: int) -> list[tuple[str, str]]:
        rows = self._db.execute(
            "SELECT role, text FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [(role, text) for role, text in reversed(rows)]

    # --- состояние чатов ---

    def _ensure_chat(self, chat_id: int) -> None:
        self._db.execute("INSERT OR IGNORE INTO chats (chat_id) VALUES (?)", (chat_id,))

    def pause_chat(self, chat_id: int, seconds: float) -> None:
        with self._db:
            self._ensure_chat(chat_id)
            self._db.execute(
                "UPDATE chats SET paused_until = ? WHERE chat_id = ?", (time.time() + seconds, chat_id)
            )

    def is_paused(self, chat_id: int) -> bool:
        row = self._db.execute("SELECT paused_until FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        return row is not None and row[0] > time.time()

    def set_ignored(self, chat_id: int, ignored: bool) -> None:
        with self._db:
            self._ensure_chat(chat_id)
            self._db.execute("UPDATE chats SET ignored = ? WHERE chat_id = ?", (int(ignored), chat_id))

    def is_ignored(self, chat_id: int) -> bool:
        row = self._db.execute("SELECT ignored FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        return row is not None and bool(row[0])

    # --- глобальный выключатель ---

    def set_enabled(self, enabled: bool) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO settings VALUES ('enabled', ?)", ("1" if enabled else "0",)
            )

    def is_enabled(self) -> bool:
        row = self._db.execute("SELECT value FROM settings WHERE key = 'enabled'").fetchone()
        return row is None or row[0] == "1"
