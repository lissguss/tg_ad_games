"""Запуск бота: python -m game"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from .config import load_config
from .handlers import resume_pending_flips, router
from .service import GameService
from .storage import Storage

CLEANUP_INTERVAL = 3600


async def cleanup_loop(service: GameService):
    while True:
        try:
            n = service.cleanup()
            if n:
                logging.info("удалено устаревших сессий: %s", n)
        except Exception:
            logging.exception("ошибка очистки")
        await asyncio.sleep(CLEANUP_INTERVAL)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = load_config()
    service = GameService(Storage(config.db_path), ttl_seconds=config.session_ttl_days * 86400)
    bot = Bot(config.bot_token)
    dp = Dispatcher(config=config, service=service)
    dp.include_router(router)
    resume_pending_flips(bot, service)
    cleanup = asyncio.create_task(cleanup_loop(service))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        cleanup.cancel()


if __name__ == "__main__":
    asyncio.run(main())
