"""Общая подготовка тестов: окружение без настоящего токена, данные во временной папке, поддельный Discord.

Тесты не подключаются к Discord и не трогают настоящие data/, img/ и logs/ проекта."""

import os

# до импорта бота: config.py требует токен и ID сервера, а старые ключи .env не должны попасть в тесты
# (load_dotenv не перезаписывает уже заданные переменные, так что настоящий .env, если он есть, не прочитается)
os.environ["DISCORD_TOKEN"] = "test-token"
os.environ["GUILD_ID"] = "1"
for key in ("ADMIN_ROLE_ID", "FUN_CHANNEL_ID", "TIMEZONE", "QUOTES_DIR"):
    os.environ[key] = ""

from unittest import mock  # noqa: E402

import discord  # noqa: E402
import pytest  # noqa: E402

import sakuya_bot.reactions.cog as reactions_cog  # noqa: E402
import sakuya_bot.schedules.cog as schedules_cog  # noqa: E402
import sakuya_bot.storage as storage  # noqa: E402
from sakuya_bot.settings import STORE  # noqa: E402
from sakuya_bot.settings.model import Settings  # noqa: E402

from fakes import LOG_CHANNEL_ID, FakeChannel  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Каждый тест — со своими пустыми data/ и img/ во временной папке и настройками по умолчанию."""
    img = tmp_path / "img"
    img.mkdir()
    for module in (storage, schedules_cog, reactions_cog):
        monkeypatch.setattr(module, "IMG_DIR", img)
    monkeypatch.setattr(storage, "UPLOADS_FILE", tmp_path / "uploads.json")
    monkeypatch.setattr(schedules_cog, "SCHEDULES_FILE", tmp_path / "schedules.json")
    monkeypatch.setattr(reactions_cog, "REACTIONS_FILE", tmp_path / "reactions.json")
    monkeypatch.setattr(STORE, "path", tmp_path / "settings.toml")
    monkeypatch.setattr(STORE, "settings", Settings())
    monkeypatch.setattr(STORE, "_mtime", None)
    return tmp_path


@pytest.fixture
def img_dir(isolated):
    return isolated / "img"


@pytest.fixture
def settings(monkeypatch):
    """settings(log_channel=…) — задать настройки для теста (остальные — по умолчанию)."""
    def apply(**values):
        monkeypatch.setattr(STORE, "settings", Settings(**values))
        return STORE.settings
    return apply


@pytest.fixture
def channels():
    """Каналы, которые «видит» бот: ID -> FakeChannel. Канал логов есть всегда."""
    return {LOG_CHANNEL_ID: FakeChannel(LOG_CHANNEL_ID), 5: FakeChannel(5)}


@pytest.fixture
def log_channel(channels):
    return channels[LOG_CHANNEL_ID]


@pytest.fixture
def bot(channels):
    """Настоящий класс бота без подключения к Discord: каналы и эмодзи — подставные."""
    from sakuya_bot.client import SakuyaBot

    b = SakuyaBot()
    b.get_channel = channels.get
    b.fetch_channel = mock.AsyncMock(side_effect=discord.NotFound(mock.Mock(status=404, reason="Not Found"), "нет канала"))
    b.get_emoji = lambda emoji_id: object() if emoji_id < 900_000_000_000_000_000 else None  # «чужие» эмодзи — с ID от 9…
    yield b
    for cog in (b.schedule_cog, b.reaction_cog):  # циклы, запущенные setup_hook, не должны пережить тест
        if hasattr(cog, "scheduler"):
            cog.scheduler.cancel()
    b.heartbeat.cancel()
