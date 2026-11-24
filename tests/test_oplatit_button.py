"""Публикация и персональное раскрытие лонгрида через настоящий Dispatcher."""
import asyncio
import itertools
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.types import FSInputFile, InputMediaAnimation, Message, Update

from bot import admin, fallback
from bot.config import Config
from games import memory, oplatit_button, phone

ADMIN, READER, OTHER_READER, CHANNEL, POST = 111, 333, 444, -100, 7
ROUTERS = (admin.router, memory.router, phone.router, oplatit_button.router, fallback.router)
ORIGINAL_INTRO = "Исходная подводка"
ORIGINAL_ARTICLE = "<h1>Исходный материал</h1><p>Весь текст до конца.</p>"


class Markup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


def names(calls):
    return [type(call).__name__ for call in calls]


def assert_article_cover(message):
    markup = Markup(message.html)
    assert markup.tags[0][0] == "video"
    assert markup.tags[0][1]["src"] == "tg://video?id=oplatit_cover"
    assert len(message.media) == 1
    cover = message.media[0]
    assert cover.id == "oplatit_cover"
    assert isinstance(cover.media, InputMediaAnimation)
    assert isinstance(cover.media.media, FSInputFile)
    assert Path(cover.media.media.path) == oplatit_button.render.COVER_FILE
    assert Path(cover.media.media.path).is_file()


def assert_no_cover(message):
    assert not message.media
    assert not any(tag == "video" for tag, _ in Markup(message.html).tags)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.ids = itertools.count(500)
        self.last_message = None
        self.fail_next = None
        self.on_send = None

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append(method)
        if self.fail_next == name:
            self.fail_next = None
            raise TelegramNetworkError(method=method, message="Соединение прервано")
        if (name == "SendRichMessage" and method.ephemeral_message_parameters is not None
                and int(method.chat_id) > 0):
            # Как в реальном журнале: личка не принимает такой способ открытия экрана.
            raise TelegramBadRequest(method=method, message="Bad Request: PEER_ID_INVALID")
        if name == "GetChat":
            return SimpleNamespace(username="test_channel", title="Тестовый канал")
        if name in ("SendMessage", "SendRichMessage"):
            if self.on_send is not None:
                self.on_send(method)
            message_id = next(self.ids)
            ephemeral = name == "SendRichMessage" and method.ephemeral_message_parameters is not None
            self.last_message = Message.model_validate({
                "message_id": 0 if ephemeral else message_id,
                "date": 1,
                "chat": {"id": method.chat_id,
                         "type": "private" if int(method.chat_id) > 0 else "channel"},
                **({"ephemeral_message_id": message_id} if ephemeral else {}),
            })
            return self.last_message
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def close(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "game.db")
    config = Config("1:x", CHANNEL, frozenset({ADMIN}), db_path, 7)
    memory_service = memory.create_service(db_path, 86400)
    phone_service = phone.create_service(db_path, 86400)
    service = oplatit_button.create_service(db_path, 86400)
    dp = Dispatcher(config=config, service=memory_service, phone_service=phone_service,
                    oplatit_service=service)
    dp.include_routers(*ROUTERS)
    session = FakeSession()
    bot = Bot("1:x", session=session)
    counter = itertools.count(1)
    monkeypatch.setattr(admin, "_channel_mention", "@test_channel")
    admin._pending.clear()

    def feed(update, update_id=None):
        session.calls.clear()
        asyncio.run(dp.feed_update(bot, Update.model_validate({
            "update_id": next(counter) if update_id is None else update_id, **update,
        })))
        return list(session.calls)

    def press(data, *, user=READER, chat_id=CHANNEL, message_id=POST,
              ephemeral_id=None, update_id=None):
        msg = {"message_id": 0 if ephemeral_id else message_id, "date": 1,
               "chat": {"id": chat_id, "type": "private" if chat_id > 0 else "channel"}}
        if ephemeral_id is not None:
            msg["ephemeral_message_id"] = ephemeral_id
        return feed({"callback_query": {
            "id": f"q-{update_id or next(counter)}",
            "from": {"id": user, "is_bot": False, "first_name": "Читатель"},
            "chat_instance": "0", "data": data, "message": msg,
        }}, update_id=update_id)

    def send(text, user=ADMIN):
        return feed({"message": {
            "message_id": 1, "date": 1, "text": text,
            "chat": {"id": user, "type": "private"},
            "from": {"id": user, "is_bot": False, "first_name": "Админ"},
        }})

    yield SimpleNamespace(service=service, session=session, press=press, send=send,
                          memory=memory_service, phone=phone_service, dispatcher=dp, bot=bot)
    admin._pending.clear()
    for router in ROUTERS:
        router._parent_router = None


