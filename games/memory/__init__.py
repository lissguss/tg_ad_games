"""Игра «Мемори»: найти шесть пар эмодзи под кнопками."""
from __future__ import annotations

from .handlers import open_in_bot_chat, router
from .render import START, public_post
from .service import CHAT_GAME
from .service import GameService
from .storage import Storage

TITLE = "Мемори"
COMMAND = "memory"


def create_service(db_path: str, ttl_seconds: int) -> GameService:
    return GameService(Storage(db_path), ttl_seconds=ttl_seconds)


def post_html(bot_username: str) -> str:
    return public_post(bot_username)


def on_published(service: GameService, chat_id: int, message_id: int) -> None:
    """Мемори не хранит публикации."""


def stats(service: GameService) -> str:
    """Только по сессиям, которые ещё лежат в базе: история не хранится."""
    row = service.storage.db.execute(
        "SELECT COUNT(DISTINCT CASE WHEN message_id != ? THEN message_id END) posts,"
        " COUNT(DISTINCT user_id) players, COUNT(*) games,"
        " COALESCE(SUM(status = 'won'), 0) won FROM sessions",
        (CHAT_GAME,),
    ).fetchone()
    days = service.ttl // 86400
    return (
        f"Активные игры за последние {days} дн. (старые удаляются):\n"
        f"постов с играми: {row['posts']}, игроков: {row['players']}, "
        f"партий: {row['games']}, собрали все пары: {row['won']}"
    )
