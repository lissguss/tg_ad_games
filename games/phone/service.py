"""Обработка нажатий: сессии, ревизии, блокировки. Без вызовов Telegram — их делает handlers.py."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .logic import CODE_LENGTH, HIT, LOST, MAX_ATTEMPTS, NEAR, WON, Game
from .storage import Key, Session, Storage

MSG_STALE_SCREEN = "Экран обновился"
MSG_RESTORED = "Ваша игра восстановлена"
MSG_EXPIRED = "Прошлая игра устарела — начинаем новую"
MSG_ENTER_FIRST = "Сначала наберите цифры"
MSG_NEW_GAME = "Новая игра"

# Подсказки при нажатиях в общем посте. На iPhone Telegram не показывает личный экран,
# и игрок проходит игру по ним: что набрано, как прошла проверка, сколько осталось попыток.
POST_WON = "🎉 Телефон разблокирован, запоминайте дату 📆"
POST_LOST = "🔒 Попытки закончились. Нажмите любую цифру — начнём заново."
POST_LEGEND = "🟩 на месте · 🟦 есть, но не там"
MARK_EMOJI = {HIT: "🟩", NEAR: "🟦"}
MISS_EMOJI = "⬜"
HIDDEN = "•"
ALERT_MAX = 200  # предел Telegram для answerCallbackQuery

DIGITS = "0123456789"
DELETE, CHECK, NEW_GAME = "del", "ok", "new"
VIEW = "-"  # клетка экрана или попытки: только открыть игру, ничего не менять
ACTIONS = (*DIGITS, DELETE, CHECK, NEW_GAME, VIEW)

# Показ экрана в Telegram. Вызывается под блокировкой сессии, чтобы экраны не обгоняли друг друга.
Deliver = Callable[[Session], Awaitable[None]] | None


@dataclass
class Outcome:
    session: Session
    toast: str | None = None   # всплывающее уведомление (answerCallbackQuery)
    changed: bool = False      # изменилось ли состояние (нужна перерисовка)
    alert: bool = False        # показать уведомление окном, а не всплывающей строкой


def typed(game: Game) -> str:
    """Набранные цифры, на пустых местах точки: «1 7 • • • •»."""
    return " ".join(game.current[i] if i < len(game.current) else HIDDEN for i in range(CODE_LENGTH))


def _history_lines(game: Game) -> list[str]:
    return [a.guess + " " + "".join(MARK_EMOJI.get(m, MISS_EMOJI) for m in a.marks)
            for a in game.attempts]


def _fit(head: str, lines: list[str], tail: list[str]) -> str:
    """Окно уведомления не длиннее ALERT_MAX: если не помещается, старые попытки отбрасываются."""
    while lines:
        text = "\n".join([head, *lines, *tail])
        if len(text) <= ALERT_MAX:
            return text
        lines = lines[1:]
    return "\n".join([head, *tail])


def describe(game: Game, checked: bool) -> str:
    """Что показать игроку после нажатия в посте."""
    if game.status == WON:
        return _fit(POST_WON, _history_lines(game), [])
    if game.status == LOST:
        return _fit(POST_LOST, _history_lines(game), [])
    if checked:
        left = MAX_ATTEMPTS - len(game.attempts)
        head = f"Попытка {len(game.attempts)} из {MAX_ATTEMPTS}: не подошло"
        return _fit(head, _history_lines(game), [POST_LEGEND, f"Осталось попыток: {left}"])
    return f"Попытка {game.attempt_number} из {MAX_ATTEMPTS}. Набрано: {typed(game)}"


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

    async def press_on_post(self, user_id: int, chat_id: int, message_id: int, action: str,
                            deliver: Deliver = None) -> Outcome:
        """Нажатие в общем посте: тот же ход, что в личном экране, плюс подсказка словами.
        Личный экран открывается там, где Telegram его показывает (Android, компьютер)."""
        key = (user_id, chat_id, message_id)
        async with self._lock(key):
            s, created, expired = self._load_or_create(key)
            prefix = MSG_EXPIRED if expired else None
            if s.game.status == LOST and action in DIGITS:  # «Играть снова» в посте нет
                s = Session(*key, Game(), revision=s.revision, ephemeral_id=s.ephemeral_id)
                prefix = MSG_NEW_GAME
            error = self._apply(s, action) if not s.game.finished else None
            checked = action == CHECK and error is None
            move = f"{error}. Набрано: {typed(s.game)}" if error else describe(s.game, checked)
            s.revision += 1  # экран будет новый — старые кнопки должны стать неактуальными
            self.storage.save(s)
            toast = ". ".join(filter(None, [prefix, move]))
            out = Outcome(s, toast, changed=True, alert=checked or s.game.finished)
            return await self._deliver(out, deliver)

    async def press_on_screen(self, user_id: int, chat_id: int, message_id: int,
                              action: str, revision: int, deliver: Deliver = None) -> Outcome:
        """Нажатие в личном экране. Действие применяется, только если ревизия совпадает."""
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
        s.ephemeral_id = ephemeral_id
        self.storage.save(s)

    def cleanup(self) -> int:
        n = self.storage.cleanup(self.ttl)
        for key, lock in list(self._locks.items()):
            if not lock.locked():
                del self._locks[key]
        return n
