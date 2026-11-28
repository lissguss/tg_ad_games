"""Два rich-экрана: подводка с кнопкой и лонгрид с отменой платежа."""
from __future__ import annotations

from html import escape
from pathlib import Path

from aiogram.types import FSInputFile, InputMediaAnimation, InputRichMessage, InputRichMessageMedia

ROOT = Path(__file__).resolve().parent
INTRO_FILE = ROOT / "intro.txt"
POST_FILE = ROOT / "post.html"
# Подготовленная H.264-анимация для отправки в rich-пост.
COVER_FILE = ROOT / "cover_oplatit_button_final.mp4"
COVER_THUMBNAIL = ROOT / "cover_oplatit_button_thumbnail.jpg"
COVER_WIDTH, COVER_HEIGHT, COVER_DURATION = 1080, 1080, 3
COVER_ID = "oplatit_cover"
COVER_HTML = f'<video src="tg://video?id={COVER_ID}"></video>'

PREFIX = "oplatit_button:"
OPEN_DATA = f"{PREFIX}open"
OPEN, CANCEL = "open", "cancel"
MAX_POST_ID = 2**63 - 1  # предел INTEGER в SQLite


def load_content() -> tuple[str, str]:
    """Прочитать подводку и HTML лонгрида для новой публикации."""
    return (INTRO_FILE.read_text(encoding="utf-8").strip(),
            POST_FILE.read_text(encoding="utf-8").strip())


def callback_data(action: str, post_id: int) -> str:
    if action not in (OPEN, CANCEL) or type(post_id) is not int or not 0 < post_id <= MAX_POST_ID:
        raise ValueError("Нужны действие open/cancel и положительный id поста")
    data = f"{PREFIX}{action}:{post_id}"
    if len(data.encode("utf-8")) > 64:
        raise ValueError("callback_data длиннее 64 байт")
    return data


def parse_data(data: str | None) -> tuple[str, int | None] | None:
    if data == OPEN_DATA:
        return OPEN, None
    if not data or len(data.encode("utf-8")) > 64:
        return None
    parts = data.split(":")
    if (len(parts) != 3 or parts[0] + ":" != PREFIX or parts[1] not in (OPEN, CANCEL)
            or not parts[2].isascii() or not parts[2].isdigit()):
        return None
    post_id = int(parts[2])
    return (parts[1], post_id) if 0 < post_id <= MAX_POST_ID else None


def _button_row(text: str, data: str, style: str | None = None) -> str:
    button_style = f' style="{style}"' if style else ""
    return (f'<tg-button-row><tg-button type="callback_data"{button_style} '
            f'data="{data}">{text}</tg-button></tg-button-row>')


def public_post(intro: str | None = None, post_id: int | None = None) -> str:
    """На первом экране только подводка и одна кнопка «Оплатить»."""
    if intro is None:
        intro = INTRO_FILE.read_text(encoding="utf-8").strip()
    data = OPEN_DATA if post_id is None else callback_data(OPEN, post_id)
    text = "<br>".join(escape(intro).splitlines())
    return f"<p>{text}</p>" + _button_row("Оплатить", data, "primary")


def article_screen(article_html: str, post_id: int) -> str:
    """Обложка появляется только во втором экране, перед лонгридом."""
    return COVER_HTML + article_html + _button_row("Отменить платёж", callback_data(CANCEL, post_id), "primary")


def article_message(article_html: str, post_id: int) -> InputRichMessage:
    """Загрузить подготовленную анимацию с её размерами, длительностью и превью."""
    return InputRichMessage(
        html=article_screen(article_html, post_id),
        media=[InputRichMessageMedia(
            id=COVER_ID, media=InputMediaAnimation(
                media=FSInputFile(COVER_FILE),
                thumbnail=FSInputFile(COVER_THUMBNAIL),
                width=COVER_WIDTH, height=COVER_HEIGHT, duration=COVER_DURATION,
            ),
        )],
    )
