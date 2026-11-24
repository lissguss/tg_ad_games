"""Проверка ботов на сервере: ssh root@HOST python3 - < deploy/check_server.py.

Только чтение настроек, журнала и Telegram getMe/getChat/getWebhookInfo.
Не печатает токены, не читает обновления ботов и не меняет состояние сервисов.
"""
from __future__ import annotations

import json
import pathlib
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

SERVICES = ("tg_ad_games", "tg_ad_games_prod")
EXPECTED_TEST_BOT = "tg_ad_game_bot"
EXPECTED_TEST_CHANNEL = "tg_ad_game_test"


def redact(text: str) -> str:
    return re.sub(r"[0-9]{5,}(?::|%3[Aa])[A-Za-z0-9_-]{15,}", "<BOT_TOKEN>", text)


def inspect_test_runtime() -> None:
    """Состояние, установленный код и последние ошибки только тестового сервиса."""
    folder = pathlib.Path("/opt/tg_ad_games")
    checks = (
        ("Состояние тестового сервиса", [
            "systemctl", "show", "tg_ad_games", "-p", "ActiveState", "-p", "SubState",
            "-p", "Result", "-p", "ExecMainStatus", "-p", "MainPID", "-p", "WorkingDirectory",
        ]),
        ("Версия библиотеки и доступные команды на сервере", [
            str(folder / ".venv/bin/python"), "-c",
            "import aiogram; from bot.admin import admin_commands; "
            "print('aiogram:', aiogram.__version__); "
            "print('Команды:', ', '.join('/' + c.command for c in admin_commands()))",
        ]),
        ("Доступность pytest", [str(folder / ".venv/bin/python"), "-m", "pytest", "--version"]),
        ("Последние записи тестового сервиса", [
            "journalctl", "-u", "tg_ad_games", "-n", "60", "--no-pager", "--output=short-iso",
        ]),
    )
    for title, command in checks:
        print(f"\n{title}:")
        try:
            result = subprocess.run(command, cwd=folder, capture_output=True, text=True,
                                    check=False, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f"  Проверка не выполнена ({type(error).__name__})")
            continue
        print(redact((result.stdout + result.stderr).strip()) or "  Нет записей")
        if result.returncode:
            print(f"  Код завершения проверки: {result.returncode}")


def inspect_test_instances() -> None:
    """Найти вторую копию тестового бота; ничего не останавливать."""
    for name in ("tg_ad_games", "tg_ad_game_emoji", "tg_ad_games_prod"):
        result = subprocess.run(
            ["systemctl", "show", name, "-p", "LoadState", "-p", "ActiveState",
             "-p", "MainPID", "-p", "WorkingDirectory"],
            capture_output=True, text=True, check=False,
        )
        print(f"\n{name}:\n" + redact(result.stdout.strip()))

    print("\nПроцессы ботов (без токенов):")
    processes = subprocess.run(["ps", "-eo", "pid=,ppid=,args="], capture_output=True,
                               text=True, check=False)
    found_test = 0
    for line in processes.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        pid, parent, command = parts
        try:
            args = shlex.split(command)
        except ValueError:
            continue
        if not args or not pathlib.Path(args[0]).name.lower().startswith("python"):
            continue
        try:
            folder = pathlib.Path(f"/proc/{pid}/cwd").resolve(strict=True)
        except OSError:
            continue
        module_bot = any(args[i:i + 2] == ["-m", "bot"] for i in range(len(args) - 1))
        script_bot = (len(args) > 1 and args[1].endswith(".py")
                      and ("bot" in pathlib.Path(args[1]).name or "tg_ad_game" in str(folder)))
        if not module_bot and not script_bot:
            continue
        env = read_env(folder / ".env")
        try:
            raw = pathlib.Path(f"/proc/{pid}/environ").read_bytes()
            env.update(item.decode(errors="replace").split("=", 1)
                       for item in raw.split(b"\0") if b"=" in item)
            cgroup = pathlib.Path(f"/proc/{pid}/cgroup").read_text()
        except OSError:
            cgroup = ""
        token = env.get("BOT_TOKEN", "")
        bot_id = token.split(":", 1)[0]
        bot_id = bot_id if bot_id.isdecimal() else "не определён"
        units = re.findall(r"/([^/\n]+\.service)(?:/|\n|$)", cgroup)
        unit = units[-1] if units else "не определён (возможен ручной запуск)"
        print(f"  PID={pid}, PPID={parent}, бот ID={bot_id}")
        print(f"    Папка: {folder}\n    Сервис: {unit}")
        if bot_id == "8649750168":
            found_test += 1
    print(f"\nНайдено процессов тестового бота 8649750168 на этом сервере: {found_test}")
    print("Если найден один, второй polling-процесс может быть на другой машине или в контейнере.")


