"""Rich HTML для общего поста и личного экрана.

Текст поста берётся из файла post.txt: абзацы разделены пустой строкой,
строка [игра] — место для экрана и клавиатуры.

callback_data (все начинаются с phone:):
  общий пост    phone:<цифра>, phone:del, phone:ok  ход сразу применяется, ответ — всплывающая подсказка
  личный экран  phone:<действие>:<пост>:<ревизия>   например phone:1:2:12, phone:new:2:20
  действие: цифра 0–9, del, ok, new («Играть снова»)
  phone:-       клетки строки ввода и попыток: в посте открывает игру, в личном экране ничего не делает
                (это обычные кнопки, а не disabled, чтобы цифры не были бледными)
Пароля нет ни в callback_data, ни в тексте экрана.
"""
from __future__ import annotations

import pathlib

from .logic import CODE_LENGTH, HIT, LOST, NEAR, WON, Game
from .service import ACTIONS, CHECK, DELETE, NEW_GAME, VIEW
from .storage import Session

POST_FILE = pathlib.Path(__file__).resolve().parent / "post.txt"
GAME_MARK = "[игра]"
PREFIX = "phone:"

VIEW_DATA = f"{PREFIX}{VIEW}"

# цвета только у отметок в строках попыток; клавиатура и остальные кнопки без цвета
MARK_STYLE = {HIT: "success", NEAR: "primary"}
EMPTY = "·"
KEYPAD = ("123", "456", "789")

# Тексты экрана
ATTEMPT_LINE = "<b>Попытка #{n}</b>"
WIN_LINE = "<b>Телефон разблокирован, запоминайте дату 📆</b>"
LOSE_LINE = "🔒 <b>Попытки закончились.</b> Попробуйте ещё раз."
PLAY_AGAIN = "Играть снова"
DELETE_LABEL = "⌫ Удалить"
CHECK_LABEL = "Проверить"


def _button(text: str, data: str, style: str | None = None) -> str:
    st = f' style="{style}"' if style else ""
    return f'<tg-button type="callback_data"{st} data="{data}">{text}</tg-button>'


def _cell(text: str, style: str | None = None) -> str:
    """Клетка строки ввода или попытки. Не disabled: такие кнопки Telegram рисует бледными."""
    return _button(text, VIEW_DATA, style)


def _disabled(text: str, style: str | None = None) -> str:
    st = f' style="{style}"' if style else ""
    return f'<tg-button type="disabled"{st}>{text}</tg-button>'


def _row(*buttons: str) -> str:
    return '<tg-button-row align="center">' + "".join(buttons) + "</tg-button-row>"


def _display(current: str) -> str:
    """Набранные цифры, на пустых местах точки."""
    return _row(*(_cell(current[i] if i < len(current) else EMPTY) for i in range(CODE_LENGTH)))


def _keypad(data, current: str, always_active: bool = False) -> str:
    """Раскладка как у телефона: * и # неактивны. «Удалить» активна, когда есть что удалять,
    «Проверить» — когда набраны все цифры. В общем посте обе активны всегда: на iPhone
    игра идёт по кнопкам поста (личный экран там не открывается). data(action) -> callback_data."""
    rows = [_row(*(_button(d, data(d)) for d in line)) for line in KEYPAD]
    rows.append(_row(_disabled("*"), _button("0", data("0")), _disabled("#")))
    can_delete = always_active or bool(current)
    can_check = always_active or len(current) == CODE_LENGTH
    delete = _button(DELETE_LABEL, data(DELETE)) if can_delete else _disabled(DELETE_LABEL)
    check = _button(CHECK_LABEL, data(CHECK)) if can_check else _disabled(CHECK_LABEL)
    rows.append(_row(delete, check))
    return "".join(rows)


def _history(game: Game) -> str:
    """Каждая попытка — строка из цифр: зелёная на своём месте, синяя есть, но не там."""
    return "".join(
        _row(*(_cell(d, MARK_STYLE.get(m)) for d, m in zip(a.guess, a.marks)))
        for a in game.attempts
    )


def _wrap(game_block: str) -> str:
    """Текст из post.txt с игрой на месте [игра]. Личный экран повторяет пост,
    чтобы на его месте у игрока не пропадал текст. Абзацы подряд идут одним блоком
    через пустую строку: между отдельными <p> Telegram ставит слишком узкий отступ."""
    parts, text = [], []

    def flush():
        if text:
            parts.append("<p>" + "<br><br>".join(text) + "</p>")
            text.clear()

    for block in POST_FILE.read_text(encoding="utf-8").strip().split("\n\n"):
        block = block.strip()
        if block == GAME_MARK:
            flush()
            parts.append(game_block)
        elif block:
            text.append("<br>".join(block.splitlines()))
    flush()
    return "".join(parts)


def _playing(game: Game, data, always_active: bool = False) -> str:
    return (f"<p>{ATTEMPT_LINE.format(n=game.attempt_number)}</p>"
            + _history(game) + _display(game.current) + _keypad(data, game.current, always_active))


def public_post() -> str:
    return _wrap(_playing(Game(), lambda action: f"{PREFIX}{action}", always_active=True))


def personal_screen(s: Session) -> str:
    g = s.game

    def data(action: str) -> str:
        return f"{PREFIX}{action}:{s.message_id}:{s.revision}"

    again = _row(_button(PLAY_AGAIN, data(NEW_GAME)))
    if g.status == WON:
        return _wrap(f"<p>{WIN_LINE}</p>" + _history(g) + again)
    if g.status == LOST:
        return _wrap(f"<p>{LOSE_LINE}</p>" + _history(g) + again)
    return _wrap(_playing(g, data))


def parse_post_data(data: str) -> str | None:
    """phone:<действие> → действие."""
    parts = data.split(":")
    if len(parts) == 2 and parts[0] + ":" == PREFIX and parts[1] in ACTIONS:
        return parts[1]
    return None


def parse_screen_data(data: str) -> tuple[str, int, int] | None:
    """phone:<действие>:<пост>:<ревизия> → (действие, пост, ревизия)."""
    parts = data.split(":")
    if len(parts) != 4 or parts[0] + ":" != PREFIX or parts[1] not in ACTIONS:
        return None
    try:
        return parts[1], int(parts[2]), int(parts[3])
    except ValueError:
        return None
