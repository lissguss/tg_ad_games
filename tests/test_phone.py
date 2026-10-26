import asyncio
import re

import pytest

from games.phone import render
from games.phone.logic import (
    HIT, LOST, MAX_ATTEMPTS, MISS, MSG_FINISHED, MSG_FULL, MSG_NEED_ALL, NEAR, PASSWORD, PLAYING,
    WON, Game, score,
)
from games.phone.service import MSG_STALE_SCREEN, GameService
from games.phone.storage import Storage

U1, U2, CHAT, POST = 111, 222, -100, 7
WRONG = "234567"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def service(tmp_path):
    return GameService(Storage(str(tmp_path / "game.db")), ttl_seconds=7 * 86400)


def enter(game: Game, code: str):
    for d in code:
        assert game.press_digit(d) is None
    return game.press_check()


def tap(service, s, action, user=U1, revision=None):
    rev = s.revision if revision is None else revision
    return run(service.press_on_screen(user, CHAT, POST, action, rev))


def type_code(service, s, code):
    for d in code:
        s = tap(service, s, d).session
    return tap(service, s, "ok").session


# --- логика ---

def test_password_is_date():
    assert PASSWORD == "171126" and len(PASSWORD) == 6


def test_score_exact():
    assert score(PASSWORD, PASSWORD) == [HIT] * 6


def test_score_repeated_digits_like_wordle():
    # в пароле три единицы; лишние совпадения — серые
    assert score("171126", "111111") == [HIT, MISS, HIT, HIT, MISS, MISS]
    assert score("171126", "711000") == [NEAR, NEAR, HIT, MISS, MISS, MISS]
    assert score("171126", "622222") == [NEAR, MISS, MISS, MISS, HIT, MISS]


def test_score_no_digits():
    assert score("171126", "333333") == [MISS] * 6


def test_repeated_digits_allowed_in_input():
    g = Game()
    for d in "111":
        assert g.press_digit(d) is None
    assert g.current == "111"


def test_seventh_digit_rejected():
    g = Game()
    for d in WRONG:
        g.press_digit(d)
    assert g.press_digit("9") == MSG_FULL


def test_check_requires_all_digits():
    g = Game()
    g.press_digit("1")
    assert g.press_check() == MSG_NEED_ALL and g.attempts == []


def test_win_on_fifth_attempt():
    g = Game()
    for _ in range(MAX_ATTEMPTS - 1):
        enter(g, WRONG)
    assert g.status == PLAYING
    enter(g, PASSWORD)
    assert g.status == WON


def test_loss_after_five_attempts_and_input_blocked():
    g = Game()
    for _ in range(MAX_ATTEMPTS):
        enter(g, WRONG)
    assert g.status == LOST
    assert g.press_digit("1") == MSG_FINISHED


# --- сервис ---

def post(service, action, user=U1):
    return run(service.press_on_post(user, CHAT, POST, action))


def test_first_digit_on_post_starts_game(service):
    out = post(service, "1")
    assert out.session.game.current == "1"
    assert out.toast == "Попытка 1 из 5. Набрано: 1 • • • • •" and not out.alert


def test_delete_on_post_before_game(service):
    assert post(service, "del").toast == "Нечего удалять. Набрано: • • • • • •"


def test_post_press_continues_the_game(service):
    post(service, "1")
    post(service, "7")
    assert post(service, "del").toast == "Попытка 1 из 5. Набрано: 1 • • • • •"
    out = post(service, "ok")
    assert out.toast == "Введите 6 цифр. Набрано: 1 • • • • •" and out.session.game.current == "1"


def test_whole_game_is_playable_from_the_post(service):
    """iPhone не показывает личный экран: цифры, «Удалить» и «Проверить» работают кнопками поста."""
    for d in WRONG:
        post(service, d)
    checked = post(service, "ok")
    assert checked.alert
    assert checked.toast.splitlines() == [
        "Попытка 1 из 5: не подошло",
        "234567 🟦⬜⬜⬜🟦🟦",
        "🟩 на месте · 🟦 есть, но не там",
        "Осталось попыток: 4",
    ]
    for d in PASSWORD:
        post(service, d)
    won = post(service, "ok")
    assert won.session.game.status == WON and won.alert
    assert won.toast.splitlines()[0] == "🎉 Телефон разблокирован, запоминайте дату 📆"
    assert post(service, "5").session.game.status == WON  # после победы цифры не вводятся
    assert service.storage.stats()["wins"] == 1


def test_lost_game_restarts_from_the_post(service):
    for _ in range(MAX_ATTEMPTS):
        for d in WRONG:
            post(service, d)
        last = post(service, "ok")
    assert last.session.game.status == LOST and last.alert
    assert last.toast.startswith("🔒 Попытки закончились")
    assert PASSWORD not in last.toast
    assert len(last.toast) <= 200
    fresh = post(service, "1")
    assert fresh.session.game.status == PLAYING and fresh.session.game.attempts == []
    assert fresh.toast == "Новая игра. Попытка 1 из 5. Набрано: 1 • • • • •"


def test_post_presses_never_blame_the_app(service):
    toasts = [post(service, a).toast for a in ["1", "-", "del", "ok", "2", "2"]]
    assert not any("Обновите" in t for t in toasts)