def store_post(env, *, chat_id=CHANNEL, post_id=POST):
    env.service.store_post(chat_id, post_id, ORIGINAL_INTRO, ORIGINAL_ARTICLE,
                           published=chat_id < 0)


def test_first_screen_has_only_intro_and_one_pay_button():
    html = oplatit_button.post_html()
    markup = Markup(html)
    buttons = [attrs for tag, attrs in markup.tags if tag == "tg-button"]
    assert [chunk.strip() for chunk in markup.text if chunk.strip()] == [
        "Редкий шанс нажать кнопку оплаты и не потратить ни рубля 😏", "Оплатить",
    ]
    assert len(buttons) == 1
    assert buttons[0] == {"type": "callback_data", "style": "primary", "data": "oplatit_button:open"}
    assert [tag for tag, _ in markup.tags].count("tg-button-row") == 1
    assert not any(tag == "details" for tag, _ in markup.tags)
    assert not any(tag == "video" for tag, _ in markup.tags)


def test_whole_article_has_six_collapsed_sections_and_cancel_button_at_the_end():
    _, source = oplatit_button.render.load_content()
    html = oplatit_button.render.article_screen(source, POST)
    markup = Markup(html)
    details = [attrs for tag, attrs in markup.tags if tag == "details"]
    assert len(details) == 6 and all("open" not in attrs for attrs in details)
    summaries = ["".join(Markup(summary).text) for summary in
                 re.findall(r"<summary>(.*?)</summary>", html, re.S)]
    assert summaries == [
        "📁 Собираем данные", "💸 Запускаем платёж", "⚔️ Проверяем безопасность",
        "✅ Получаем подтверждение", "⚡️ Проводим платёж", "🔧 Обрабатываем ошибки",
    ]
    assert markup.tags[0][0] == "video"
    assert markup.tags[0][1]["src"] == "tg://video?id=oplatit_cover"
    assert html.index("</video>") < html.index(source)
    assert not any(re.fullmatch(r"h[1-6]", tag) for tag, _ in markup.tags)
    assert html.index("@alfadigital_jobs") < html.index("Отменить платёж")
    assert any(tag == "a" and attrs.get("href") == "https://t.me/alfadigital_jobs"
               for tag, attrs in markup.tags)
    assert ("Казалось бы, всего одна кнопка. Но стоит её нажать, как в работу включаются сразу "
            "несколько банковских систем: проверяют платёж, защищают от мошенничества "
            "и следят, чтобы операция завершилась корректно.") in html
    assert ("Узнали у наших разработчиков, что стоит за кнопкой «Оплатить» "
            "и что нужно учесть, чтобы всё работало как надо.") in html
    assert ("Раньше в PIPE состояние приходилось восстанавливать по логам, но переход на Zeebe "
            "упростил поиск причин сбоев и изменение платёжных сценариев.") in html
    assert "Как следим за стабильностью?" not in html
    assert "Большую часть этой работы пользователь не видит." not in html
    assert [attrs for tag, attrs in markup.tags if tag == "tg-button"] == [
        {"type": "callback_data", "style": "primary", "data": "oplatit_button:cancel:7"},
    ]
    assert html.rstrip().endswith("</tg-button-row>")


def test_cover_uploads_prepared_mp4_with_metadata_and_thumbnail(env):
    message = oplatit_button.render.article_message(ORIGINAL_ARTICLE, POST)
    assert_article_cover(message)
    files = {}
    encoded = json.loads(env.session.prepare_value(message, bot=env.bot, files=files))
    media = encoded["media"][0]["media"]
    assert (media["width"], media["height"], media["duration"]) == (1000, 1000, 9)
    references = [media["media"], media["thumbnail"]]
    assert len(set(references)) == 2 and len(files) == 2
    assert all(ref.startswith("attach://") and ref.removeprefix("attach://") in files
               for ref in references)
    assert str(oplatit_button.render.COVER_FILE) not in encoded["html"]

    async def read_upload(reference):
        return b"".join([chunk async for chunk in files[reference.removeprefix("attach://")].read(env.bot)])

    video = asyncio.run(read_upload(media["media"]))
    assert video == oplatit_button.render.COVER_FILE.read_bytes()
    assert video[4:8] == b"ftyp"  # В rich-пост уходит MP4, а не исходный GIF.
    thumbnail = asyncio.run(read_upload(media["thumbnail"]))
    assert thumbnail == oplatit_button.render.COVER_THUMBNAIL.read_bytes()
    assert thumbnail.startswith(b"\xff\xd8") and len(thumbnail) < 200_000


