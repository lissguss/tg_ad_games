"""Где показывается игра — общее для всех игр.

Основное место — личный экран прямо в посте: нажатие на кнопку поста заменяет пост у этого
игрока его собственной игрой (эфемерное сообщение). Пост бот публикует в канал сам, как админ:
в пересланном посте кнопки неактивны.

Запасное место — чат с ботом: если личный экран открыть не удалось, кнопка ведёт ссылкой
t.me/<бот>?start=<игра>, а /start присылает игру одним сообщением, которое перерисовывается
при каждом нажатии."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, EphemeralMessageParameters, InputRichMessage

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


async def show_in_post(bot: Bot, cq: CallbackQuery, html: str) -> int:
    """Открыть личный экран на месте поста, в котором нажали кнопку. Возвращает его id."""
    sent = await bot.send_rich_message(
        chat_id=cq.message.chat.id,
        rich_message=InputRichMessage(html=html),
        ephemeral_message_parameters=EphemeralMessageParameters(
            receiver_user_id=cq.from_user.id,
            callback_query_id=cq.id,
            replace_callback_query_message=True,
        ),
    )
    # Telegram принял экран; если игрок его не увидел — дело в приложении, а не в боте
    log.info("личный экран chat=%s user=%s id=%s", cq.message.chat.id, cq.from_user.id,
             sent.ephemeral_message_id)
    return sent.ephemeral_message_id
