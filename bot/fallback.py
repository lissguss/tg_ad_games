"""Подключается последним: отвечает на кнопки, которые не взяла ни одна игра, чтобы у игрока не висели часики."""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery

log = logging.getLogger(__name__)
router = Router()


@router.callback_query()
async def on_unknown_callback(cq: CallbackQuery):
    log.info("кнопка без игры: user=%s data=%r", cq.from_user.id, cq.data)
    await cq.answer("Экран устарел — нажмите кнопку в посте")
