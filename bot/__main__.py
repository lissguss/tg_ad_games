"""Запуск бота: python -m bot"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from games import memory, oplatit_button, phone
from games.memory.handlers import resume_pending_flips

from . import admin, fallback
from .config import load_config

CLEANUP_INTERVAL = 3600


async def cleanup_loop(*services):
    while True:
        for service in services:
            try:
                n = service.cleanup()
                if n:
                    logging.info("%s: удалено устаревших сессий: %s", type(service).__module__, n)
            except Exception:
                logging.exception("ошибка очистки")
        await asyncio.sleep(CLEANUP_INTERVAL)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = load_config()
    ttl = config.session_ttl_days * 86400
    memory_service = memory.create_service(config.db_path, ttl)
    phone_service = phone.create_service(config.db_path, ttl)
    oplatit_service = oplatit_button.create_service(config.db_path, ttl)
    bot = Bot(config.bot_token)
    # service — имя, под которым роутер мемори получает свой сервис
    dp = Dispatcher(config=config, service=memory_service, phone_service=phone_service,
                    oplatit_service=oplatit_service)
    dp.include_routers(admin.router, memory.router, phone.router, oplatit_button.router, fallback.router)
    await admin.setup_menu(bot, config)
    resume_pending_flips(bot, memory_service)
    cleanup = asyncio.create_task(cleanup_loop(memory_service, phone_service))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        cleanup.cancel()


if __name__ == "__main__":
    asyncio.run(main())
