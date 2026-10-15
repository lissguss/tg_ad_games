"""Rich HTML для общего поста и личного экрана.

Текст поста берётся из файла post.txt: абзацы разделены пустой строкой,
строка [ПОЛЕ] — место для игрового поля.

callback_data:
  общий пост    p:<карточка>                    например p:7
  личный экран  c:<карточка>:<пост>:<ревизия>   например c:7:2:12
                new:-:<пост>:<ревизия>          «Сыграть ещё»
Эмодзи закрытых карточек нет ни в тексте, ни в callback_data — раскладка только на сервере.
"""
from __future__ import annotations

from .config import ROOT
from .logic import CARDS, COLUMNS, WON
from .storage import Session

POST_FILE = ROOT / "post.txt"
FIELD_MARK = "[ПОЛЕ]"
BACK = "❓"
FOUND_STYLE = "success"  # найденные пары — зелёные
WIN_LINE = "Все пары найдены! Ставьте 🔥 под постом"
PLAY_AGAIN = "Сыграть ещё"


def _button(text: str, data: str, style: str | None = None) -> str:
    st = f' style="{style}"' if style else ""
    return f'<tg-button type="callback_data"{st} data="{data}">{text}</tg-button>'


def _row(*buttons: str) -> str:
    return '<tg-button-row align="center">' + "".join(buttons) + "</tg-button-row>"


def _grid(buttons: list[str]) -> str:
    return "".join(_row(*buttons[i:i + COLUMNS]) for i in range(0, len(buttons), COLUMNS))


def _wrap(field: str) -> str:
    """Текст из post.txt с полем на месте [ПОЛЕ]. Личный экран повторяет пост,
    чтобы на его месте у игрока не пропадал текст."""
    parts = []
    for block in POST_FILE.read_text(encoding="utf-8").strip().split("\n\n"):
        block = block.strip()
        if block == FIELD_MARK:
            parts.append(field)
        elif block:
            parts.append("<p>" + "<br>".join(block.splitlines()) + "</p>")
    return "".join(parts)


def public_post() -> str:
    return _wrap(_grid([_button(BACK, f"p:{i}") for i in range(CARDS)]))


def personal_screen(s: Session) -> str:
    g = s.game

    def data(action: str, arg: object) -> str:
        return f"{action}:{arg}:{s.message_id}:{s.revision}"

    buttons = []
    for i in range(CARDS):
        if i in g.found:
            buttons.append(_button(g.layout[i], data("c", i), FOUND_STYLE))
        elif i in g.opened:
            buttons.append(_button(g.layout[i], data("c", i)))
        else:
            buttons.append(_button(BACK, data("c", i)))
    field = _grid(buttons)
    if g.status == WON:
        field += f"<p><b>{WIN_LINE}</b></p>" + _row(_button(PLAY_AGAIN, data("new", "-"), "primary"))
    return _wrap(field)


def parse_post_data(data: str) -> int | None:
    parts = data.split(":")
    if len(parts) == 2 and parts[0] == "p" and parts[1].isdigit():
        return int(parts[1])
    return None


def parse_screen_data(data: str) -> tuple[str, int, int, int] | None:
    """(действие, карточка, пост, ревизия); для new карточка = -1."""
    parts = data.split(":")
    if len(parts) != 4:
        return None
    action, arg = parts[0], parts[1]
    try:
        card = -1 if arg == "-" else int(arg)
        return action, card, int(parts[2]), int(parts[3])
    except ValueError:
        return None
