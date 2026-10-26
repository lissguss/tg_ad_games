"""Кнопка «🎮 Играть в боте»: /start <игра> открывает игру в чате с ботом, нажатия там
перерисовывают обычное сообщение (а не личный экран). На iPhone личный экран в посте
не открывается, а такое сообщение видно."""
import asyncio

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import Message, Update

from bot import admin, fallback
from bot.config import Config
from games import memory, phone
from games.memory.service import CHAT_GAME as MEMORY_CHAT_GAME
from games.phone.service import CHAT_GAME as PHONE_CHAT_GAME

ADMIN, PLAYER, CHANNEL = 111, 333, -100
BOT_MESSAGE = 700


class FakeSession(BaseSession):
    """Отвечает как Telegram: в личке — обычное сообщение, в канале — личный экран."""

    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if type(method).__name__ == "SendRichMessage":
            private = int(method.chat_id) > 0
            return Message.model_validate({
                "message_id": BOT_MESSAGE if private else 0, "date": 0,
                "chat": {"id": method.chat_id, "type": "private" if private else "channel"},
                **({} if private else {"ephemeral_message_id": 900}),
            })
        if type(method).__name__ == "SendMessage":
            return Message.model_validate({
                "message_id": 1, "date": 0, "chat": {"id": method.chat_id, "type": "private"},
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
    dp = Dispatcher(config=config, service=ms, phone_service=ps)
    dp.include_routers(admin.router, memory.router, phone.router, fallback.router)
    session = FakeSession()
    bot = Bot("1:x", session=session)
    counter = iter(range(1, 10**6))

    def feed(update: dict):
        session.calls.clear()
        asyncio.run(dp.feed_update(bot, Update.model_validate({"update_id": next(counter), **update})))
        return session.calls

    def start(payload: str | None, user=PLAYER):
        text = "/start" + (f" {payload}" if payload else "")
        return feed({"message": {
            "message_id": 1, "date": 0, "text": text,
            "chat": {"id": user, "type": "private"},
            "from": {"id": user, "is_bot": False, "first_name": "u"},
        }})

    def press_in_chat(data: str, user=PLAYER):
        return feed({"callback_query": {
            "id": "q", "from": {"id": user, "is_bot": False, "first_name": "u"},
            "chat_instance": "0", "data": data,
            "message": {"message_id": BOT_MESSAGE, "date": 0, "chat": {"id": user, "type": "private"}},
        }})

    yield start, press_in_chat, ms, ps
    for r in (admin.router, memory.router, phone.router, fallback.router):
        r._parent_router = None


def names(calls):
    return [type(c).__name__ for c in calls]


def test_posts_link_to_the_bot_chat():
    for game in (phone, memory):
        html = game.post_html("alfa_games_bot")
        button = (f'<tg-button type="url" url="https://t.me/alfa_games_bot?start={game.START}">'
                  "🎮 Играть в боте</tg-button>")
        assert button in html


def test_start_phone_opens_the_game_in_the_bot_chat(env):
    start, press, ms, ps = env

    calls = start("phone")

    assert names(calls) == ["SendRichMessage"]
    assert int(calls[0].chat_id) == PLAYER
    assert 'data="phone:1:0:1"' in calls[0].rich_message.html  # пост = 0: игра в чате
    s = ps.storage.load(PLAYER, PLAYER, PHONE_CHAT_GAME)
    assert (s.revision, s.ephemeral_id) == (1, BOT_MESSAGE)


def test_phone_presses_in_the_bot_chat_edit_the_message(env):
    start, press, ms, ps = env
    start("phone")

    calls = press("phone:1:0:1")

    assert names(calls) == ["EditMessageText", "AnswerCallbackQuery"]
    assert (int(calls[0].chat_id), calls[0].message_id) == (PLAYER, BOT_MESSAGE)
    assert "phone:del:0:2" in calls[0].rich_message.html
    assert ps.storage.load(PLAYER, PLAYER, PHONE_CHAT_GAME).game.current == "1"


def test_whole_phone_game_in_the_bot_chat(env):
    start, press, ms, ps = env
    start("phone")
    revision = 1
    for d in phone.logic.PASSWORD:
        press(f"phone:{d}:0:{revision}")
        revision += 1
    press(f"phone:ok:0:{revision}")

    s = ps.storage.load(PLAYER, PLAYER, PHONE_CHAT_GAME)
    assert s.game.status == phone.logic.WON
    assert ps.storage.stats()["wins"] == 1


def test_start_memory_and_open_a_card_in_the_bot_chat(env):
    start, press, ms, ps = env
    assert names(start("memory")) == ["SendRichMessage"]

    calls = press("c:3:0:1")

    assert names(calls) == ["EditMessageText", "AnswerCallbackQuery"]
    s = ms.storage.load(PLAYER, PLAYER, MEMORY_CHAT_GAME)
    assert s.game.opened == [3] and s.ephemeral_id == BOT_MESSAGE


def test_bot_chat_game_keeps_progress_between_starts(env):
    start, press, ms, ps = env
    start("phone")
    press("phone:7:0:1")

    calls = start("phone")

    assert 'data="phone:del:0:3"' in calls[0].rich_message.html  # набранная 7 на месте
    assert ps.storage.load(PLAYER, PLAYER, PHONE_CHAT_GAME).game.current == "7"


def test_memory_flip_redraws_the_bot_chat_message():
    from games.memory.handlers import _edit_screen
    from games.memory.logic import Game, generate_layout
    from games.memory.storage import Session

    session = FakeSession()
    bot = Bot("1:x", session=session)
    in_chat = Session(PLAYER, PLAYER, MEMORY_CHAT_GAME, Game(layout=generate_layout()))
    in_post = Session(PLAYER, CHANNEL, 5, Game(layout=generate_layout()))

    asyncio.run(_edit_screen(bot, in_chat, BOT_MESSAGE))
    asyncio.run(_edit_screen(bot, in_post, 900))

    assert names(session.calls) == ["EditMessageText", "EditEphemeralMessageText"]


def test_plain_start_and_unknown_payload_keep_the_greeting(env):
    start, press, ms, ps = env
    for payload in (None, "nothing"):
        calls = start(payload)
        assert names(calls) == ["SendMessage"]
        assert "Играть можно прямо в посте" in calls[0].text
    assert names(start(None, user=ADMIN)) == ["SendMessage"]  # админу — список игр


def test_bot_chat_games_are_not_counted_as_posts(env):
    start, press, ms, ps = env
    start("memory")
    assert "постов с играми: 0" in memory.stats(ms)
