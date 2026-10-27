"""Игра в чате с ботом — общее для всех игр.

Любая кнопка поста открывает бота ссылкой t.me/<бот>?start=<игра>, а /start присылает игру
одним сообщением, которое перерисовывается при каждом нажатии. Так поле видно на любом
устройстве: личный экран прямо в посте Telegram на iPhone показывает через раз."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InputRichMessage

log = logging.getLogger(__name__)


def bot_link(bot_username: str, start: str) -> str:
    return f"https://t.me/{bot_username}?start={start}"


def is_bot_chat(chat_id: int) -> bool:
    """У личных чатов id положительный, у каналов и групп — отрицательный."""
    return chat_id > 0


async def show(bot: Bot, chat_id: int, html: str, stale_ids: tuple[int | None, ...]) -> int:
    """Прислать игру новым сообщением и убрать устаревшие (прошлый экран игры и команду /start),
    чтобы в чате оставалось одно живое сообщение с игрой. Возвращает id нового сообщения."""
    sent = await bot.send_rich_message(chat_id=chat_id, rich_message=InputRichMessage(html=html))
    for message_id in stale_ids:
        if message_id and message_id != sent.message_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except TelegramAPIError as e:  # уже удалено или старше 48 часов — не страшно
                log.info("не удалось удалить сообщение %s: %s", message_id, e)
    return sent.message_id