def test_open_cancel_reopen_only_changes_this_readers_screen(env):
    store_post(env)
    calls = env.press("oplatit_button:open")
    assert names(calls) == ["SendRichMessage", "AnswerCallbackQuery"]
    sent = calls[0]
    params = sent.ephemeral_message_parameters
    assert (int(sent.chat_id), params.receiver_user_id,
            params.replace_callback_query_message) == (CHANNEL, READER, True)
    assert sent.rich_message.html == oplatit_button.render.article_screen(ORIGINAL_ARTICLE, POST)
    assert_article_cover(sent.rich_message)
    ephemeral_id = env.session.last_message.ephemeral_message_id

    calls = env.press("oplatit_button:cancel:7", ephemeral_id=ephemeral_id)
    assert names(calls) == ["EditEphemeralMessageText", "AnswerCallbackQuery"]
    assert (calls[0].receiver_user_id, calls[0].ephemeral_message_id) == (READER, ephemeral_id)
    assert calls[0].rich_message.html == oplatit_button.render.public_post(ORIGINAL_INTRO, POST)
    assert ORIGINAL_ARTICLE not in calls[0].rich_message.html
    assert_no_cover(calls[0].rich_message)

    calls = env.press("oplatit_button:open:7", ephemeral_id=ephemeral_id)
    assert names(calls) == ["EditEphemeralMessageText", "AnswerCallbackQuery"]
    assert calls[0].rich_message.html == sent.rich_message.html
    assert_article_cover(calls[0].rich_message)

    calls = env.press("oplatit_button:open", user=OTHER_READER)
    assert names(calls) == ["SendRichMessage", "AnswerCallbackQuery"]
    assert calls[0].ephemeral_message_parameters.receiver_user_id == OTHER_READER
    assert oplatit_button.stats(env.service) == (
        "публикаций: 1, читателей: 2\nоткрытий: 3, отмен: 1"
    )
    assert env.memory.storage.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert env.phone.storage.db.execute("SELECT COUNT(*) FROM phone_sessions").fetchone()[0] == 0


def test_private_preview_uses_regular_message_editing_without_peer_id_invalid(env):
    store_post(env, chat_id=ADMIN)
    calls = env.press("oplatit_button:open", chat_id=ADMIN, user=ADMIN)
    assert names(calls) == ["EditMessageText", "AnswerCallbackQuery"]
    assert (int(calls[0].chat_id), calls[0].message_id) == (ADMIN, POST)
    assert calls[0].rich_message.html == oplatit_button.render.article_screen(ORIGINAL_ARTICLE, POST)
    assert_article_cover(calls[0].rich_message)
    assert calls[-1].text is None

    calls = env.press("oplatit_button:cancel:7", chat_id=ADMIN, user=ADMIN)
    assert names(calls) == ["EditMessageText", "AnswerCallbackQuery"]
    assert (int(calls[0].chat_id), calls[0].message_id) == (ADMIN, POST)
    assert calls[0].rich_message.html == oplatit_button.render.public_post(ORIGINAL_INTRO, POST)
    assert_no_cover(calls[0].rich_message)

    calls = env.press("oplatit_button:open:7", chat_id=ADMIN, user=ADMIN)
    assert names(calls) == ["EditMessageText", "AnswerCallbackQuery"]
    assert calls[0].message_id == POST
    assert calls[0].rich_message.html == oplatit_button.render.article_screen(ORIGINAL_ARTICLE, POST)
    assert_article_cover(calls[0].rich_message)
    assert "публикаций: 0" in oplatit_button.stats(env.service)


