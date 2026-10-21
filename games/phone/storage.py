"""Хранение в SQLite. Все таблицы с префиксом phone_ — с таблицами мемори не пересекаются."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

from .logic import Attempt, Game

SCHEMA = """
CREATE TABLE IF NOT EXISTS phone_posts (
    chat_id      INTEGER NOT NULL,
    message_id   INTEGER NOT NULL,
    created_at   INTEGER NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
CREATE TABLE IF NOT EXISTS phone_sessions (
    user_id      INTEGER NOT NULL,
    chat_id      INTEGER NOT NULL,
    message_id   INTEGER NOT NULL,   -- id исходного поста
    current      TEXT NOT NULL,      -- набранные, ещё не проверенные цифры
    attempts     TEXT NOT NULL,      -- JSON: [{"guess": "...", "marks": [...]}, ...]
    status       TEXT NOT NULL,
    revision     INTEGER NOT NULL,
    ephemeral_id INTEGER,
    updated_at   INTEGER NOT NULL,
    PRIMARY KEY (user_id, chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS phone_sessions_updated ON phone_sessions (updated_at);
-- каждая проверка комбинации; не чистится, по ней считается /stats
CREATE TABLE IF NOT EXISTS phone_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    chat_id      INTEGER NOT NULL,
    message_id   INTEGER NOT NULL,
    attempt_no   INTEGER NOT NULL,   -- 1..5 в рамках партии
    guess        TEXT NOT NULL,
    correct      INTEGER NOT NULL,
    at           INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS phone_processed_updates (
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

    # --- посты ---
    def add_post(self, chat_id: int, message_id: int) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO phone_posts VALUES (?, ?, ?)",
            (chat_id, message_id, int(time.time())),
        )

    # --- сессии ---
    def load(self, user_id: int, chat_id: int, message_id: int) -> Session | None:
        row = self.db.execute(
            "SELECT * FROM phone_sessions WHERE user_id=? AND chat_id=? AND message_id=?",
            (user_id, chat_id, message_id),
        ).fetchone()
        if not row:
            return None
        game = Game(
            current=row["current"],
            attempts=[Attempt(a["guess"], a["marks"]) for a in json.loads(row["attempts"])],
            status=row["status"],
        )
        return Session(
            user_id=row["user_id"], chat_id=row["chat_id"], message_id=row["message_id"],
            game=game, revision=row["revision"], ephemeral_id=row["ephemeral_id"],
            updated_at=row["updated_at"],
        )

    def save(self, s: Session) -> None:
        s.updated_at = int(time.time())
        g = s.game
        self.db.execute(
            "INSERT OR REPLACE INTO phone_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                s.user_id, s.chat_id, s.message_id, g.current,
                json.dumps([{"guess": a.guess, "marks": a.marks} for a in g.attempts]),
                g.status, s.revision, s.ephemeral_id, s.updated_at,
            ),
        )

    def cleanup(self, ttl_seconds: int) -> int:
        cutoff = int(time.time()) - ttl_seconds
        n = self.db.execute("DELETE FROM phone_sessions WHERE updated_at < ?", (cutoff,)).rowcount
        self.db.execute("DELETE FROM phone_processed_updates WHERE at < ?", (int(time.time()) - 86400,))
        return n

    # --- журнал проверок ---
    def log_attempt(self, s: Session) -> None:
        a = s.game.attempts[-1]
        self.db.execute(
            "INSERT INTO phone_attempts (user_id, chat_id, message_id, attempt_no, guess, correct, at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (s.user_id, s.chat_id, s.message_id, len(s.game.attempts), a.guess,
             int(a.guess == s.game.secret), int(time.time())),
        )

    def stats(self) -> sqlite3.Row:
        return self.db.execute(
            "SELECT"
            " (SELECT COUNT(*) FROM phone_posts) posts,"
            " COUNT(DISTINCT user_id) players,"
            " COUNT(*) checks,"
            " COUNT(DISTINCT CASE WHEN correct THEN user_id END) solvers,"
            " COALESCE(SUM(correct), 0) wins,"
            " COALESCE(SUM(attempt_no = 5 AND NOT correct), 0) losses"
            " FROM phone_attempts"
        ).fetchone()

    # --- защита от повторной обработки одного update ---
    def mark_update(self, update_id: int) -> bool:
        """True, если update ещё не обрабатывался (и теперь помечен)."""
        try:
            self.db.execute(
                "INSERT INTO phone_processed_updates VALUES (?, ?)", (update_id, int(time.time()))
            )
            return True
        except sqlite3.IntegrityError:
            return False
