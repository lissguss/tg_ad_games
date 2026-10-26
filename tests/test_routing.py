"""Кнопки одной игры не попадают в другую: прогоняем update через настоящий Dispatcher
с теми же роутерами, что в bot/__main__.py, но с подменённой отправкой в Telegram."""
import asyncio
import re

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import Message, Update, User

from bot import admin, fallback
from bot.config import Config
from games import memory, phone

CHAT, USER, MEMORY_POST, PHONE_POST = -100, 111, 5, 6


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        if type(method).__name__ == "GetMe":  # имя бота для ссылки «Играть в боте»
            return User.model_validate({"id": 1, "is_bot": True, "first_name": "b", "username": "test_bot"})
        self.calls.append(method)
        if type(method).__name__ == "SendRichMessage":
            return Message.model_validate({
                "message_id": 0, "date": 0, "chat": {"id": CHAT, "type": "channel"},
                "ephemeral_message_id": 900 + len(self.calls),
            })
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def close(self):
        pass


@pytest.fixture
def env(tmp_path):
    db = str(tmp_path / "game.db")
    config = Config("1:x", CHAT, frozenset({USER}), db, 7)
    ms, ps = memory.create_service(db, 86400), phone.create_service(db, 86400)
    dp = Dispatcher(config=config, service=ms, phone_service=ps)
    dp.include_routers(admin.router, memory.router, phone.router, fallback.router)
    session = FakeSession()
    bot = Bot("1:x", session=session)
    counter = iter(range(1, 10**6))

    def press(data, post_id, ephemeral_id=None):
        msg = {"message_id": 0 if ephemeral_id else post_id, "date": 0,
               "chat": {"id": CHAT, "type": "channel"}}
        if ephemeral_id:
            msg["ephemeral_message_id"] = ephemeral_id
        update = Update.model_validate({"update_id": next(counter), "callback_query": {
            "id": "q", "from": {"id": USER, "is_bot": False, "first_name": "u"},
            "chat_instance": "0", "data": data, "message": msg,
        }})
        session.calls.clear()
        asyncio.run(dp.feed_update(bot, update))
        return [type(c).__name__ for c in session.calls], session.calls

    yield press, ms, ps
    for r in (admin.router, memory.router, phone.router, fallback.router):
        r._parent_router = None  # роутеры модульные — отцепляем, чтобы подключить в следующем тесте


def test_memory_patterns_do_not_match_phone():
    for data in ("phone:1", "phone:del", "phone:ok", "phone:1:6:3", "phone:new:6:3"):
        assert not re.match(memory.render.CALLBACK_RE, data)
    for data in ("p:0", "p:11", "c:7:2:12", "new:-:2:20"):
        assert re.match(memory.render.CALLBACK_RE, data)


def test_phone_post_press_opens_the_bot_and_does_not_touch_memory(env):
    press, ms, ps = env
    for data in ("phone:1", "phone:del", "phone:ok", "phone:-"):
        names, calls = press(data, PHONE_POST)
        assert names == ["AnswerCallbackQuery"]
        assert calls[0].url == "https://t.me/test_bot?start=phone"
    assert ps.storage.db.execute("SELECT COUNT(*) FROM phone_sessions").fetchone()[0] == 0
    assert ms.storage.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert ms.storage.db.execute("SELECT COUNT(*) FROM processed_updates").fetchone()[0] == 0


def test_memory_post_press_opens_the_bot_and_does_not_touch_phone(env):
    press, ms, ps = env
    names, calls = press("p:3", MEMORY_POST)
    assert names == ["AnswerCallbackQuery"]
    assert calls[0].url == "https://t.me/test_bot?start=memory"
    assert ms.storage.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert ps.storage.db.execute("SELECT COUNT(*) FROM phone_sessions").fetchone()[0] == 0
    assert ps.storage.db.execute("SELECT COUNT(*) FROM phone_processed_updates").fetchone()[0] == 0


def opened_screen(service, post_id, ephemeral_id):
    """Личный экран, открытый в посте раньше (до перехода игры в чат с ботом)."""
    s = asyncio.run(service.open(USER, CHAT, post_id))
    service.remember_ephemeral(s, ephemeral_id)
    return s


