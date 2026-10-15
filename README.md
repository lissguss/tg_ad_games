# Игра «Мемори» в посте Telegram-канала

Бот публикует в канал пост с полем 3 × 4. Каждый читатель играет прямо в посте в свою игру, которую видит только он.

Команды в этой инструкции запускаются в приложении «Терминал» на Mac. В блоках с пометкой «на сервере» команды вводятся после входа на сервер через `ssh`.

## Настройки (.env)

Файл `.env` лежит в папке проекта:

```
BOT_TOKEN=токен от BotFather
CHANNEL_ID=id канала, например -1003844288830
ADMIN_ID=ваш id в Telegram (только ему бот разрешает /publish)
SESSION_TTL_DAYS=7        # необязательно: через сколько дней без активности игра удаляется
```

## Новый бот (если нужен)

1. В Telegram откройте @BotFather → `/newbot` → придумайте имя и username.
2. Скопируйте токен в `BOT_TOKEN` в `.env`.
3. В канале: Настройки → Администраторы → Добавить → найдите бота → оставьте право «Публикация сообщений».

Один бот может работать только в одном месте. Если тот же бот запущен на другом компьютере, остановите его там.

## Первая установка на сервер

**1. На сервере:** создать папку и проверить Python (нужен 3.11 или новее).

```bash
ssh root@72.56.22.151
mkdir -p /opt/tg_ad_game_emoji
python3 --version
apt update && apt install -y python3-venv rsync
exit
```

**2. На Mac:** залить файлы (без локальной базы и служебных папок).

```bash
rsync -av --exclude .venv --exclude '*.db' --exclude __pycache__ --exclude .pytest_cache ~/tg_ad_game_emoji/ root@72.56.22.151:/opt/tg_ad_game_emoji/
```

**3. На сервере:** установить зависимости, проверить тесты, включить автозапуск.

```bash
ssh root@72.56.22.151
cd /opt/tg_ad_game_emoji
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
cp deploy/tg_ad_game_emoji.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tg_ad_game_emoji
systemctl status tg_ad_game_emoji
```

В выводе `status` должно быть `active (running)`. Бот сам перезапускается после сбоя и после перезагрузки сервера.

## Публикация игры

Напишите боту в личку `/publish` — пост появится в канале из `.env`.

Текст поста лежит в файле `post.txt`. Абзацы разделяются пустой строкой, можно использовать `<b>жирный</b>` и `<i>курсив</i>`. Строка `[ПОЛЕ]` — место для кнопок. Изменения применяются без перезапуска, но только к новым публикациям.

Чтобы публиковать в боевой канал: поменяйте `CHANNEL_ID` в `.env` на сервере, добавьте бота админом в этот канал и перезапустите бота (см. ниже).

## Обновление

**На Mac:** залить изменённые файлы.

```bash
rsync -av --exclude .venv --exclude '*.db' --exclude __pycache__ --exclude .pytest_cache ~/tg_ad_game_emoji/ root@72.56.22.151:/opt/tg_ad_game_emoji/
```

**На сервере:** перезапустить бота. Незаконченные игры продолжатся с того же места.

```bash
ssh root@72.56.22.151 systemctl restart tg_ad_game_emoji
```

Если менялся `requirements.txt`, перед перезапуском выполните на сервере `/opt/tg_ad_game_emoji/.venv/bin/pip install -r /opt/tg_ad_game_emoji/requirements.txt`.

## Логи

```bash
ssh root@72.56.22.151 journalctl -u tg_ad_game_emoji -n 100
```

Смотреть в реальном времени (выход — Ctrl+C):

```bash
ssh root@72.56.22.151 journalctl -u tg_ad_game_emoji -f
```

## Остановить бота

```bash
ssh root@72.56.22.151 systemctl stop tg_ad_game_emoji
```
