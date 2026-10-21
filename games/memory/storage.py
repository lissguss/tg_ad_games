"""Хранение состояния в SQLite: игровые сессии и обработанные update_id."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

from .logic import Game

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    user_id      INTEGER NOT NULL,
    chat_id      INTEGER NOT NULL,
    message_id   INTEGER NOT NULL,   -- id исходного поста
    layout       TEXT NOT NULL,      -- JSON: 12 эмодзи
    opened       TEXT NOT NULL,      -- JSON: номера карточек, открытых в текущем ходе
    found        TEXT NOT NULL,      -- JSON: номера карточек из найденных пар
    status       TEXT NOT NULL,
    flip_at      REAL,               -- когда перевернуть несовпавшую пару; NULL — нечего
    revision     INTEGER NOT NULL,
    ephemeral_id INTEGER,
    updated_at   INTEGER NOT NULL,
    PRIMARY KEY (user_id, chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS sessions_updated ON sessions (updated_at);
CREATE TABLE IF NOT EXISTS processed_updates (
    update_id    INTEGER PRIMARY KEY,
    at           INTEGER NOT NULL
);
"""

Key = tuple[int, int, int]  # (user_id, chat_id, message_id)


@dataclass
class Session:
    user_id: int
    chat_id: int
    message_id: int
    game: Game
    revision: int = 0
    ephemeral_id: int | None = None
    updated_at: int = 0

    @property
    def key(self) -> Key:
        return self.user_id, self.chat_id, self.message_id


class Storage:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, isolation_level=None)  # autocommit
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    @staticmethod
    def _session(row: sqlite3.Row) -> Session:
        game = Game(
            layout=json.loads(row["layout"]),
            opened=json.loads(row["opened"]),
            found=json.loads(row["found"]),
            status=row["status"],
            flip_at=row["flip_at"],
        )
        return Session(
            user_id=row["user_id"], chat_id=row["chat_id"], message_id=row["message_id"],
            game=game, revision=row["revision"], ephemeral_id=row["ephemeral_id"],
            updated_at=row["updated_at"],
        )

    def load(self, user_id: int, chat_id: int, message_id: int) -> Session | None:
        row = self.db.execute(
            "SELECT * FROM sessions WHERE user_id=? AND chat_id=? AND message_id=?",
            (user_id, chat_id, message_id),
        ).fetchone()
        return self._session(row) if row else None

    def save(self, s: Session) -> None:
        s.updated_at = int(time.time())
        g = s.game
        self.db.execute(
            "INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                s.user_id, s.chat_id, s.message_id,
                json.dumps(g.layout, ensure_ascii=False), json.dumps(g.opened), json.dumps(g.found),
                g.status, g.flip_at, s.revision, s.ephemeral_id, s.updated_at,
            ),
        )

    def pending_flips(self) -> list[Session]:
        """Сессии с несовпавшей парой, которую ещё не перевернули (для восстановления после перезапуска)."""
        rows = self.db.execute("SELECT * FROM sessions WHERE flip_at IS NOT NULL").fetchall()
        return [self._session(r) for r in rows]

    def cleanup(self, ttl_seconds: int) -> int:
        cutoff = int(time.time()) - ttl_seconds
        n = self.db.execute("DELETE FROM sessions WHERE updated_at < ?", (cutoff,)).rowcount
        self.db.execute("DELETE FROM processed_updates WHERE at < ?", (int(time.time()) - 86400,))
        return n

    # --- защита от повторной обработки одного update ---
    def mark_update(self, update_id: int) -> bool:
        """True, если update ещё не обрабатывался (и теперь помечен)."""
        try:
            self.db.execute(
                "INSERT INTO processed_updates VALUES (?, ?)", (update_id, int(time.time()))
            )
            return True
        except sqlite3.IntegrityError:
            return False
