"""Обработчики Telegram для «Взломайте телефон». Ловят только callback_data с префиксом phone:."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import CallbackQuery, InputRichMessage, Update

from .. import botchat
from . import render
from .service import GameService, Outcome
from .storage import Session

log = logging.getLogger(__name__)
router = Router()

MSG_FAILED = "Что-то пошло не так, попробуйте ещё раз"


async def _with_retry(call):
    """Один повтор при ограничении частоты (429)."""
    try:
        return await call()
    except TelegramRetryAfter as e:
        log.warning("429, ждём %s с", e.retry_after)
        await asyncio.sleep(e.retry_after)
        return await call()


def _log(where: str, user_id: int, post_id: int, action: str, out: Outcome) -> None:
    g = out.session.game
    log.info("%s user=%s post=%s %s -> %s attempts=%s %s", where, user_id, post_id, action,
             g.status, len(g.attempts), out.toast or "")


async def _edit_screen(bot: Bot, s: Session, screen_id: int) -> None:
    """Перерисовать экран: в чате с ботом это обычное сообщение, в посте — личный экран."""
    html = InputRichMessage(html=render.personal_screen(s))

    async def call():
        if botchat.is_bot_chat(s.chat_id):
            return await bot.edit_message_text(chat_id=s.chat_id, message_id=screen_id, rich_message=html)
        return await bot.edit_ephemeral_message_text(
            chat_id=s.chat_id, receiver_user_id=s.user_id, ephemeral_message_id=screen_id,
            rich_message=html,
        )

    try:
        await _with_retry(call)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            raise


async def open_in_bot_chat(bot: Bot, service: GameService, user_id: int, chat_id: int,
                           start_message_id: int | None = None) -> None:
    """Ответ на /start phone: игра одним сообщением в чате с ботом, прошлое сообщение убирается."""
    s = await service.open_in_chat(user_id, chat_id)
    previous = s.ephemeral_id
    sent_id = await _with_retry(lambda: botchat.show(
        bot, chat_id, render.personal_screen(s), (previous, start_message_id)))
    service.remember_ephemeral(s, sent_id)
    log.info("bot chat user=%s opened, attempts=%s", user_id, len(s.game.attempts))


@router.callback_query(F.data.startswith(render.PREFIX))
async def on_callback(cq: CallbackQuery, bot: Bot, event_update: Update, phone_service: GameService):
    service = phone_service
    if not service.storage.mark_update(event_update.update_id):
        return  # этот update уже обработан
    msg = cq.message
    if msg is None:
        await cq.answer()
        return
    user_id, chat_id = cq.from_user.id, msg.chat.id
    ephemeral_id = getattr(msg, "ephemeral_message_id", None)
    screen_id = ephemeral_id or (msg.message_id if botchat.is_bot_chat(chat_id) else None)

    if not screen_id:
        # нажатие в общем посте: любая кнопка открывает игру в чате с ботом
        me = await bot.me()
        log.info("post user=%s post=%s %s -> bot chat", user_id, msg.message_id, cq.data)
        await cq.answer(url=botchat.bot_link(me.username, render.START))
        return

    # нажатие в игре в чате с ботом (или в личном экране, открытом в посте раньше)
    if cq.data == render.VIEW_DATA:
        await cq.answer()  # клетка ввода или попытки — ничего не делаем
        return
    parsed = render.parse_screen_data(cq.data)
    if not parsed:
        await cq.answer("Экран устарел — нажмите кнопку в посте")
        return
    action, post_id, revision = parsed

    async def deliver(s: Session):
        await _edit_screen(bot, s, screen_id)
        if s.ephemeral_id != screen_id:
            service.remember_ephemeral(s, screen_id)

    try:
        out = await service.press_on_screen(user_id, chat_id, post_id, action, revision, deliver)
    except Exception:
        log.exception("ошибка при нажатии на экране игры")
        await cq.answer(MSG_FAILED)
        return
    _log("screen", user_id, post_id, f"{action} rev={revision}", out)
    await cq.answer(out.toast)
