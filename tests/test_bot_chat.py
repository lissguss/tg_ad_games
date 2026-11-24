"""Игра в чате с ботом: любая кнопка поста ведёт сюда (/start <игра>), игра — одно сообщение,
которое перерисовывается при каждом нажатии. Так поле видно на любом устройстве: личный экран
прямо в посте Telegram на iPhone показывает через раз."""
import asyncio
import itertools

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import Message, Update, User

from bot import admin, fallback
from bot.config import Config
from games import memory, oplatit_button, phone
from games.memory.service import CHAT_GAME as MEMORY_CHAT_GAME
from games.phone.service import CHAT_GAME as PHONE_CHAT_GAME

ADMIN, PLAYER, CHANNEL = 111, 333, -100
START_MESSAGE = 1


class FakeSession(BaseSession):
    """Отвечает как Telegram: каждое сообщение в личке получает новый id."""

    def __init__(self):
        super().__init__()
        self.calls = []
        self.ids = itertools.count(700)
        self.last_id = None

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        if name == "GetMe":
            return User.model_validate({"id": 1, "is_bot": True, "first_name": "b", "username": "test_bot"})
        self.calls.append(method)
        if name in ("SendRichMessage", "SendMessage"):
            self.last_id = next(self.ids)
            return Message.model_validate({
                "message_id": self.last_id, "date": 0,
                "chat": {"id": method.chat_id, "type": "private"},
            })
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def close(self):
        pass


@pytest.fixture
def env(tmp_path):
    db = str(tmp_path / "game.db")
    config = Config("1:x", CHANNEL, frozenset({ADMIN}), db, 7)
    ms, ps = memory.create_service(db, 86400), phone.create_service(db, 86400)
    oplatit_service = oplatit_button.create_service(db, 86400)
    dp = Dispatcher(config=config, service=ms, phone_service=ps, oplatit_service=oplatit_service)
    dp.include_routers(admin.router, memory.router, phone.router, oplatit_button.router, fallback.router)
    session = FakeSession()
    bot = Bot("1:x", session=session)
    counter = itertools.count(1)

    def feed(update: dict):
        session.calls.clear()
        asyncio.run(dp.feed_update(bot, Update.model_validate({"update_id": next(counter), **update})))
        return session.calls

    def start(payload: str | None, user=PLAYER):
        text = "/start" + (f" {payload}" if payload else "")
        return feed({"message": {
            "message_id": START_MESSAGE, "date": 0, "text": text,
            "chat": {"id": user, "type": "private"},
            "from": {"id": user, "is_bot": False, "first_name": "u"},
        }})

    def press(data: str, user=PLAYER, message_id=None):
        """Нажатие в последнем сообщении с игрой."""
        return feed({"callback_query": {
            "id": "q", "from": {"id": user, "is_bot": False, "first_name": "u"},
            "chat_instance": "0", "data": data,
            "message": {"message_id": message_id or session.last_id, "date": 0,
                        "chat": {"id": user, "type": "private"}},
        }})

    yield start, press, ms, ps, session
    for r in (admin.router, memory.router, phone.router, oplatit_button.router, fallback.router):
        r._parent_router = None


def names(calls):
    return [type(c).__name__ for c in calls]


def test_start_phone_opens_the_game_and_removes_the_command(env):
    start, press, ms, ps, session = env

    calls = start("phone")

    assert names(calls) == ["SendRichMessage", "DeleteMessage"]
    assert int(calls[0].chat_id) == PLAYER
    assert 'data="phone:1:0:1"' in calls[0].rich_message.html  # пост = 0: игра в чате
    assert calls[1].message_id == START_MESSAGE  # команда /start из чата убрана
    s = ps.storage.load(PLAYER, PLAYER, PHONE_CHAT_GAME)
    assert (s.revision, s.ephemeral_id) == (1, session.last_id)


def test_phone_presses_redraw_the_same_message(env):
    start, press, ms, ps, session = env
    start("phone")
    game_message = session.last_id

    calls = press("phone:1:0:1")

    assert names(calls) == ["EditMessageText", "AnswerCallbackQuery"]
    assert (int(calls[0].chat_id), calls[0].message_id) == (PLAYER, game_message)
    assert "phone:del:0:2" in calls[0].rich_message.html
    assert ps.storage.load(PLAYER, PLAYER, PHONE_CHAT_GAME).game.current == "1"


def test_whole_phone_game_in_the_bot_chat(env):
    start, press, ms, ps, session = env
    start("phone")
    revision = 1
    for d in phone.logic.PASSWORD:
        press(f"phone:{d}:0:{revision}")
        revision += 1
    press(f"phone:ok:0:{revision}")

    s = ps.storage.load(PLAYER, PLAYER, PHONE_CHAT_GAME)
    assert s.game.status == phone.logic.WON
    assert ps.storage.stats()["wins"] == 1


def test_second_start_keeps_progress_and_leaves_one_game_message(env):
    start, press, ms, ps, session = env
    start("phone")
    first_message = session.last_id
    press("phone:7:0:1")

    calls = start("phone")  # снова нажали кнопку в посте

    assert names(calls) == ["SendRichMessage", "DeleteMessage", "DeleteMessage"]
    assert 'data="phone:del:0:3"' in calls[0].rich_message.html  # набранная 7 на месте
    assert sorted(c.message_id for c in calls[1:]) == sorted([first_message, START_MESSAGE])
    assert ps.storage.load(PLAYER, PLAYER, PHONE_CHAT_GAME).ephemeral_id == session.last_id


def test_start_memory_and_open_a_card_in_the_bot_chat(env):
    start, press, ms, ps, session = env
    assert names(start("memory")) == ["SendRichMessage", "DeleteMessage"]

    calls = press("c:3:0:1")

    assert names(calls) == ["EditMessageText", "AnswerCallbackQuery"]
    s = ms.storage.load(PLAYER, PLAYER, MEMORY_CHAT_GAME)
    assert s.game.opened == [3] and s.ephemeral_id == session.last_id


def test_memory_flip_redraws_the_bot_chat_message():
    from games.memory.handlers import _edit_screen
    from games.memory.logic import Game, generate_layout
    from games.memory.storage import Session

    session = FakeSession()
    bot = Bot("1:x", session=session)
    in_chat = Session(PLAYER, PLAYER, MEMORY_CHAT_GAME, Game(layout=generate_layout()))
    in_post = Session(PLAYER, CHANNEL, 5, Game(layout=generate_layout()))

    asyncio.run(_edit_screen(bot, in_chat, 700))
    asyncio.run(_edit_screen(bot, in_post, 900))

    assert names(session.calls) == ["EditMessageText", "EditEphemeralMessageText"]


def test_plain_start_and_unknown_payload_keep_the_greeting(env):
    start, press, ms, ps, session = env
    for payload in (None, "nothing"):
        calls = [c for c in start(payload) if names([c]) != ["GetChat"]]
        assert names(calls) == ["SendMessage"]
        assert "Игра проходит в нашем канале" in calls[0].text
    # админу — меню команд и список игр
    assert names(start(None, user=ADMIN)) == ["SetMyCommands", "SendMessage"]


def test_bot_chat_games_are_not_counted_as_posts(env):
    start, press, ms, ps, session = env
    start("memory")
    assert "постов с играми: 0" in memory.stats(ms)
