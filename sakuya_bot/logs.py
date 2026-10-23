"""Логирование: консоль + logs/sakuya-bot.log."""

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

import discord

from .config import LOG_DIR, TZ

log = logging.getLogger("sakuya-bot")


def setup_logging() -> None:
    """Логи в консоль и в logs/sakuya-bot.log (до 4 файлов по 5 МБ). Время — в часовом поясе TIMEZONE."""
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    formatter.converter = lambda ts: datetime.fromtimestamp(ts, TZ).timetuple()
    # иначе на Windows вывод в терминал IDE или в файл идёт в cp1251 и русский текст/эмодзи ломаются
    sys.stdout.reconfigure(encoding="utf-8")
    console = logging.StreamHandler(sys.stdout)
    LOG_DIR.mkdir(exist_ok=True)
    file = RotatingFileHandler(LOG_DIR / "sakuya-bot.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (console, file):
        handler.setFormatter(formatter)
        root.addHandler(handler)
    # бот не работает с голосовыми каналами — предупреждения о PyNaCl/davey только мешают
    logging.getLogger("discord.client").addFilter(lambda r: "voice will NOT be supported" not in r.getMessage())


def who(user: discord.abc.User) -> str:
    return f"{user} ({user.id})"
