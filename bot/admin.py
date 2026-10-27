"""Команды в личке с ботом: /start, /games, /stats и публикация игр (только для ADMIN_IDS).
Пост с игрой бот публикует в канал сам, от своего имени (он админ канала): пересланный пост
кнопки теряет.
Те же команды показываются админам в кнопке «Меню» (setMyCommands).
/start <игра> открывает игру в чате с ботом — для всех: сюда ведёт кнопка поста, если личный экран не открылся."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BotCommand, BotCommandScopeChat, InputRichMessage, Message

from games import memory, phone

from .config import Config

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == "private")

# игра → имя её сервиса в Dispatcher (см. bot/__main__.py)
GAMES = [(memory, "service"), (phone, "phone_service")]


def _is_admin(message: Message, config: Config) -> bool:
    return message.from_user is not None and message.from_user.id in config.admin_ids


def admin_commands() -> list[BotCommand]:
    commands = [BotCommand(command=game.COMMAND, description=f"Опубликовать «{game.TITLE}» в канал")
                for game, _ in GAMES]
    commands += [BotCommand(command="stats", description="Статистика по каждой игре"),
                 BotCommand(command="games", description="Список игр и команд")]
    return commands


async def setup_menu(bot: Bot, config: Config) -> None:
    """Кнопка «Меню» с командами — только в личке с админами, остальные её не видят."""
    for admin_id in config.admin_ids:
        try:
            await bot.set_my_commands(admin_commands(), scope=BotCommandScopeChat(chat_id=admin_id))
        except TelegramBadRequest as e:
            # админ ещё ни разу не писал боту — Telegram не знает такой чат
            log.warning("меню для %s не установлено: %s", admin_id, e)


def games_text() -> str:
    lines = ["Игры и команды публикации:"]
    lines += [f"/{game.COMMAND} — опубликовать «{game.TITLE}» в канал" for game, _ in GAMES]
    lines += ["", "/stats — статистика по каждой игре", "/games — этот список",
              "", "Все команды есть в кнопке «Меню» слева от поля ввода."]
    return "\n".join(lines)


def _game_by_start(payload: str | None):
    return next(((g, name) for g, name in GAMES if g.START == payload), None)


@router.message(CommandStart())
async def on_start(message: Message, bot: Bot, config: Config, command: CommandObject, **data):
    found = _game_by_start(command.args)
    if found is not None and message.from_user is not None:
        game, service_name = found
        await game.open_in_bot_chat(bot, data[service_name], message.from_user.id, message.chat.id,
                                    message.message_id)
        return
    if _is_admin(message, config):
        await message.answer(games_text())
    else:
        await message.answer("Привет! Здесь игры из канала: нажмите любую кнопку под постом с игрой — "
                             "она откроется тут.")


@router.message(Command("games"))
async def on_games(message: Message, config: Config):
    if _is_admin(message, config):
        await message.answer(games_text())


@router.message(Command("stats"))
async def on_stats(message: Message, config: Config, **data):
    if not _is_admin(message, config):
        return
    blocks = [f"«{game.TITLE}»\n{game.stats(data[service_name])}" for game, service_name in GAMES]
    await message.answer("\n\n".join(blocks))


def _publisher(game, service_name: str):
    async def on_publish(message: Message, bot: Bot, config: Config, **data):
        if not _is_admin(message, config):
            return
        sent = await bot.send_rich_message(
            chat_id=config.channel_id, rich_message=InputRichMessage(html=game.post_html()),
        )
        game.on_published(data[service_name], config.channel_id, sent.message_id)
        log.info("%s: опубликован пост %s", game.COMMAND, sent.message_id)
        await message.answer(f"Игра «{game.TITLE}» опубликована в канал.")
    return on_publish


for _game, _service_name in GAMES:
    router.message.register(_publisher(_game, _service_name), Command(_game.COMMAND))
