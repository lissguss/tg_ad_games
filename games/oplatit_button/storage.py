"""Снимки публикаций и статистика открытий; таблицы других игр не затрагиваются."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS oplatit_posts (
    chat_id      INTEGER NOT NULL,
    message_id   INTEGER NOT NULL,
    intro        TEXT NOT NULL,
    article_html TEXT NOT NULL,
    published    INTEGER NOT NULL DEFAULT 0,
    created_at   INTEGER NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
CREATE TABLE IF NOT EXISTS oplatit_events (
    update_id    INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL,
    chat_id      INTEGER NOT NULL,
    message_id   INTEGER NOT NULL,
    action       TEXT NOT NULL CHECK (action IN ('open', 'cancel')),
    at           INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class Content:
    intro: str
    article_html: str


@dataclass(frozen=True)
class Post:
    chat_id: int
    message_id: int
    intro: str
    article_html: str
    published: bool


class Storage:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def add_post(self, chat_id: int, message_id: int, content: Content, *, published: bool) -> None:
        # Повторный вызов не должен заменить сохранённый текст более свежим шаблоном.
        self.db.execute(
            "INSERT INTO oplatit_posts VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(chat_id, message_id) DO UPDATE SET"
            " published = MAX(oplatit_posts.published, excluded.published)",
            (chat_id, message_id, content.intro, content.article_html, int(published), int(time.time())),
        )

    def get_post(self, chat_id: int, message_id: int) -> Post | None:
        row = self.db.execute(
            "SELECT * FROM oplatit_posts WHERE chat_id=? AND message_id=?", (chat_id, message_id),
        ).fetchone()
        if row is None:
            return None
        return Post(row["chat_id"], row["message_id"], row["intro"], row["article_html"],
                    bool(row["published"]))

    def processed(self, update_id: int) -> bool:
        return self.db.execute(
            "SELECT 1 FROM oplatit_events WHERE update_id=?", (update_id,),
        ).fetchone() is not None

    def log_event(self, update_id: int, user_id: int, chat_id: int, message_id: int,
                  action: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO oplatit_events VALUES (?, ?, ?, ?, ?, ?)",
            (update_id, user_id, chat_id, message_id, action, int(time.time())),
        )

    def stats(self) -> sqlite3.Row:
        # Предпросмотры в личке работают, но в статистику канала не попадают.
        return self.db.execute(
            "SELECT (SELECT COUNT(*) FROM oplatit_posts WHERE published = 1) posts,"
            " COUNT(DISTINCT CASE WHEN e.action = 'open' THEN e.user_id END) readers,"
            " COALESCE(SUM(e.action = 'open'), 0) opens,"
            " COALESCE(SUM(e.action = 'cancel'), 0) cancels"
            " FROM oplatit_events e JOIN oplatit_posts p"
            " ON p.chat_id = e.chat_id AND p.message_id = e.message_id"
            " WHERE p.published = 1",
        ).fetchone()
