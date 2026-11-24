"""Интерактивный пост о том, что происходит после нажатия «Оплатить»."""
from __future__ import annotations

from . import render
from .handlers import router
from .service import GameService
from .storage import Content, Storage

TITLE = "Что происходит после нажатия кнопки «Оплатить»?"
COMMAND = "oplatit_button"


def create_service(db_path: str, ttl_seconds: int) -> GameService:
    # Снимки публикаций и статистика сохраняются: таймеров и истекающих партий здесь нет.
    return GameService(Storage(db_path))


def prepare_publication() -> tuple[str, Content]:
    intro, article_html = render.load_content()
    return render.public_post(intro), Content(intro, article_html)


def post_html() -> str:
    return prepare_publication()[0]


def on_preview(service: GameService, chat_id: int, message_id: int, publication: Content) -> None:
    service.store_post(chat_id, message_id, publication.intro, publication.article_html)


def on_published(service: GameService, chat_id: int, message_id: int,
                 *, publication: Content | None = None) -> None:
    if publication is None:
        _, publication = prepare_publication()
    service.store_post(chat_id, message_id, publication.intro, publication.article_html, published=True)


def stats(service: GameService) -> str:
    row = service.storage.stats()
    return (f"публикаций: {row['posts']}, читателей: {row['readers']}\n"
            f"открытий: {row['opens']}, отмен: {row['cancels']}")
