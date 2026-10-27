"""Обработчики Telegram: нажатия кнопок и автопереворот пар. Команды публикации — в bot/admin.py."""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import CallbackQuery, InputRichMessage, Update

from .. import botchat
from . import render
from .service import GameService, Outcome
from .storage import Key, Session

log = logging.getLogger(__name__)
router = Router()

MSG_FAILED = "Что-то пошло не так, попробуйте ещё раз"

_flip_tasks: set[asyncio.Task] = set()  # ссылки на задачи, чтобы их не собрал сборщик мусора


async def _with_retry(call):
    """Один повтор при ограничении частоты (429)."""
    try:
        return await call()
    except TelegramRetryAfter as e:
        log.warning("429, ждём %s с", e.retry_after)
        await asyncio.sleep(e.retry_after)
        return await call()


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
    """Ответ на /start memory: игра одним сообщением в чате с ботом, прошлое сообщение убирается."""
    s = await service.open_in_chat(user_id, chat_id)
    previous = s.ephemeral_id
    sent_id = await _with_retry(lambda: botchat.show(
        bot, chat_id, render.personal_screen(s), (previous, start_message_id)))
    service.remember_ephemeral(s, sent_id)  # сюда же перерисует автопереворот пары
    schedule_flip(bot, service, s)  # пара могла ждать переворота
    log.info("bot chat user=%s opened, found=%s/12", user_id, len(s.game.found))


# --- автопереворот несовпавшей пары ---

def schedule_flip(bot: Bot, service: GameService, s: Session) -> None:
    if s.game.flip_at is None:
        return
    task = asyncio.create_task(_flip_later(bot, service, s.key, s.game.flip_at))
    _flip_tasks.add(task)
    task.add_done_callback(_flip_tasks.discard)


async def _flip_later(bot: Bot, service: GameService, key: Key, flip_at: float) -> None:
    await asyncio.sleep(max(0.0, flip_at - time.time()))

    async def deliver(s: Session):
        if s.ephemeral_id:
            await _edit_screen(bot, s, s.ephemeral_id)

    try:
        out = await service.flip(key, deliver)
    except Exception:
        # личный экран мог исчезнуть (перезапуск приложения) — состояние уже сохранено
        log.warning("не удалось перерисовать экран после переворота %s", key, exc_info=True)
        return
    if out:
        log.info("flip user=%s post=%s", key[0], key[2])


def resume_pending_flips(bot: Bot, service: GameService) -> None:
    """После перезапуска сервера: доделать перевороты, которые не успели выполниться."""
    pending = service.storage.pending_flips()
    for s in pending:
        schedule_flip(bot, service, s)
    if pending:
        log.info("восстановлено отложенных переворотов: %s", len(pending))


# --- нажатия ---

def _log(where: str, user_id: int, post_id: int, action: str, out: Outcome) -> None:
    g = out.session.game
    log.info("%s user=%s post=%s %s -> %s found=%s/12 %s", where, user_id, post_id, action,
             g.status, len(g.found), out.toast or "")


@router.callback_query(F.data.regexp(render.CALLBACK_RE))
async def on_callback(cq: CallbackQuery, bot: Bot, event_update: Update, service: GameService):
    if not service.storage.mark_update(event_update.update_id):
        return  # этот update уже обработан
    msg = cq.message
    if msg is None or not cq.data:
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
    parsed = render.parse_screen_data(cq.data)
    if not parsed:
        await cq.answer("Экран устарел — нажмите кнопку в посте")
        return
    action, card, post_id, revision = parsed

    async def deliver(s: Session):
        await _edit_screen(bot, s, screen_id)
        if s.ephemeral_id != screen_id:
            service.remember_ephemeral(s, screen_id)

    try:
        out = await service.press_on_screen(user_id, chat_id, post_id, action, card, revision, deliver)
    except Exception:
        log.exception("ошибка при нажатии на экране игры")
        await cq.answer(MSG_FAILED)
        return
    _log("screen", user_id, post_id, f"{action}:{card} rev={revision}", out)
    if out.changed:
        schedule_flip(bot, service, out.session)
    await cq.answer(out.toast)
