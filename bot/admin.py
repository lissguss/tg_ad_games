"""Команды в личке с ботом: /start, /games, /stats и публикация игр (только для ADMIN_IDS).
Пост с игрой бот публикует в канал сам, от своего имени (он админ канала): пересланный пост
кнопки теряет. Перед публикацией бот присылает админу пост и спрашивает «Да / Нет».
Остальным /start отвечает, что игра идёт в канале; команды публикации для них молчат.
Те же команды показываются админам в кнопке «Меню» (setMyCommands).
/start <игра> открывает игру в чате с ботом — для всех: сюда ведёт кнопка поста, если личный экран не открылся."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    BotCommand, BotCommandScopeChat, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    InputRichMessage, Message,
)

from games import memory, oplatit_button, phone

from .config import Config

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == "private")

# игра → имя её сервиса в Dispatcher (см. bot/__main__.py)
GAMES = [(memory, "service"), (phone, "phone_service"), (oplatit_button, "oplatit_service")]

CONFIRM_QUESTION = "Вы уверены, что хотите опубликовать этот пост в канале {channel}?"
CONFIRM_YES, CONFIRM_NO = "publish:yes", "publish:no"

# (чат, сообщение с вопросом) → (игра, имя сервиса, html поста, снимок содержимого).
# В памяти: после перезапуска бота вопрос устаревает, команду нужно отправить ещё раз.
_pending: dict[tuple[int, int], tuple[object, str, str, object | None]] = {}
_channel_mention: str | None = None


def _is_admin(message: Message, config: Config) -> bool:
    return message.from_user is not None and message.from_user.id in config.admin_ids


async def channel_mention(bot: Bot, config: Config) -> str:
    """@username канала (или его название) для ответа не-админам. Запоминается после первого раза."""
    global _channel_mention
    if _channel_mention is None:
        try:
            chat = await bot.get_chat(config.channel_id)
            _channel_mention = f"@{chat.username}" if chat.username else f"«{chat.title}»"
        except Exception:
            log.warning("не удалось узнать имя канала %s", config.channel_id, exc_info=True)
            return ""
    return _channel_mention


def admin_commands() -> list[BotCommand]:
    commands = [BotCommand(command=game.COMMAND, description=f"Опубликовать «{game.TITLE}» в канал")
                for game, _ in GAMES]
    commands += [BotCommand(command="stats", description="Статистика по каждой игре"),
                 BotCommand(command="games", description="Список игр и команд")]
    return commands


async def set_admin_menu(bot: Bot, admin_id: int) -> None:
    try:
        await bot.set_my_commands(admin_commands(), scope=BotCommandScopeChat(chat_id=admin_id))
    except TelegramBadRequest as e:
        # админ ещё ни разу не писал боту — Telegram не знает такой чат; меню поставит его /start
        log.warning("меню для %s не установлено: %s", admin_id, e)


async def setup_menu(bot: Bot, config: Config) -> None:
    """Кнопка «Меню» с командами — только в личке с админами, остальные её не видят.
    При запуске — всем админам, и ещё раз при /start админа: так меню появится и у того,
    кто впервые написал боту уже после запуска."""
    for admin_id in config.admin_ids:
        await set_admin_menu(bot, admin_id)


def games_text() -> str:
    lines = ["Игры и команды публикации:"]
    lines += [f"/{game.COMMAND} — опубликовать «{game.TITLE}» в канал" for game, _ in GAMES]
    lines += ["", "/stats — статистика по каждой игре", "/games — этот список",
              "", "Все команды есть в кнопке «Меню» слева от поля ввода. "
              "Если её нет — закройте и снова откройте чат с ботом."]
    return "\n".join(lines)


def _game_by_start(payload: str | None):
    if not payload:
        return None
    return next(((g, name) for g, name in GAMES if getattr(g, "START", None) == payload), None)


@router.message(CommandStart())
async def on_start(message: Message, bot: Bot, config: Config, command: CommandObject, **data):
    # id в логе — так проще узнать id нового админа: он пишет боту /start
    log.info("start user=%s admin=%s args=%r", message.from_user and message.from_user.id,
             _is_admin(message, config), command.args)
    found = _game_by_start(command.args)
    if found is not None and message.from_user is not None:
        game, service_name = found
        await game.open_in_bot_chat(bot, data[service_name], message.from_user.id, message.chat.id,
                                    message.message_id)
        return
    if _is_admin(message, config):
        await set_admin_menu(bot, message.from_user.id)
        await message.answer(games_text())
    else:
        channel = await channel_mention(bot, config)
        where = f"в нашем канале {channel}" if channel else "в нашем канале"
        await message.answer(f"Игра проходит {where} — нажимайте кнопки в посте.")


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
        """Защита от дурака: сначала пост админу в личку и вопрос, в канал — только после «Да»."""
        if not _is_admin(message, config):
            return
        prepare = getattr(game, "prepare_publication", None)
        html, publication = prepare() if prepare else (game.post_html(), None)
        preview = await bot.send_rich_message(chat_id=message.chat.id, rich_message=InputRichMessage(html=html))
        if publication is not None:
            game.on_preview(data[service_name], preview.chat.id, preview.message_id, publication)
        # канал в вопросе — чтобы было видно, куда уйдёт пост; имя не узнали — показываем id из .env
        channel = await channel_mention(bot, config) or str(config.channel_id)
        question = await message.answer(CONFIRM_QUESTION.format(channel=channel), reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Да", callback_data=CONFIRM_YES),
                              InlineKeyboardButton(text="Нет", callback_data=CONFIRM_NO)]]))
        _pending[(message.chat.id, question.message_id)] = (game, service_name, html, publication)
        log.info("%s: предпросмотр для user=%s", game.COMMAND, message.from_user.id)
    return on_publish


for _game, _service_name in GAMES:
    router.message.register(_publisher(_game, _service_name), Command(_game.COMMAND))


@router.callback_query(F.data.in_({CONFIRM_YES, CONFIRM_NO}))
async def on_confirm(cq: CallbackQuery, bot: Bot, config: Config, **data):
    if cq.from_user.id not in config.admin_ids or cq.message is None:
        await cq.answer("Публиковать могут только админы")
        return
    # pop до всех await: второе нажатие «Да» не опубликует пост ещё раз
    pending = _pending.pop((cq.message.chat.id, cq.message.message_id), None)
    if pending is None:
        await cq.answer("Вопрос устарел — отправьте команду публикации ещё раз")
        await cq.message.edit_reply_markup(reply_markup=None)
        return
    game, service_name, html, publication = pending
    if cq.data == CONFIRM_NO:
        await cq.message.edit_text(f"«{game.TITLE}»: пост не опубликован.")
        await cq.answer()
        return
    try:
        sent = await bot.send_rich_message(chat_id=config.channel_id,
                                           rich_message=InputRichMessage(html=html))
    except TelegramAPIError as e:
        log.exception("%s: не удалось опубликовать", game.COMMAND)
        await cq.message.edit_text(f"«{game.TITLE}»: не удалось опубликовать — {e}\n"
                                   "Проверьте, что бот — админ канала с правом публикации.")
        await cq.answer()
        return
    if publication is None:
        game.on_published(data[service_name], sent.chat.id, sent.message_id)
    else:
        game.on_published(data[service_name], sent.chat.id, sent.message_id, publication=publication)
    log.info("%s: опубликован пост %s в %s (user=%s)", game.COMMAND, sent.message_id, sent.chat.id,
             cq.from_user.id)
    channel = await channel_mention(bot, config) or str(config.channel_id)
    await cq.message.edit_text(f"Игра «{game.TITLE}» опубликована в канал {channel}.")
    await cq.answer("Опубликовано")
