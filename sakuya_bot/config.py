"""Настройки из .env и пути к папкам проекта."""

import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from dotenv import load_dotenv

load_dotenv()


def parse_ids(value: str) -> set[int]:
    """«111, 222 333» -> {111, 222, 333}: ID через запятую и/или пробел."""
    return {int(x) for x in re.split(r"[,\s]+", value) if x}


TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = int(os.environ["GUILD_ID"])
GUILD = discord.Object(id=GUILD_ID)
# одна или несколько ролей через запятую; для доступа достаточно любой из них
ADMIN_ROLE_IDS = parse_ids(os.environ["ADMIN_ROLE_ID"])
if not ADMIN_ROLE_IDS:
    raise SystemExit("ADMIN_ROLE_ID в .env пустой — укажите хотя бы одну роль")
# каналы, где работает !кофе (через запятую); пусто — во всех каналах
COFFEE_CHANNEL_IDS = parse_ids(os.getenv("COFFEE_CHANNEL_ID", ""))
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))

BASE_DIR = Path(__file__).parent.parent
IMG_DIR = BASE_DIR / "img"
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
TEMPLATE_DIR = BASE_DIR / "template"
IMG_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
