"""Запуск на хостинге агентства: python -m game. Локально и через systemd по-прежнему python -m bot."""
import asyncio

from bot.__main__ import main

asyncio.run(main())
