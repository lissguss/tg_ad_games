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
        if type(method).__name__ == "SendMessage":
            return Message.model_validate({
                "message_id": 500 + len(self.calls), "date": 0, "text": method.text,
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
    config = Config("1:x", CHAT, frozenset({USER}), db, 7)
    ms, ps = memory.create_service(db, 86400), phone.create_service(db, 86400)
    dp = Dispatcher(config=config, service=ms, phone_service=ps)
    dp.include_routers(admin.router, memory.router, phone.router, fallback.router)
    session = FakeSession()
    bot = Bot("1:x", session=session)
    counter = iter(range(1, 10**6))

    def press(data, post_id, ephemeral_id=None, chat=(CHAT, "channel")):
        msg = {"message_id": 0 if ephemeral_id else post_id, "date": 0,
               "chat": {"id": chat[0], "type": chat[1]}}
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


def test_phone_post_press_opens_screen_in_post_and_does_not_touch_memory(env):
    press, ms, ps = env
    names, calls = press("phone:1", PHONE_POST)
    assert names == ["SendRichMessage", "AnswerCallbackQuery"]
    params = calls[0].ephemeral_message_parameters
    assert (params.receiver_user_id, params.replace_callback_query_message) == (USER, True)
    s = ps.storage.load(USER, CHAT, PHONE_POST)
    assert s.game.current == "1"  # цифра из поста сразу введена
    assert s.ephemeral_id == 901 and f'data="phone:del:{PHONE_POST}:{s.revision}"' in calls[0].rich_message.html
    for data in ("phone:del", "phone:ok", "phone:open", "phone:-"):  # только открывают экран
        names, _ = press(data, PHONE_POST)
        assert names == ["SendRichMessage", "AnswerCallbackQuery"]
    assert ps.storage.load(USER, CHAT, PHONE_POST).game.current == "1"
    assert ms.storage.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert ms.storage.db.execute("SELECT COUNT(*) FROM processed_updates").fetchone()[0] == 0


def test_memory_post_press_opens_screen_in_post_and_does_not_touch_phone(env):
    press, ms, ps = env
    names, calls = press("p:3", MEMORY_POST)
    assert names == ["SendRichMessage", "AnswerCallbackQuery"]
    s = ms.storage.load(USER, CHAT, MEMORY_POST)
    assert s.game.opened == [3] and s.ephemeral_id == 901
    assert ps.storage.db.execute("SELECT COUNT(*) FROM phone_sessions").fetchone()[0] == 0
    assert ps.storage.db.execute("SELECT COUNT(*) FROM phone_processed_updates").fetchone()[0] == 0


def test_post_press_falls_back_to_bot_chat_if_screen_fails(env, monkeypatch):
    press, ms, ps = env

    async def fail(*args, **kwargs):
        raise RuntimeError("личный экран не открылся")

    monkeypatch.setattr("games.botchat.show_in_post", fail)
    for data, start in (("phone:1", "phone"), ("p:3", "memory")):
        names, calls = press(data, PHONE_POST)
        assert names == ["AnswerCallbackQuery"]
        assert calls[0].url == f"https://t.me/test_bot?start={start}"


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

    def feed(update):
        session.calls.clear()
        asyncio.run(dp.feed_update(bot, Update.model_validate({"update_id": next(counter), **update})))
        return session.calls

    def send(text, user=USER):
        return feed({"message": {
            "message_id": 1, "date": 0, "text": text,
            "chat": {"id": user, "type": "private"},
            "from": {"id": user, "is_bot": False, "first_name": "u"},
        }})

    def click(data, message_id, user=USER):
        """Нажатие «Да» / «Нет» под вопросом в личке админа."""
        return feed({"callback_query": {
            "id": "q", "from": {"id": user, "is_bot": False, "first_name": "u"},
            "chat_instance": "0", "data": data,
            "message": {"message_id": message_id, "date": 1, "text": admin.CONFIRM_QUESTION,
                        "chat": {"id": USER, "type": "private"}},
        }})

    admin._pending.clear()
    send.click = click
    return send, ps


def ask(send, cmd):
    """Команда публикации: пост и вопрос приходят админу в личку. Возвращает (пост, id вопроса)."""
    preview, get_chat, question = send(cmd)
    assert type(preview).__name__ == "SendRichMessage" and preview.chat_id == USER
    assert type(get_chat).__name__ == "GetChat" and get_chat.chat_id == CHAT
    # имя канала FakeSession не отдаёт — в вопросе id канала из настроек
    assert question.text == f"Вы уверены, что хотите опубликовать этот пост в канале {CHAT}?"
    buttons = [b.callback_data for b in question.reply_markup.inline_keyboard[0]]
    assert buttons == [admin.CONFIRM_YES, admin.CONFIRM_NO]
    return preview, 500 + 3  # id вопроса из FakeSession: третий вызов


def test_admin_publishes_both_games_to_channel_after_yes(command):
    send, ps = command
    for cmd, mark in (("/memory", "p:0"), ("/post_phone", "phone:0")):
        preview, question_id = ask(send, cmd)
        assert mark in preview.rich_message.html
        post, _, edited, answer = send.click(admin.CONFIRM_YES, question_id)
        assert type(post).__name__ == "SendRichMessage" and post.chat_id == CHAT
        assert post.rich_message.html == preview.rich_message.html  # уходит ровно то, что показали
        assert edited.text.endswith(f"опубликована в канал {CHAT}.")
    assert ps.storage.stats()["posts"] == 1


def test_no_does_not_publish(command):
    send, ps = command
    _, question_id = ask(send, "/post_phone")
    calls = send.click(admin.CONFIRM_NO, question_id)
    assert [type(c).__name__ for c in calls] == ["EditMessageText", "AnswerCallbackQuery"]
    assert "не опубликован" in calls[0].text
    assert ps.storage.stats()["posts"] == 0


def test_second_yes_does_not_publish_again(command):
    send, ps = command
    _, question_id = ask(send, "/post_phone")
    send.click(admin.CONFIRM_YES, question_id)
    calls = send.click(admin.CONFIRM_YES, question_id)
    assert "SendRichMessage" not in [type(c).__name__ for c in calls]
    assert ps.storage.stats()["posts"] == 1


def test_non_admin_cannot_confirm(command):
    send, ps = command
    _, question_id = ask(send, "/post_phone")
    calls = send.click(admin.CONFIRM_YES, question_id, user=999)
    assert [type(c).__name__ for c in calls] == ["AnswerCallbackQuery"]
    assert ps.storage.stats()["posts"] == 0
    send.click(admin.CONFIRM_YES, question_id)  # вопрос остался в силе для админа
    assert ps.storage.stats()["posts"] == 1


def test_non_admin_start_points_to_the_channel(command):
    send, _ = command
    get_chat, reply = send("/start", user=999)  # имя канала спрашивается у Telegram
    assert type(get_chat).__name__ == "GetChat" and get_chat.chat_id == CHAT
    assert reply.text.startswith("Игра проходит в нашем канале")


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


@pytest.mark.parametrize("chat", [(-200, "channel"), (-300, "supergroup"), (333, "private")])
def test_forwarded_post_opens_screen_where_it_was_pressed(env, chat):
    """Пост пересылается из лички с ботом куда угодно: личный экран открывается там, где нажали."""
    press, ms, ps = env
    for data in ("p:3", "phone:1"):
        names, calls = press(data, PHONE_POST, chat=chat)
        assert names == ["SendRichMessage", "AnswerCallbackQuery"]
        assert int(calls[0].chat_id) == chat[0]
    assert ps.storage.load(USER, chat[0], PHONE_POST).game.current == "1"
    assert ms.storage.load(USER, chat[0], PHONE_POST).game.opened == [3]


def test_screen_button_outside_screen_is_stale(env):
    press, ms, ps = env
    names, calls = press(f"phone:1:{PHONE_POST}:1", PHONE_POST)
    assert names == ["AnswerCallbackQuery"] and "устарел" in calls[0].text


def test_post_cells_differ_from_screen_cells():
    assert 'data="phone:open"' in phone.render.public_post()
    assert 'data="phone:-"' not in phone.render.public_post()


def test_question_names_the_channel(command, monkeypatch):
    send, _ = command
    monkeypatch.setattr(admin, "_channel_mention", "@test_channel")
    preview, question = send("/post_phone")  # имя уже известно — Telegram не спрашиваем
    assert question.text == "Вы уверены, что хотите опубликовать этот пост в канале @test_channel?"
