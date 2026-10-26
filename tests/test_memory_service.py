import asyncio
import re
import time

import pytest

from games.memory import render
from games.memory.logic import CARDS, EMOJIS, FLIP_DELAY, MSG_ALREADY_OPEN, PLAYING, WON
from games.memory.service import (
    CARD, MSG_EXPIRED, MSG_STALE_SCREEN, NEW_GAME, GameService,
)
from games.memory.storage import Storage

U1, U2, CHAT, POST = 111, 222, -100, 7
LAYOUT = [EMOJIS[i // 4] for i in range(CARDS)]  # пары: (0,1), (2,3), (4,5) ...


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "game.db")


@pytest.fixture
def service(db_path):
    return GameService(Storage(db_path), ttl_seconds=7 * 86400)


def run(coro):
    return asyncio.run(coro)


def start(service, user=U1, card=0, now=None):
    """Первое нажатие в посте; подменяем раскладку на известную."""
    s = run(service.press_on_post(user, CHAT, POST, card, now=now)).session
    s.game.layout = list(LAYOUT)
    service.storage.save(s)
    return s


def tap(service, s, card, user=U1, revision=None, now=None):
    rev = s.revision if revision is None else revision
    return run(service.press_on_screen(user, CHAT, POST, CARD, card, rev, now=now))


def test_post_press_starts_game_and_opens_card(service):
    s = start(service, card=5)
    assert s.game.opened == [5]
    assert s.game.status == PLAYING


def test_stale_revision_is_not_applied(service):
    s = start(service)
    old_rev = s.revision
    s = tap(service, s, 1).session
    out = tap(service, s, 2, revision=old_rev)
    assert out.toast == MSG_STALE_SCREEN
    assert out.session.game.found == [0, 1] and out.session.game.opened == []


def test_double_press_is_one_move(service):
    s = start(service)
    rev = s.revision

    async def two_presses():
        return await asyncio.gather(
            service.press_on_screen(U1, CHAT, POST, CARD, 4, rev),
            service.press_on_screen(U1, CHAT, POST, CARD, 4, rev),
        )

    a, b = run(two_presses())
    assert sorted([a.toast or "", b.toast or ""]) == ["", MSG_STALE_SCREEN]
    assert service.storage.load(U1, CHAT, POST).game.opened == [0, 4]


def test_same_update_processed_once(service):
    assert service.storage.mark_update(42)
    assert not service.storage.mark_update(42)


def test_open_card_press_gives_toast(service):
    s = start(service)
    out = tap(service, s, 0)
    assert out.toast == MSG_ALREADY_OPEN and not out.changed


def test_mismatch_auto_flip(service):
    s = start(service, now=100)
    s = tap(service, s, 4, now=110).session
    assert s.game.flip_at == 110 + FLIP_DELAY
    assert run(service.flip(s.key, now=111)) is None  # ещё рано
    out = run(service.flip(s.key, now=112))
    assert out.session.game.opened == []
    assert out.session.revision == s.revision + 1
    assert run(service.flip(s.key, now=113)) is None  # второй раз не переворачивает


def test_flip_skipped_if_player_already_moved_on(service):
    s = start(service, now=100)
    s = tap(service, s, 4, now=100).session
    s = tap(service, s, 8, now=101).session  # новая карточка сразу перевернула пару
    assert s.game.opened == [8]
    assert run(service.flip(s.key, now=200)) is None
    assert service.storage.load(*s.key).game.opened == [8]


def test_pending_flip_survives_restart(db_path):
    svc = GameService(Storage(db_path), 86400)
    s = start(svc, now=100)
    tap(svc, s, 4, now=100)
    svc2 = GameService(Storage(db_path), 86400)  # «перезапуск сервера»
    pending = svc2.storage.pending_flips()
    assert [p.key for p in pending] == [s.key]
    assert run(svc2.flip(s.key)).session.game.opened == []
    assert svc2.storage.pending_flips() == []


def test_game_survives_restart(db_path):
    svc = GameService(Storage(db_path), 86400)
    s = start(svc)
    svc2 = GameService(Storage(db_path), 86400)
    out = tap(svc2, s, 1)
    assert out.session.game.found == [0, 1]


def test_players_do_not_affect_each_other(service):
    s1 = start(service, U1, card=0)
    start(service, U2, card=4)
    tap(service, s1, 1, user=U1)
    assert service.storage.load(U2, CHAT, POST).game.opened == [4]
    assert service.storage.load(U2, CHAT, POST).game.found == []
    assert service.storage.load(U1, CHAT, POST).game.found == [0, 1]


def test_each_player_gets_own_random_layout(service):
    layouts = {
        tuple(run(service.press_on_post(uid, CHAT, POST, 0)).session.game.layout)
        for uid in range(1, 30)
    }
    assert len(layouts) > 1


def test_post_press_restores_existing_game(service):
    s = start(service, now=100)
    out = run(service.press_on_post(U1, CHAT, POST, 1, now=1000))  # приложение перезапустили
    assert out.toast == f"{LAYOUT[0]} + {LAYOUT[0]} — пара! Найдено 1 из 6"  # игра продолжается
    assert out.session.game.found == [0, 1]
    assert out.session.revision > s.revision  # старый экран больше не действует


def test_expired_session_starts_new_game(service):
    s = start(service)
    service.storage.db.execute("UPDATE sessions SET updated_at=?", (int(time.time()) - 8 * 86400,))
    out = tap(service, s, 1)
    assert out.toast == MSG_EXPIRED
    assert out.session.game.opened == [] and out.session.game.found == []


def win(service, s):
    for a, b in [(0, 1), (2, 3), (4, 5), (6, 7), (8, 9), (10, 11)]:
        if a not in s.game.opened:
            s = tap(service, s, a).session
        s = tap(service, s, b).session
    return s


def test_win_and_play_again(service):
    s = win(service, start(service))
    assert s.game.status == WON
    html = render.personal_screen(s)
    assert render.WIN_LINE in html and render.PLAY_AGAIN in html
    assert tap(service, s, 0).toast is not None  # поле заблокировано
    s = run(service.press_on_screen(U1, CHAT, POST, NEW_GAME, -1, s.revision)).session
    assert s.game.status == PLAYING and s.game.found == [] and s.game.opened == []


def test_layout_hidden_from_message(service):
    s = start(service)
    html = render.personal_screen(s)
    shown = [e for e in EMOJIS if e in html]
    assert shown == [LAYOUT[0]]  # видна только открытая карточка
    assert html.count(LAYOUT[0]) == 1
    for e in EMOJIS:
        assert e not in render.public_post("test_bot").replace("эмодзи", "")


def test_post_contains_text_and_field():
    html = render.public_post("test_bot")
    assert "<p>Спрятали под кнопками шесть пар эмодзи. Сможете найти все?</p>" in html
    assert html.count("❓") == 12
    assert html.count("<tg-button-row") == 5  # поле 4 ряда + «Играть в боте»
    assert 'url="https://t.me/test_bot?start=memory"' in html
    assert render.FIELD_MARK not in html


def test_callback_data_fits_64_bytes(service):
    s = start(service)
    s = s.__class__(U1, CHAT, 10**12, s.game, revision=10**12)
    for data in re.findall(r'data="([^"]+)"', render.personal_screen(s)):
        assert 1 <= len(data.encode()) <= 64
        assert render.parse_screen_data(data)
    for data in re.findall(r'data="([^"]+)"', render.public_post("test_bot")):
        assert render.parse_post_data(data) is not None


def post(service, card, user=U1, now=None):
    return run(service.press_on_post(user, CHAT, POST, card, now=now))


def test_post_press_describes_the_move(service):
    """На iPhone личный экран не открывается: игра идёт по подсказкам на нажатия в посте."""
    start(service, now=100)  # открыта карточка 0
    pair = post(service, 1, now=101)
    assert pair.toast == f"{LAYOUT[0]} + {LAYOUT[0]} — пара! Найдено 1 из 6" and not pair.alert
    assert post(service, 4, now=102).toast == f"Карточка 5: {LAYOUT[4]}"
    miss = post(service, 8, now=103)
    assert miss.toast == f"{LAYOUT[4]} и {LAYOUT[8]} — не совпали, запоминайте"


def test_post_presses_win_with_an_alert_and_never_blame_the_app(service):
    start(service, now=100)
    toasts = []
    for card in range(1, CARDS):
        out = post(service, card, now=100 + card)
        toasts.append(out.toast or "")
    assert out.session.game.status == WON and out.alert
    assert out.toast.endswith("Все пары найдены! Ставьте 🔥 под постом")
    assert not any("Обновите" in t for t in toasts)
    again = post(service, 0, now=200)
    assert again.alert and again.toast.endswith("Все пары найдены! Ставьте 🔥 под постом")