def test_existing_private_ephemeral_screen_keeps_ephemeral_editing(env):
    store_post(env, chat_id=ADMIN)
    calls = env.press("oplatit_button:cancel:7", chat_id=ADMIN, user=ADMIN, ephemeral_id=900)
    assert names(calls) == ["EditEphemeralMessageText", "AnswerCallbackQuery"]
    assert (int(calls[0].chat_id), calls[0].receiver_user_id,
            calls[0].ephemeral_message_id) == (ADMIN, ADMIN, 900)
    assert calls[0].rich_message.html == oplatit_button.render.public_post(ORIGINAL_INTRO, POST)
    assert_no_cover(calls[0].rich_message)
    calls = env.press("oplatit_button:open:7", chat_id=ADMIN, user=ADMIN, ephemeral_id=900)
    assert names(calls) == ["EditEphemeralMessageText", "AnswerCallbackQuery"]
    assert calls[0].ephemeral_message_id == 900
    assert calls[0].rich_message.html == oplatit_button.render.article_screen(ORIGINAL_ARTICLE, POST)
    assert_article_cover(calls[0].rich_message)


@pytest.mark.parametrize("chat_id,user", [(CHANNEL, READER), (ADMIN, ADMIN)])
@pytest.mark.parametrize("restart", [False, True])
def test_unregistered_post_opens_cancels_and_reopens_with_empty_database(
        env, monkeypatch, tmp_path, chat_id, user, restart):
    """Кнопка уже доставленного поста работает и без записи от публикации/предпросмотра."""
    if restart:
        env.service = oplatit_button.create_service(str(tmp_path / "game.db"), 86400)
        env.dispatcher["oplatit_service"] = env.service
    assert env.service.get_post(chat_id, POST) is None
    current = [ORIGINAL_INTRO, ORIGINAL_ARTICLE]
    monkeypatch.setattr(oplatit_button.render, "load_content", lambda: tuple(current))

    calls = env.press("oplatit_button:open", chat_id=chat_id, user=user)
    private = chat_id == ADMIN
    assert names(calls) == ["EditMessageText" if private else "SendRichMessage", "AnswerCallbackQuery"]
    assert calls[0].rich_message.html == oplatit_button.render.article_screen(ORIGINAL_ARTICLE, POST)
    assert_article_cover(calls[0].rich_message)
    if private:
        assert calls[0].message_id == POST
        ephemeral_id = None
    else:
        assert calls[0].ephemeral_message_parameters.receiver_user_id == user
        assert calls[0].ephemeral_message_parameters.replace_callback_query_message is True
        ephemeral_id = env.session.last_message.ephemeral_message_id
    assert calls[-1].text is None and calls[-1].url is None
    post = env.service.get_post(chat_id, POST)
    assert (post.intro, post.article_html) == (ORIGINAL_INTRO, ORIGINAL_ARTICLE)

    current[:] = ["Другая подводка", "<p>Другой материал</p>"]
    calls = env.press("oplatit_button:cancel:7", chat_id=chat_id, user=user,
                      ephemeral_id=ephemeral_id)
    edit_method = "EditMessageText" if private else "EditEphemeralMessageText"
    assert names(calls) == [edit_method, "AnswerCallbackQuery"]
    if private:
        assert calls[0].message_id == POST
    else:
        assert (calls[0].receiver_user_id, calls[0].ephemeral_message_id) == (user, ephemeral_id)
    assert calls[0].rich_message.html == oplatit_button.render.public_post(ORIGINAL_INTRO, POST)
    assert_no_cover(calls[0].rich_message)

    calls = env.press("oplatit_button:open:7", chat_id=chat_id, user=user,
                      ephemeral_id=ephemeral_id)
    assert names(calls) == [edit_method, "AnswerCallbackQuery"]
    assert calls[0].rich_message.html == oplatit_button.render.article_screen(ORIGINAL_ARTICLE, POST)
    assert_article_cover(calls[0].rich_message)
    assert calls[-1].text is None and calls[-1].url is None


def test_old_ephemeral_screen_can_cancel_after_restart_without_a_registered_post(
        env, monkeypatch, tmp_path):
    restarted = oplatit_button.create_service(str(tmp_path / "game.db"), 86400)
    env.dispatcher["oplatit_service"] = restarted
    monkeypatch.setattr(oplatit_button.render, "load_content",
                        lambda: (ORIGINAL_INTRO, ORIGINAL_ARTICLE))
    assert restarted.get_post(CHANNEL, POST) is None
    calls = env.press("oplatit_button:cancel:7", ephemeral_id=900)
    assert names(calls) == ["EditEphemeralMessageText", "AnswerCallbackQuery"]
    assert calls[0].ephemeral_message_id == 900
    assert calls[0].rich_message.html == oplatit_button.render.public_post(ORIGINAL_INTRO, POST)
    assert_no_cover(calls[0].rich_message)
    assert calls[-1].text is None and calls[-1].url is None


