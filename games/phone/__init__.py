"""Игра «Взломайте телефон»: подобрать пароль-дату из 6 цифр за 5 попыток."""
from __future__ import annotations

from .handlers import open_in_bot_chat, router
from .render import START, public_post
from .service import GameService
from .storage import Storage

TITLE = "Взломайте телефон"
COMMAND = "post_phone"


def create_service(db_path: str, ttl_seconds: int) -> GameService:
    return GameService(Storage(db_path), ttl_seconds=ttl_seconds)


def post_html() -> str:
    return public_post()


def on_published(service: GameService, chat_id: int, message_id: int) -> None:
    service.storage.add_post(chat_id, message_id)


def stats(service: GameService) -> str:
    r = service.storage.stats()
    return (
        f"постов: {r['posts']}, игроков: {r['players']}, проверок: {r['checks']}\n"
        f"взломали: {r['solvers']} чел. ({r['wins']} партий), "
        f"исчерпали 5 попыток: {r['losses']} партий"
    )
