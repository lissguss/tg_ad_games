"""«Оплатить» раскрывает лонгрид, «Отменить платёж» возвращает первый личный экран."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import CallbackQuery, InputRichMessage, Update

from .. import botchat
from . import render
from .service import GameService
from .storage import Post

log = logging.getLogger(__name__)
router = Router()

MSG_STALE = "Экран устарел — откройте пост заново"
MSG_FAILED = "Не удалось обновить экран — попробуйте ещё раз"


async def _with_retry(call):
    try:
        return await call()
    except TelegramRetryAfter as e:
        log.warning("oplatit_button: 429, ждём %s с", e.retry_after)
        await asyncio.sleep(e.retry_after)
        return await call()


@router.callback_query(F.data.startswith(render.PREFIX))
async def on_callback(cq: CallbackQuery, bot: Bot, event_update: Update,
                      oplatit_service: GameService):
    parsed = render.parse_data(cq.data)
    msg = cq.message
    if msg is None or parsed is None:
        await cq.answer(MSG_STALE)
        return
    action, post_id = parsed
    ephemeral_id = getattr(msg, "ephemeral_message_id", None)
    private_preview = msg.chat.type == "private" and not ephemeral_id
    log.info("oplatit_button callback user=%s chat=%s message=%s screen=%s data=%r", cq.from_user.id,
             msg.chat.id, msg.message_id, ephemeral_id, cq.data)
    # В личке предпросмотр уже виден только админу — редактируем его обычное сообщение.
    # В канале исходный пост остаётся общим, поэтому там нужен личный экран.
    if post_id is None:
        if ephemeral_id or msg.message_id <= 0:
            await cq.answer(MSG_STALE)
            return
        post_id = msg.message_id
    elif not ephemeral_id and not (private_preview and post_id == msg.message_id):
        await cq.answer(MSG_STALE)
        return

    async def deliver(post: Post):
        content = (render.article_message(post.article_html, post.message_id) if action == render.OPEN
                   else InputRichMessage(html=render.public_post(post.intro, post.message_id)))
        if not ephemeral_id and not private_preview:
            await _with_retry(lambda: botchat.show_in_post(bot, cq, content))
            return

        async def edit():
            if private_preview:
                return await bot.edit_message_text(chat_id=msg.chat.id, message_id=msg.message_id,
                                                   rich_message=content)
            return await bot.edit_ephemeral_message_text(
                chat_id=msg.chat.id, receiver_user_id=cq.from_user.id,
                ephemeral_message_id=ephemeral_id, rich_message=content,
            )

        try:
            await _with_retry(edit)
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                raise

    try:
        await oplatit_service.display(cq.from_user.id, msg.chat.id, post_id, action,
                                      event_update.update_id, deliver,
                                      published=msg.chat.type == "channel")
    except Exception:
        log.exception("oplatit_button: не удалось показать экран")
        await cq.answer(MSG_FAILED)
        return
    log.info("oplatit_button user=%s chat=%s post=%s action=%s", cq.from_user.id,
             msg.chat.id, post_id, action)
    await cq.answer()
