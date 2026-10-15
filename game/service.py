"""Обработка нажатий: сессии, ревизии, блокировки, переворот пар. Без вызовов Telegram — их делает handlers.py."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .logic import Game, generate_layout
from .storage import Key, Session, Storage

MSG_STALE_SCREEN = "Экран обновился"
MSG_RESTORED = "Ваша игра восстановлена"
MSG_EXPIRED = "Прошлая игра устарела — начинаем новую"
MSG_NOT_SHOWN = "Ваша версия Telegram не может показать игру. Обновите приложение и попробуйте снова."

# Нажатия в общем посте подряд (без нажатий в личном экране) с интервалом не больше BLIND_WINDOW секунд.
# Со второго такого нажатия считаем, что приложение игрока не показывает личный экран.
BLIND_PRESSES = 2
BLIND_WINDOW = 60

CARD, NEW_GAME = "c", "new"

# Показ экрана в Telegram. Вызывается под блокировкой сессии, чтобы экраны не обгоняли друг друга.
Deliver = Callable[[Session], Awaitable[None]] | None


@dataclass
class Outcome:
    session: Session
    toast: str | None = None   # всплывающее уведомление (answerCallbackQuery)
    changed: bool = False      # изменилось ли состояние (нужна перерисовка)
    alert: bool = False        # показать уведомление окном, а не всплывающей строкой


class GameService:
    def __init__(self, storage: Storage, ttl_seconds: int):
        self.storage = storage
        self.ttl = ttl_seconds
        self._locks: dict[Key, asyncio.Lock] = {}
        self._post_streak: dict[Key, tuple[int, float]] = {}  # (нажатий подряд, время последнего)

    def _lock(self, key: Key) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    @staticmethod
    def _new_session(key: Key, revision: int = 0) -> Session:
        return Session(*key, Game(layout=generate_layout()), revision=revision)

    def _load_or_create(self, key: Key) -> tuple[Session, bool, bool]:
        """(сессия, создана_заново, прежняя_устарела)."""
        s = self.storage.load(*key)
        if s and time.time() - s.updated_at > self.ttl:
            fresh = self._new_session(key, s.revision + 1)
            fresh.ephemeral_id = s.ephemeral_id
            return fresh, True, True
        if s:
            return s, False, False
        return self._new_session(key), True, False

    @staticmethod
    async def _deliver(out: Outcome, deliver: Deliver) -> Outcome:
        if deliver and out.changed:
            await deliver(out.session)
        return out

    async def press_on_post(self, user_id: int, chat_id: int, message_id: int, card: int,
                            deliver: Deliver = None, now: float | None = None) -> Outcome:
        """Нажатие на карточку в общем посте: начать (или восстановить) игру и открыть карточку."""
        key = (user_id, chat_id, message_id)
        async with self._lock(key):
            s, created, expired = self._load_or_create(key)
            toast, _ = s.game.press(card, time.time() if now is None else now)
            if expired:
                toast = MSG_EXPIRED
            elif not created:
                toast = toast or MSG_RESTORED
            s.revision += 1  # экран будет новый — старые кнопки должны стать неактуальными
            self.storage.save(s)
            out = Outcome(s, toast, changed=True)
            if self._count_post_press(key, time.time() if now is None else now):
                out.toast, out.alert = MSG_NOT_SHOWN, True
            return await self._deliver(out, deliver)

    def _count_post_press(self, key: Key, now: float) -> bool:
        """True, если игрок жмёт в общий пост подряд — значит, личный экран у него не открывается."""
        count, last = self._post_streak.get(key, (0, 0.0))
        count = count + 1 if now - last <= BLIND_WINDOW else 1
        self._post_streak[key] = (count, now)
        return count >= BLIND_PRESSES

    async def press_on_screen(self, user_id: int, chat_id: int, message_id: int,
                              action: str, arg: int, revision: int,
                              deliver: Deliver = None, now: float | None = None) -> Outcome:
        """Нажатие в личном экране. Действие применяется, только если ревизия совпадает."""
        key = (user_id, chat_id, message_id)
        self._post_streak.pop(key, None)  # личный экран у игрока открылся
        async with self._lock(key):
            out = self._screen(key, action, arg, revision, time.time() if now is None else now)
            return await self._deliver(out, deliver)

    def _screen(self, key: Key, action: str, arg: int, revision: int, now: float) -> Outcome:
        s, created, expired = self._load_or_create(key)
        if created:
            s.revision += 1
            self.storage.save(s)
            return Outcome(s, MSG_EXPIRED if expired else MSG_STALE_SCREEN, changed=True)
        if revision != s.revision:
            return Outcome(s, MSG_STALE_SCREEN, changed=True)
        if action == NEW_GAME:
            fresh = self._new_session(key, s.revision + 1)
            fresh.ephemeral_id = s.ephemeral_id
            self.storage.save(fresh)
            return Outcome(fresh, None, changed=True)
        if action != CARD:
            return Outcome(s, None, changed=False)
        toast, changed = s.game.press(arg, now)
        if not changed:
            return Outcome(s, toast, changed=False)
        s.revision += 1
        self.storage.save(s)
        return Outcome(s, toast, changed=True)

    async def flip(self, key: Key, deliver: Deliver = None, now: float | None = None) -> Outcome | None:
        """Перевернуть несовпавшую пару, если подошло время. None — переворачивать нечего
        (игрок уже нажал другую карточку или пара перевёрнута раньше)."""
        async with self._lock(key):
            s = self.storage.load(*key)
            now = time.time() if now is None else now
            if not s or s.game.flip_at is None or s.game.flip_at > now:
                return None
            s.game.flip_back()
            s.revision += 1
            self.storage.save(s)
            return await self._deliver(Outcome(s, None, changed=True), deliver)

    def remember_ephemeral(self, s: Session, ephemeral_id: int) -> None:
        s.ephemeral_id = ephemeral_id
        self.storage.save(s)

    def cleanup(self) -> int:
        n = self.storage.cleanup(self.ttl)
        for key, lock in list(self._locks.items()):
            if not lock.locked():
                del self._locks[key]
        cutoff = time.time() - BLIND_WINDOW
        for key, (_, last) in list(self._post_streak.items()):
            if last < cutoff:
                del self._post_streak[key]
        return n
