"""Обработка нажатий: сессии, ревизии, блокировки. Без вызовов Telegram — их делает handlers.py.

Играют в личном экране на месте поста: нажатие в посте открывает его, цифра сразу вводится.
Запасной вариант — игра в чате с ботом (/start phone, пост = CHAT_GAME), устроена так же."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .logic import Game
from .storage import Key, Session, Storage

MSG_STALE_SCREEN = "Экран обновился"
MSG_EXPIRED = "Прошлая игра устарела — начинаем новую"

CHAT_GAME = 0  # message_id игры в чате с ботом: поста у неё нет

DIGITS = "0123456789"
DELETE, CHECK, NEW_GAME = "del", "ok", "new"
VIEW = "-"  # клетка экрана или попытки: ничего не меняет
ACTIONS = (*DIGITS, DELETE, CHECK, NEW_GAME, VIEW)

# Показ экрана в Telegram. Вызывается под блокировкой сессии, чтобы экраны не обгоняли друг друга.
Deliver = Callable[[Session], Awaitable[None]] | None


@dataclass
class Outcome:
    session: Session
    toast: str | None = None   # всплывающее уведомление (answerCallbackQuery)
    changed: bool = False      # изменилось ли состояние (нужна перерисовка)


class GameService:
    def __init__(self, storage: Storage, ttl_seconds: int):
        self.storage = storage
        self.ttl = ttl_seconds
        self._locks: dict[Key, asyncio.Lock] = {}

    def _lock(self, key: Key) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def _load_or_create(self, key: Key) -> tuple[Session, bool, bool]:
        """(сессия, создана_заново, прежняя_устарела)."""
        s = self.storage.load(*key)
        if s and time.time() - s.updated_at > self.ttl:
            return Session(*key, Game(), revision=s.revision + 1, ephemeral_id=s.ephemeral_id), True, True
        if s:
            return s, False, False
        return Session(*key, Game()), True, False

    def _apply(self, s: Session, action: str) -> str | None:
        g = s.game
        if action in DIGITS:
            return g.press_digit(action)
        if action == DELETE:
            return g.press_delete()
        if action == CHECK:
            err = g.press_check()
            if err is None:
                self.storage.log_attempt(s)
            return err
        return None

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

    async def press_on_post(self, user_id: int, chat_id: int, message_id: int, action: str,
                            deliver: Deliver = None) -> Outcome:
        """Нажатие в посте: открыть личный экран (та же партия, если она есть); цифра сразу вводится.
        «Удалить» и «Проверить» в посте только открывают экран."""
        key = (user_id, chat_id, message_id)
        async with self._lock(key):
            s, _, expired = self._load_or_create(key)
            toast = MSG_EXPIRED if expired else None
            if action in DIGITS and not s.game.finished:
                toast = self._apply(s, action) or toast
            s.revision += 1  # экран будет новый — кнопки прежнего экрана устаревают
            self.storage.save(s)
            return await self._deliver(Outcome(s, toast, changed=True), deliver)

    async def open_in_chat(self, user_id: int, chat_id: int) -> Session:
        """/start phone: игра в чате с ботом."""
        return await self.open(user_id, chat_id, CHAT_GAME)

    async def press_on_screen(self, user_id: int, chat_id: int, message_id: int,
                              action: str, revision: int, deliver: Deliver = None) -> Outcome:
        """Нажатие на экране игры. Действие применяется, только если ревизия совпадает."""
        key = (user_id, chat_id, message_id)
        async with self._lock(key):
            return await self._deliver(self._screen(key, action, revision), deliver)

    def _screen(self, key: Key, action: str, revision: int) -> Outcome:
        s, created, expired = self._load_or_create(key)
        if created:
            s.revision += 1
            self.storage.save(s)
            return Outcome(s, MSG_EXPIRED if expired else MSG_STALE_SCREEN, changed=True)
        if revision != s.revision:
            return Outcome(s, MSG_STALE_SCREEN, changed=True)
        if action == NEW_GAME:
            fresh = Session(*key, Game(), revision=s.revision + 1, ephemeral_id=s.ephemeral_id)
            self.storage.save(fresh)
            return Outcome(fresh, None, changed=True)
        err = self._apply(s, action)
        if err:
            return Outcome(s, err, changed=False)
        s.revision += 1
        self.storage.save(s)
        return Outcome(s, None, changed=True)

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