def test_stale_revision_not_applied(service):
    s = run(service.press_on_post(U1, CHAT, POST, "1")).session
    old = s.revision
    s = tap(service, s, "7").session
    out = tap(service, s, "9", revision=old)
    assert out.toast == MSG_STALE_SCREEN and out.session.game.current == "17"


def test_win_logs_attempts_and_stats(service):
    s = run(service.press_on_post(U1, CHAT, POST, "1")).session
    s = tap(service, s, "del").session
    s = type_code(service, s, WRONG)
    s = type_code(service, s, PASSWORD)
    assert s.game.status == WON
    service.storage.add_post(CHAT, POST)
    r = service.storage.stats()
    assert (r["posts"], r["players"], r["checks"], r["solvers"], r["wins"], r["losses"]) == (1, 1, 2, 1, 1, 0)
    s = tap(service, s, "new").session
    assert s.game.status == PLAYING and s.game.attempts == []


def test_loss_counted(service):
    s = run(service.press_on_post(U2, CHAT, POST, "del")).session
    for _ in range(MAX_ATTEMPTS):
        for d in WRONG:
            s = run(service.press_on_screen(U2, CHAT, POST, d, s.revision)).session
        s = run(service.press_on_screen(U2, CHAT, POST, "ok", s.revision)).session
    assert s.game.status == LOST
    assert service.storage.stats()["losses"] == 1


# --- рендер ---

def test_post_text_and_keypad():
    html = render.public_post()
    # три абзаца до игры — один блок, между ними пустая строка
    assert html.startswith("<p>После планёрки наш эксперт по безопасной разработке")
    assert "подсказкой: <tg-spoiler>«Пароль — день," in html
    assert "в бигтехе»</tg-spoiler>.<br><br>Если вы всё-таки" in html
    assert '<a href="https://t.me/alfadigital_jobs/6597">нажали на стикер</a>' in html
    assert "дату 🍸<br><br>Скоро всё расскажем." in html
    assert html.count("<p>") == 3  # текст, «Попытка #1», подпись
    assert "<b>Попытка #1</b>" in html
    assert html.endswith("<p>@alfadigital_jobs</p>")
    assert render.GAME_MARK not in html
    datas = re.findall(r'data="([^"]+)"', html)
    # «Удалить» и «Проверить» в посте активны (на iPhone игра идёт по кнопкам поста), * и # неактивны
    assert sorted(set(datas)) == sorted(
        [f"phone:{d}" for d in "0123456789"] + ["phone:-", "phone:del", "phone:ok"]
    )
    assert datas.count("phone:-") == 6  # строка ввода — обычные кнопки, не бледные
    assert '<tg-button type="disabled">*</tg-button>' in html
    assert '<tg-button type="disabled">#</tg-button>' in html
    assert "style=" not in html  # клавиатура без цвета
    assert '<tg-button type="disabled">·</tg-button>' not in html


def test_password_never_in_screen(service):
    s = run(service.press_on_post(U1, CHAT, POST, "1")).session
    assert PASSWORD not in render.personal_screen(s)
    for _ in range(MAX_ATTEMPTS):
        s = type_code(service, s, WRONG)
    assert s.game.status == LOST
    assert PASSWORD not in render.personal_screen(s)


def test_screen_callback_data_prefixed_and_short(service):
    s = run(service.press_on_post(U1, CHAT, POST, "1")).session
    s.revision = 10**9
    s.message_id = 10**10
    for data in re.findall(r'data="([^"]+)"', render.personal_screen(s)):
        assert data.startswith("phone:") and len(data.encode()) <= 64
        assert data == render.VIEW_DATA or render.parse_screen_data(data)


def test_delete_and_check_enabled_by_input(service):
    s = run(service.press_on_post(U1, CHAT, POST, "1")).session
    html = render.personal_screen(s)
    assert f'data="phone:del:{POST}:{s.revision}"' in html
    assert f'data="phone:ok:' not in html
    for d in "71126":
        s = tap(service, s, d).session
    html = render.personal_screen(s)
    assert f'data="phone:ok:{POST}:{s.revision}"' in html


def test_attempt_rows_colored_keypad_not(service):
    s = run(service.press_on_post(U1, CHAT, POST, "del")).session
    s = type_code(service, s, "711000")
    html = render.personal_screen(s)
    assert "<b>Попытка #2</b>" in html
    row = '<tg-button-row align="center">' + "".join([
        '<tg-button type="callback_data" style="primary" data="phone:-">7</tg-button>',
        '<tg-button type="callback_data" style="primary" data="phone:-">1</tg-button>',
        '<tg-button type="callback_data" style="success" data="phone:-">1</tg-button>',
        '<tg-button type="callback_data" data="phone:-">0</tg-button>' * 3,
    ]) + "</tg-button-row>"
    assert row in html
    assert html.count("style=") == 3  # только в строке попытки


def test_view_cell_on_post_opens_game_without_input(service):
    out = run(service.press_on_post(U1, CHAT, POST, "-"))
    assert out.toast == "Попытка 1 из 5. Набрано: • • • • • •"
    assert out.session.game.current == "" and out.changed
