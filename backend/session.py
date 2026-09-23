"""Small SQLite conversation store scoped to a browser-generated session ID."""

import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class SessionStore:
    def __init__(self, database: str | Path = Path(__file__).with_name("demo_cart.sqlite3")) -> None:
        self.database = str(database)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS chat_history_session ON chat_history(session_id, id)")
            db.execute("""CREATE TABLE IF NOT EXISTS chat_selection (
                session_id TEXT PRIMARY KEY, product_ids TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS shopping_requirements (
                session_id TEXT PRIMARY KEY, data_json TEXT NOT NULL)""")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def recent(self, session_id: str, limit: int = 8) -> list[dict[str, str]]:
        with self._connect() as db:
            rows = db.execute("SELECT role, content FROM chat_history WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit)).fetchall()
        return [dict(row) for row in reversed(rows)]

    def append_exchange(self, session_id: str, user_message: str, assistant_message: str) -> None:
        with self._connect() as db:
            db.executemany("INSERT INTO chat_history(session_id, role, content) VALUES(?,?,?)", [
                (session_id, "user", user_message[:1000]),
                (session_id, "assistant", assistant_message[:1000]),
            ])
            db.execute("""DELETE FROM chat_history WHERE session_id=? AND id NOT IN (
                SELECT id FROM chat_history WHERE session_id=? ORDER BY id DESC LIMIT 12)""", (session_id, session_id))

    def clear(self, session_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM chat_history WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM chat_selection WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM shopping_requirements WHERE session_id=?", (session_id,))

    def remember_products(self, session_id: str, product_ids: list[str]) -> None:
        with self._connect() as db:
            db.execute("""INSERT INTO chat_selection(session_id, product_ids) VALUES(?,?)
                ON CONFLICT(session_id) DO UPDATE SET product_ids=excluded.product_ids""",
                (session_id, json.dumps(product_ids[:3])))

    def last_product_ids(self, session_id: str) -> list[str]:
        with self._connect() as db:
            row = db.execute("SELECT product_ids FROM chat_selection WHERE session_id=?", (session_id,)).fetchone()
        return json.loads(row["product_ids"]) if row else []

    def save_requirements(self, session_id: str, data: dict) -> None:
        with self._connect() as db:
            db.execute("""INSERT INTO shopping_requirements(session_id, data_json) VALUES(?,?)
                ON CONFLICT(session_id) DO UPDATE SET data_json=excluded.data_json""",
                (session_id, json.dumps(data, ensure_ascii=False)))

    def get_requirements(self, session_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT data_json FROM shopping_requirements WHERE session_id=?", (session_id,)).fetchone()
        return json.loads(row["data_json"]) if row else None
