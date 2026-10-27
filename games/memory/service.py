"""Обработка нажатий: сессии, ревизии, блокировки, переворот пар. Без вызовов Telegram — их делает handlers.py.

Играют в чате с ботом: любая кнопка поста открывает бота (/start memory), игра там — одно
сообщение, которое перерисовывается при каждом нажатии. Личные экраны в посте (они ещё живы
у тех, кто открыл их раньше) обрабатываются так же."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .logic import Game, generate_layout
from .storage import Key, Session, Storage

MSG_STALE_SCREEN = "Экран обновился"
MSG_EXPIRED = "Прошлая игра устарела — начинаем новую"

CARD, NEW_GAME = "c", "new"
CHAT_GAME = 0  # message_id игры в чате с ботом («Играть в боте»): поста у неё нет

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

    async def open(self, user_id: int, chat_id: int, message_id: int) -> Session:
        """Показать игру заново (новым сообщением): та же партия, старые экраны устаревают."""
        key = (user_id, chat_id, message_id)
        async with self._lock(key):
            s, _, _ = self._load_or_create(key)
            s.revision += 1
            self.storage.save(s)
            return s

    async def press_on_screen(self, user_id: int, chat_id: int, message_id: int,
                              action: str, arg: int, revision: int,
                              deliver: Deliver = None, now: float | None = None) -> Outcome:
        """Нажатие на экране игры. Действие применяется, только если ревизия совпадает."""
        key = (user_id, chat_id, message_id)
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

    async def open_in_chat(self, user_id: int, chat_id: int) -> Session:
        """/start memory: игра в чате с ботом."""
        return await self.open(user_id, chat_id, CHAT_GAME)

    def remember_ephemeral(self, s: Session, ephemeral_id: int) -> None:
        """id сообщения с экраном: личного экрана в посте или игры в чате с ботом."""
        s.ephemeral_id = ephemeral_id
        self.storage.save(s)

    def cleanup(self) -> int:
        n = self.storage.cleanup(self.ttl)
        for key, lock in list(self._locks.items()):
            if not lock.locked():
                del self._locks[key]
        return n
