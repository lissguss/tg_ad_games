"""Обработчики Telegram для «Взломайте телефон». Ловят только callback_data с префиксом phone:."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import CallbackQuery, EphemeralMessageParameters, InputRichMessage, Update

from . import render
from .service import GameService, Outcome
from .storage import Session

log = logging.getLogger(__name__)
router = Router()

MSG_OLD_CLIENT = "Не удалось открыть игру. Обновите Telegram до последней версии и попробуйте ещё раз."


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

    if ephemeral_id:
        # нажатие в личном экране
        if cq.data == render.VIEW_DATA:
            await cq.answer()  # клетка ввода или попытки — ничего не делаем
            return
        parsed = render.parse_screen_data(cq.data)
        if not parsed:
            await cq.answer("Экран устарел — нажмите цифру в посте")
            return
        action, post_id, revision = parsed

        async def deliver(s: Session):
            try:
                await _with_retry(lambda: bot.edit_ephemeral_message_text(
                    chat_id=chat_id, receiver_user_id=user_id, ephemeral_message_id=ephemeral_id,
                    rich_message=InputRichMessage(html=render.personal_screen(s)),
                ))
            except TelegramBadRequest as e:
                if "not modified" not in str(e):
                    raise
            if s.ephemeral_id != ephemeral_id:
                service.remember_ephemeral(s, ephemeral_id)

        try:
            out = await service.press_on_screen(user_id, chat_id, post_id, action, revision, deliver)
        except Exception:
            log.exception("ошибка при нажатии в личном экране")
            await cq.answer("Что-то пошло не так, попробуйте ещё раз")
            return
        _log("screen", user_id, post_id, f"{action} rev={revision}", out)
        await cq.answer(out.toast)
        return

    # нажатие в общем посте: открываем личный экран на его месте
    action = render.parse_post_data(cq.data)
    if action is None:
        await cq.answer()
        return
    post_id = msg.message_id

    async def deliver(s: Session):
        sent = await _with_retry(lambda: bot.send_rich_message(
            chat_id=chat_id,
            rich_message=InputRichMessage(html=render.personal_screen(s)),
            ephemeral_message_parameters=EphemeralMessageParameters(
                receiver_user_id=user_id,
                callback_query_id=cq.id,
                replace_callback_query_message=True,
            ),
        ))
        service.remember_ephemeral(s, sent.ephemeral_message_id)

    try:
        out = await service.press_on_post(user_id, chat_id, post_id, action, deliver)
    except Exception:
        log.exception("не удалось открыть личный экран")
        await cq.answer(MSG_OLD_CLIENT, show_alert=True)
        return
    _log("post", user_id, post_id, f"phone:{action}", out)
    await cq.answer(out.toast, show_alert=out.alert)