@pytest.mark.parametrize("data", ["oplatit_button:open:7", "oplatit_button:cancel:7"])
def test_personal_button_on_public_message_is_stale(env, data):
    calls = env.press(data)
    assert names(calls) == ["AnswerCallbackQuery"]
    assert calls[0].text and "устарел" in calls[0].text.lower()
    assert env.service.get_post(CHANNEL, POST) is None


@pytest.mark.parametrize("data", ["oplatit_button:open:8", "oplatit_button:cancel:8"])
def test_private_message_cannot_edit_another_posts_snapshot(env, data):
    calls = env.press(data, chat_id=ADMIN, user=ADMIN, message_id=POST)
    assert names(calls) == ["AnswerCallbackQuery"]
    assert calls[0].text and "устарел" in calls[0].text.lower()
    assert env.service.get_post(ADMIN, POST) is None
    assert env.service.get_post(ADMIN, 8) is None


@pytest.mark.parametrize("data", [
    "oplatit_button:unknown", "oplatit_button:cancel", "oplatit_button:open:7:1",
    "oplatit_button:cancel:9223372036854775808",
])
def test_malformed_buttons_are_answered_without_changing_the_post(env, data):
    store_post(env)
    calls = env.press(data)
    assert names(calls) == ["AnswerCallbackQuery"]
    assert "устарел" in calls[0].text.lower()


def test_open_failure_has_no_bot_chat_fallback_and_same_update_can_retry(env):
    store_post(env)
    env.session.fail_next = "SendRichMessage"
    calls = env.press("oplatit_button:open", update_id=100)
    assert names(calls) == ["SendRichMessage", "AnswerCallbackQuery"]
    assert calls[-1].text and calls[-1].url is None
    assert "открытий: 0" in oplatit_button.stats(env.service)

    calls = env.press("oplatit_button:open", update_id=100)
    assert names(calls) == ["SendRichMessage", "AnswerCallbackQuery"]
    assert "открытий: 1" in oplatit_button.stats(env.service)


def test_cancel_failure_does_not_count_as_success(env):
    store_post(env)
    env.press("oplatit_button:open")
    ephemeral_id = env.session.last_message.ephemeral_message_id
    env.session.fail_next = "EditEphemeralMessageText"
    calls = env.press("oplatit_button:cancel:7", ephemeral_id=ephemeral_id, update_id=100)
    assert names(calls) == ["EditEphemeralMessageText", "AnswerCallbackQuery"]
    assert calls[-1].text and calls[-1].url is None
    assert "отмен: 0" in oplatit_button.stats(env.service)
    env.press("oplatit_button:cancel:7", ephemeral_id=ephemeral_id, update_id=100)
    assert "отмен: 1" in oplatit_button.stats(env.service)


def test_repeated_successful_update_does_not_send_or_count_twice(env):
    store_post(env)
    env.press("oplatit_button:open", update_id=100)
    calls = env.press("oplatit_button:open", update_id=100)
    assert "SendRichMessage" not in names(calls)
    assert "открытий: 1" in oplatit_button.stats(env.service)


def test_cancel_waits_for_open_delivery_instead_of_being_overwritten(env):
    store_post(env)

    async def run():
        opening = asyncio.Event()
        release = asyncio.Event()
        delivered = []

        async def open_screen(post):
            opening.set()
            await release.wait()
            delivered.append("longread")

        async def cancel_screen(post):
            delivered.append("intro")

        first = asyncio.create_task(env.service.display(READER, CHANNEL, POST, "open", 100, open_screen))
        await opening.wait()
        second = asyncio.create_task(env.service.display(READER, CHANNEL, POST, "cancel", 101, cancel_screen))
        await asyncio.sleep(0)
        assert not second.done()
        release.set()
        assert await first and await second
        assert delivered == ["longread", "intro"]

    asyncio.run(run())


