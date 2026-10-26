"""Обработка нажатий: сессии, ревизии, блокировки, переворот пар. Без вызовов Telegram — их делает handlers.py."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .logic import CARDS, WON, Game, generate_layout
from .storage import Key, Session, Storage

MSG_STALE_SCREEN = "Экран обновился"
MSG_RESTORED = "Ваша игра восстановлена"
MSG_EXPIRED = "Прошлая игра устарела — начинаем новую"
MSG_WON_POST = "Все пары найдены! Ставьте 🔥 под постом"

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


def describe_move(g: Game, card: int, found_before: int) -> str:
    """Что произошло после нажатия на карточку card — для всплывающей подсказки.
    На iPhone Telegram не показывает личный экран, и игрок проходит игру по этим подсказкам."""
    if g.status == WON:
        return MSG_WON_POST
    if len(g.found) > found_before:
        return f"{g.layout[card]} + {g.layout[card]} — пара! Найдено {len(g.found) // 2} из {CARDS // 2}"
    if g.mismatch:
        a, b = g.opened
        return f"{g.layout[a]} и {g.layout[b]} — не совпали, запоминайте"
    return f"Карточка {card + 1}: {g.layout[card]}"


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

    async def press_on_post(self, user_id: int, chat_id: int, message_id: int, card: int,
                            deliver: Deliver = None, now: float | None = None) -> Outcome:
        """Нажатие на карточку в общем посте: начать (или восстановить) игру и открыть карточку."""
        key = (user_id, chat_id, message_id)
        async with self._lock(key):
            s, created, expired = self._load_or_create(key)
            found_before = len(s.game.found)
            toast, changed = s.game.press(card, time.time() if now is None else now)
            # Ход описывается словами: у кого Telegram не показывает личный экран, играют по этим подсказкам
            if s.game.status == WON and not changed:
                move = MSG_WON_POST
            else:
                move = describe_move(s.game, card, found_before) if changed else toast
            prefix = MSG_EXPIRED if expired else None  # подсказка и так показывает, что игра продолжается
            s.revision += 1  # экран будет новый — старые кнопки должны стать неактуальными
            self.storage.save(s)
            out = Outcome(s, ". ".join(filter(None, [prefix, move])) or None, changed=True,
                          alert=s.game.status == WON)
            return await self._deliver(out, deliver)

    async def press_on_screen(self, user_id: int, chat_id: int, message_id: int,
                              action: str, arg: int, revision: int,
                              deliver: Deliver = None, now: float | None = None) -> Outcome:
        """Нажатие в личном экране. Действие применяется, только если ревизия совпадает."""
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
        """/start из кнопки «Играть в боте»: игра экраном в чате с ботом. Там поле видно на любом
        устройстве, в том числе на iPhone, где личный экран в посте не открывается."""
        key = (user_id, chat_id, CHAT_GAME)
        async with self._lock(key):
            s, _, _ = self._load_or_create(key)
            s.revision += 1  # экран будет новым сообщением — старые в чате устаревают
            self.storage.save(s)
            return s

    def remember_ephemeral(self, s: Session, ephemeral_id: int) -> None:
        s.ephemeral_id = ephemeral_id
        self.storage.save(s)

    def cleanup(self) -> int:
        n = self.storage.cleanup(self.ttl)
        for key, lock in list(self._locks.items()):
            if not lock.locked():
                del self._locks[key]
        return n
