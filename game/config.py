"""Настройки из файла .env (рядом с папкой game) или из переменных окружения."""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read_env_file(path: pathlib.Path) -> dict[str, str]:
    if not path.exists():
        return {}
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


@dataclass
class Config:
    bot_token: str
    channel_id: int
    admin_id: int
    db_path: str
    session_ttl_days: int


def load_config() -> Config:
    env = {**_read_env_file(ROOT / ".env"), **os.environ}
    missing = [k for k in ("BOT_TOKEN", "CHANNEL_ID", "ADMIN_ID") if not env.get(k)]
    if missing:
        raise SystemExit(f"В .env не заполнено: {', '.join(missing)}")
    return Config(
        bot_token=env["BOT_TOKEN"],
        channel_id=int(env["CHANNEL_ID"]),
        admin_id=int(env["ADMIN_ID"]),
        db_path=env.get("DB_PATH") or str(ROOT / "game.db"),
        session_ttl_days=int(env.get("SESSION_TTL_DAYS") or 7),
    )