def test_preview_can_open_before_telegram_returns_the_preview_send_response(env, monkeypatch):
    monkeypatch.setattr(oplatit_button.render, "load_content",
                        lambda: (ORIGINAL_INTRO, ORIGINAL_ARTICLE))

    async def run():
        delivered, release = asyncio.Event(), asyncio.Event()
        original_request = env.session.make_request

        async def delayed_response(bot, method, timeout=None):
            response = await original_request(bot, method, timeout)
            if type(method).__name__ == "SendRichMessage" and method.ephemeral_message_parameters is None:
                delivered.set()
                await release.wait()
            return response

        monkeypatch.setattr(env.session, "make_request", delayed_response)
        publication = asyncio.create_task(env.dispatcher.feed_update(env.bot, Update.model_validate({
            "update_id": 100, "message": {
                "message_id": 1, "date": 1, "text": "/oplatit_button",
                "chat": {"id": ADMIN, "type": "private"},
                "from": {"id": ADMIN, "is_bot": False, "first_name": "Админ"},
            },
        })))
        try:
            await asyncio.wait_for(delivered.wait(), timeout=5)
            assert env.service.get_post(ADMIN, 500) is None
            await env.dispatcher.feed_update(env.bot, Update.model_validate({
                "update_id": 101, "callback_query": {
                    "id": "preview-q", "chat_instance": "0", "data": "oplatit_button:open",
                    "from": {"id": ADMIN, "is_bot": False, "first_name": "Админ"},
                    "message": {"message_id": 500, "date": 1,
                                "chat": {"id": ADMIN, "type": "private"}},
                },
            }))
            screens = [call for call in env.session.calls if type(call).__name__ == "EditMessageText"]
            assert len(screens) == 1
            assert (int(screens[0].chat_id), screens[0].message_id) == (ADMIN, 500)
            assert screens[0].rich_message.html == oplatit_button.render.article_screen(ORIGINAL_ARTICLE, 500)
            assert_article_cover(screens[0].rich_message)
            assert not any(type(call).__name__ == "SendRichMessage"
                           and call.ephemeral_message_parameters is not None for call in env.session.calls)
            answers = [call for call in env.session.calls if type(call).__name__ == "AnswerCallbackQuery"]
            assert len(answers) == 1 and answers[0].text is None
        finally:
            release.set()
            await publication

    asyncio.run(run())


def test_publishing_and_reading_preserve_the_exact_preview_snapshot(env, monkeypatch):
    current = [ORIGINAL_INTRO, ORIGINAL_ARTICLE]
    monkeypatch.setattr(oplatit_button.render, "load_content", lambda: tuple(current))

    def edit_files_during_delivery(method):
        if type(method).__name__ == "SendRichMessage":
            current[:] = ["Новая подводка", "<p>Новый материал</p>"]

    env.session.on_send = edit_files_during_delivery
    preview, question = env.send("/oplatit_button")
    assert names([preview, question]) == ["SendRichMessage", "SendMessage"]
    preview_id = 500
    question_id = env.session.last_message.message_id
    assert ORIGINAL_INTRO in preview.rich_message.html
    assert_no_cover(preview.rich_message)

    calls = env.press("oplatit_button:open", user=ADMIN, chat_id=ADMIN, message_id=preview_id)
    assert names(calls) == ["EditMessageText", "AnswerCallbackQuery"]
    assert ORIGINAL_ARTICLE in calls[0].rich_message.html
    assert "Новый материал" not in calls[0].rich_message.html
    assert_article_cover(calls[0].rich_message)

    calls = env.press(admin.CONFIRM_YES, user=ADMIN, chat_id=ADMIN, message_id=question_id)
    assert names(calls) == ["SendRichMessage", "EditMessageText", "AnswerCallbackQuery"]
    assert calls[0].rich_message.html == preview.rich_message.html
    assert_no_cover(calls[0].rich_message)
    published_id = env.session.last_message.message_id

    calls = env.press("oplatit_button:open", message_id=published_id)
    assert ORIGINAL_ARTICLE in calls[0].rich_message.html
    assert "Новый материал" not in calls[0].rich_message.html
    assert_article_cover(calls[0].rich_message)
    assert "публикаций: 1, читателей: 1\nоткрытий: 1, отмен: 0" == oplatit_button.stats(env.service)
    calls = env.press(admin.CONFIRM_YES, user=ADMIN, chat_id=ADMIN, message_id=question_id)
    assert "SendRichMessage" not in names(calls)


def test_snapshot_survives_service_restart(env, tmp_path):
    store_post(env)
    reopened = oplatit_button.create_service(str(tmp_path / "game.db"), 1)
    post = reopened.get_post(CHANNEL, POST)
    assert (post.intro, post.article_html) == (ORIGINAL_INTRO, ORIGINAL_ARTICLE)


def test_start_payload_does_not_open_a_bot_chat_version(env):
    calls = env.send("/start oplatit_button", user=READER)
    assert names(calls) == ["SendMessage"]
    assert "Игра проходит в нашем канале" in calls[0].text