def test_old_personal_screens_keep_working_in_turns(env):
    press, ms, ps = env
    ph = opened_screen(ps, PHONE_POST, 901)
    m = opened_screen(ms, MEMORY_POST, 902)
    names, _ = press(f"phone:7:{PHONE_POST}:{ph.revision}", PHONE_POST, ephemeral_id=901)
    assert names == ["EditEphemeralMessageText", "AnswerCallbackQuery"]
    names, _ = press(f"c:1:{MEMORY_POST}:{m.revision}", MEMORY_POST, ephemeral_id=902)
    assert names == ["EditEphemeralMessageText", "AnswerCallbackQuery"]
    assert ps.storage.load(USER, CHAT, PHONE_POST).game.current == "7"
    assert ms.storage.load(USER, CHAT, MEMORY_POST).game.opened == [1]
    # ход в мемори не сдвинул ревизию телефона и наоборот
    assert ps.storage.load(USER, CHAT, PHONE_POST).revision == ph.revision + 1


def test_unknown_button_is_answered(env):
    press, ms, ps = env
    names, calls = press("e:2", MEMORY_POST)
    assert names == ["AnswerCallbackQuery"]
    assert ms.storage.db.execute("SELECT COUNT(*) FROM processed_updates").fetchone()[0] == 0


@pytest.fixture
def command(env, tmp_path):
    press, ms, ps = env
    session = FakeSession()
    bot = Bot("1:x", session=session)
    dp = Dispatcher(config=Config("1:x", CHAT, frozenset({USER}), str(tmp_path / "game.db"), 7),
                    service=ms, phone_service=ps)
    for r in (admin.router, memory.router, phone.router, fallback.router):
        r._parent_router = None
    dp.include_routers(admin.router, memory.router, phone.router, fallback.router)
    counter = iter(range(10**6, 2 * 10**6))

    def send(text, user=USER):
        update = Update.model_validate({"update_id": next(counter), "message": {
            "message_id": 1, "date": 0, "text": text,
            "chat": {"id": user, "type": "private"},
            "from": {"id": user, "is_bot": False, "first_name": "u"},
        }})
        session.calls.clear()
        asyncio.run(dp.feed_update(bot, update))
        return session.calls

    return send, ps


def test_admin_publishes_both_games_to_channel(command):
    send, ps = command
    for cmd, mark in (("/memory", "p:0"), ("/post_phone", "phone:0")):
        post, reply = send(cmd)
        assert type(post).__name__ == "SendRichMessage" and post.chat_id == CHAT
        assert mark in post.rich_message.html
        assert "опубликована" in reply.text
    assert ps.storage.stats()["posts"] == 1


def test_non_admin_cannot_publish(command):
    send, _ = command
    assert send("/post_phone", user=999) == []
    assert send("/memory", user=999) == []
    assert send("/stats", user=999) == []


def test_games_and_stats(command):
    send, _ = command
    (reply,) = send("/games")
    assert "/memory" in reply.text and "/post_phone" in reply.text and "/stats" in reply.text
    (reply,) = send("/stats")
    assert "«Мемори»" in reply.text and "«Взломайте телефон»" in reply.text


def test_menu_only_for_admins():
    session = FakeSession()
    bot = Bot("1:x", session=session)
    config = Config("1:x", CHAT, frozenset({USER, 222}), ":memory:", 7)
    asyncio.run(admin.setup_menu(bot, config))
    assert sorted(c.scope.chat_id for c in session.calls) == sorted([USER, 222])
    names = [cmd.command for cmd in session.calls[0].commands]
    assert names == ["memory", "post_phone", "stats", "games"]


def test_view_cell_on_screen_does_nothing(env):
    press, ms, ps = env
    s = opened_screen(ps, PHONE_POST, 903)
    names, _ = press("phone:-", PHONE_POST, ephemeral_id=903)
    assert names == ["AnswerCallbackQuery"]
    assert ps.storage.load(USER, CHAT, PHONE_POST).revision == s.revision