def read_env(path: pathlib.Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    result = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def telegram(token: str, method: str, **params):
    try:
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/{method}",
            data=urllib.parse.urlencode(params).encode(),
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        print(f"  {method}: ошибка HTTP {error.code}")
        return None
    except Exception as error:
        # Текст исключения может содержать URL с токеном, поэтому не выводим его.
        print(f"  {method}: не удалось проверить ({type(error).__name__})")
        return None
    if not payload.get("ok"):
        print(f"  {method}: Telegram не подтвердил запрос")
        return None
    return payload["result"]


def inspect_logs(name: str) -> None:
    patterns = ("telegramconflicterror", "terminated by other getupdates request",
                "can't use getupdates method while webhook is active", "conflict:", "conflict -")
    for period, label in (("30 minutes ago", "30 минут"), ("24 hours ago", "24 часа")):
        result = subprocess.run(
            ["journalctl", "-u", name, "--since", period, "--no-pager", "--output=cat"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            print(f"  Конфликты в журнале за {label}: не удалось прочитать журнал")
            continue
        lines = [line.lower() for line in result.stdout.splitlines() if line.strip()]
        conflicts = sum(any(pattern in line for pattern in patterns) for line in lines)
        if lines:
            print(f"  Строк с ошибками конфликта за {label}: {conflicts}")
            polling = sum("terminated by other getupdates request" in line for line in lines)
            webhook = sum("can't use getupdates method while webhook is active" in line for line in lines)
            print(f"    Другой getUpdates: {polling}; установленный webhook: {webhook}")
        else:
            print(f"  Журнал за {label}: нет записей")
    # Сами строки журнала могут содержать URL с токенами, их не выводим.


def inspect_service(name: str):
    result = subprocess.run(
        ["systemctl", "show", name, "-p", "LoadState", "-p", "ActiveState", "-p", "SubState",
         "-p", "MainPID", "-p", "WorkingDirectory", "-p", "Environment", "-p", "EnvironmentFiles",
         "-p", "PassEnvironment", "-p", "UnsetEnvironment"],
        capture_output=True, text=True, check=False,
    )
    info = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    print(f"\nСервис: {name}")
    if info.get("LoadState") != "loaded":
        print("  Сервис не установлен")
        return None
    print(f"  Состояние: {info.get('ActiveState')} ({info.get('SubState')})")
    folder = pathlib.Path(info.get("WorkingDirectory") or f"/opt/{name}")
    print(f"  Папка: {folder}")
    env = read_env(folder / ".env")
    pid = int(info.get("MainPID") or 0)
    if pid:
        try:
            raw = pathlib.Path(f"/proc/{pid}/environ").read_bytes()
            process_env = dict(item.decode(errors="replace").split("=", 1)
                               for item in raw.split(b"\0") if b"=" in item)
        except OSError:
            print("  Не удалось прочитать окружение процесса; настройки не подтверждены")
            return None
        env.update(process_env)
        print("  Источник: .env и окружение работающего процесса")
    else:
        overrides = dict(item.split("=", 1) for item in shlex.split(info.get("Environment", ""))
                         if "=" in item)
        env.update(overrides)
        print("  Источник: .env и Environment из systemd; сервис сейчас не запущен")
        if any(info.get(key) for key in ("EnvironmentFiles", "PassEnvironment", "UnsetEnvironment")):
            print("  Есть дополнительные источники окружения systemd: настройки запуска не подтверждены")
    token = env.get("BOT_TOKEN")
    channel_id = env.get("CHANNEL_ID")
    if not token or not channel_id:
        print("  BOT_TOKEN или CHANNEL_ID отсутствует; настройки не подтверждены")
        return None
    bot = telegram(token, "getMe")
    channel = telegram(token, "getChat", chat_id=channel_id)
    if bot:
        print(f"  Бот: @{bot.get('username', 'без username')} (id {bot['id']})")
    if channel:
        username = "@" + channel["username"] if channel.get("username") else "без @username"
        print(f"  Канал: {channel.get('title', '')} / {username} (id {channel['id']})")
    webhook = telegram(token, "getWebhookInfo")
    if webhook is not None:
        if webhook.get("url"):
            host = urllib.parse.urlsplit(webhook["url"]).hostname or "не удалось определить"
            # Полный webhook URL часто содержит секрет в пути или параметрах.
            print(f"  Webhook: настроен, сервер {host}")
        else:
            print("  Webhook: не настроен (место запуска polling по этому признаку не определить)")
    inspect_logs(name)
    db = pathlib.Path(env.get("DB_PATH") or str(folder / "game.db"))
    if not db.is_absolute():
        db = folder / db
    db = db.resolve()
    print(f"  База: {db}")
    return {"token": token, "channel": channel and channel["id"], "db": str(db),
            "folder": str(folder.resolve()), "bot_username": bot and bot.get("username"),
            "channel_username": channel and channel.get("username"), "running": bool(pid)}


def main():
    results = [inspect_service(name) for name in SERVICES]
    if results[0]:
        test = results[0]
        print("\nОжидаемые настройки тестового сервиса:")
        print(f"  Бот @{EXPECTED_TEST_BOT}: "
              + ("совпадает" if test["bot_username"] == EXPECTED_TEST_BOT else "НЕ ПОДТВЕРЖДЁН"))
        print(f"  Канал @{EXPECTED_TEST_CHANNEL}: "
              + ("совпадает" if test["channel_username"] == EXPECTED_TEST_CHANNEL else "НЕ ПОДТВЕРЖДЁН"))
        if not test["running"]:
            print("  Сервис остановлен: сверена конфигурация, работающий бот пока не проверен")
    if all(results):
        first, second = results
        print("\nСравнение двух сервисов:")
        for label, key in (("Тот же токен бота", "token"), ("Тот же канал", "channel"),
                           ("Та же база", "db"), ("Та же папка", "folder")):
            if first[key] is None or second[key] is None:
                answer = "не удалось проверить"
            else:
                answer = "ДА" if first[key] == second[key] else "нет"
            print(f"  {label}: {answer}")


if __name__ == "__main__":
    if "--test-runtime" in sys.argv[1:]:
        inspect_test_runtime()
    elif "--test-instances" in sys.argv[1:]:
        inspect_test_instances()
    else:
        main()
