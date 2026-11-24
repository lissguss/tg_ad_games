"""Доставка двух экранов по очереди для каждого читателя и исходного поста."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from . import render
from .storage import Content, Post, Storage

Key = tuple[int, int, int]
Deliver = Callable[[Post], Awaitable[None]]


@dataclass
class _Lock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0  # включая тех, кто ещё ждёт acquire()


class GameService:
    def __init__(self, storage: Storage):
        self.storage = storage
        self._locks: dict[Key, _Lock] = {}

    @asynccontextmanager
    async def _locked(self, key: Key):
        entry = self._locks.setdefault(key, _Lock())
        entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.users -= 1
            if entry.users == 0:
                del self._locks[key]

    def store_post(self, chat_id: int, message_id: int, intro: str, article_html: str,
                   *, published: bool = False) -> None:
        self.storage.add_post(chat_id, message_id, Content(intro, article_html), published=published)

    def get_post(self, chat_id: int, message_id: int) -> Post | None:
        return self.storage.get_post(chat_id, message_id)

    def _load_or_create(self, chat_id: int, message_id: int, *, published: bool) -> Post:
        """Как в остальных играх: первое нажатие работает без регистрации поста."""
        post = self.get_post(chat_id, message_id)
        if post is not None:
            return post
        intro, article_html = render.load_content()
        self.store_post(chat_id, message_id, intro, article_html, published=published)
        return Post(chat_id, message_id, intro, article_html, published)

    async def display(self, user_id: int, chat_id: int, message_id: int, action: str,
                      update_id: int, deliver: Deliver, *, published: bool = False) -> bool:
        """Загрузить или создать состояние и показать экран. Ошибку доставки можно повторить."""
        async with self._locked((user_id, chat_id, message_id)):
            post = self._load_or_create(chat_id, message_id, published=published)
            if self.storage.processed(update_id):
                return True
            await deliver(post)
            self.storage.log_event(update_id, user_id, chat_id, message_id, action)
            return True
