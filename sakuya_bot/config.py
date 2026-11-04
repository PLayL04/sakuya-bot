"""Настройки из .env (секреты и пути) и пути к папкам проекта.

То, что меняет модерация (роли, каналы, кулдауны, часовой пояс), — в data/settings.toml, см. settings/.
"""

import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from dotenv import load_dotenv

load_dotenv()


def parse_ids(value: str) -> set[int]:
    """«111, 222 333» -> {111, 222, 333}: ID через запятую и/или пробел."""
    return {int(x) for x in re.split(r"[,\s]+", value) if x}


def require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"В .env не указан {name} — см. .env.example")
    return value


TOKEN = require("DISCORD_TOKEN")
GUILD_ID = int(require("GUILD_ID"))
GUILD = discord.Object(id=GUILD_ID)

BASE_DIR = Path(__file__).parent.parent
IMG_DIR = BASE_DIR / "img"
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
TEMPLATE_DIR = BASE_DIR / "template"
DOWNLOAD_DIR = BASE_DIR / "tmp"  # картинки и видео для команд с картинками: лежат тут только пока обрабатываются
# тексты для !цитата (*.txt); можно указать другую папку в QUOTES_DIR в .env
QUOTES_DIR = Path(os.getenv("QUOTES_DIR") or BASE_DIR / "quotes")
IMG_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _env_defaults() -> dict:
    """Старые ключи .env (ADMIN_ROLE_ID, FUN_CHANNEL_ID, TIMEZONE): из них создаётся data/settings.toml при первом
    запуске. Когда файл уже есть, эти ключи не читаются — настройки меняются в файле."""
    defaults = {}
    try:
        if roles := parse_ids(os.getenv("ADMIN_ROLE_ID", "")):
            defaults["admin_roles"] = frozenset(roles)
        if channels := parse_ids(os.getenv("FUN_CHANNEL_ID", "")):
            defaults["fun_channels"] = frozenset(channels)
    except ValueError as e:
        raise SystemExit(f"В .env в ADMIN_ROLE_ID или FUN_CHANNEL_ID не ID: {e}") from e
    if tz := os.getenv("TIMEZONE", "").strip():
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise SystemExit(f"В .env неизвестный часовой пояс TIMEZONE={tz}") from e
        defaults["timezone"] = tz
    return defaults


ENV_DEFAULTS = _env_defaults()
