import sqlite3
import threading
import time
from pathlib import Path

from .models import ChatMessage


class BotStorage:
    """Small persistent store for idempotency, dialogue memory and orders."""

    def __init__(self, path: str) -> None:
        database = Path(path)
        database.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(database, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS processed_messages (
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (chat_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    telegram_message_id INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE (chat_id, telegram_message_id, role)
                );
                CREATE INDEX IF NOT EXISTS messages_chat_idx
                    ON messages(chat_id, id);
                CREATE TABLE IF NOT EXISTS customer_memory (
                    chat_id INTEGER PRIMARY KEY,
                    summary TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orders (
                    chat_id INTEGER PRIMARY KEY,
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rate_events (
                    chat_id INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS rate_events_chat_idx
                    ON rate_events(chat_id, created_at);
                CREATE TABLE IF NOT EXISTS sent_events (
                    chat_id INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (chat_id, event)
                );
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            self._db.commit()

    def get_bot_setting(self, key: str) -> str | None:
        """Сохранённый override настройки бота или None, если его нет.

        Пустая строка не хранится как override: saved_setting("") ведёт себя
        как отсутствие override, чтобы промпт никогда не стал пустым.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM bot_settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_bot_setting(self, key: str, value: str) -> None:
        now = time.time()
        with self._lock:
            if value:
                self._db.execute(
                    """
                    INSERT INTO bot_settings(key, value, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (key, value, now),
                )
            else:
                self._db.execute("DELETE FROM bot_settings WHERE key = ?", (key,))
            self._db.commit()

    def claim_message(self, chat_id: int, message_id: int) -> bool:
        """Atomically claim a Telegram message. False means it is a duplicate."""
        with self._lock:
            cursor = self._db.execute(
                """
                INSERT OR IGNORE INTO processed_messages(chat_id, message_id, created_at)
                VALUES (?, ?, ?)
                """,
                (chat_id, message_id, time.time()),
            )
            self._db.commit()
            return cursor.rowcount == 1

    def allow_request(
        self, chat_id: int, limit: int, window_seconds: int = 60
    ) -> bool:
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            self._db.execute("DELETE FROM rate_events WHERE created_at < ?", (cutoff,))
            count = self._db.execute(
                "SELECT COUNT(*) FROM rate_events WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()[0]
            if count >= limit:
                self._db.commit()
                return False
            self._db.execute(
                "INSERT INTO rate_events(chat_id, created_at) VALUES (?, ?)",
                (chat_id, now),
            )
            self._db.commit()
            return True

    def claim_event(self, chat_id: int, event: str) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO sent_events(chat_id, event, created_at) VALUES (?, ?, ?)",
                (chat_id, event, time.time()),
            )
            self._db.commit()
            return cursor.rowcount == 1

    def has_message_after(self, chat_id: int, message_id: int) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM processed_messages WHERE chat_id = ? AND message_id > ? LIMIT 1",
                (chat_id, message_id),
            ).fetchone()
        return row is not None

    def history(self, chat_id: int, limit: int = 20) -> list[ChatMessage]:
        with self._lock:
            rows = self._db.execute(
                """
                SELECT role, content FROM (
                    SELECT id, role, content
                    FROM messages
                    WHERE chat_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id
                """,
                (chat_id, limit),
            ).fetchall()
        return [ChatMessage(role=row["role"], content=row["content"]) for row in rows]

    def add_exchange(
        self,
        chat_id: int,
        message_id: int,
        user_text: str,
        assistant_text: str,
        kind: str,
    ) -> None:
        now = time.time()
        with self._lock:
            self._db.executemany(
                """
                INSERT OR IGNORE INTO messages(
                    chat_id, telegram_message_id, role, content, kind, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (chat_id, message_id, "user", user_text, kind, now),
                    (chat_id, message_id, "assistant", assistant_text, "text", now),
                ],
            )
            self._db.commit()

    def memory(self, chat_id: int) -> str:
        with self._lock:
            row = self._db.execute(
                "SELECT summary FROM customer_memory WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        return row["summary"] if row else ""

    def update_memory(
        self, chat_id: int, customer_summary: str, order_summary: str
    ) -> None:
        now = time.time()
        with self._lock:
            if customer_summary.strip():
                self._db.execute(
                    """
                    INSERT INTO customer_memory(chat_id, summary, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET
                        summary = excluded.summary,
                        updated_at = excluded.updated_at
                    """,
                    (chat_id, customer_summary.strip(), now),
                )
            if order_summary.strip():
                self._db.execute(
                    """
                    INSERT INTO orders(chat_id, summary, status, updated_at)
                    VALUES (?, ?, 'active', ?)
                    ON CONFLICT(chat_id) DO UPDATE SET
                        summary = excluded.summary,
                        status = 'active',
                        updated_at = excluded.updated_at
                    """,
                    (chat_id, order_summary.strip(), now),
                )
            self._db.commit()

    def clear_chat(self, chat_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
            self._db.execute("DELETE FROM customer_memory WHERE chat_id = ?", (chat_id,))
            self._db.execute("DELETE FROM orders WHERE chat_id = ?", (chat_id,))
            self._db.execute("DELETE FROM rate_events WHERE chat_id = ?", (chat_id,))
            self._db.execute("DELETE FROM sent_events WHERE chat_id = ?", (chat_id,))
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()
