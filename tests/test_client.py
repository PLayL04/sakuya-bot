"""Класс бота (client.py): регистрация команд, обработка ошибок, проверки развлекательных команд."""

import logging
from datetime import timedelta
from unittest import mock

import discord
import pytest
from discord import app_commands

from sakuya_bot.config import GUILD
from sakuya_bot.imaging.command import channel_allowed, fun_cooldown, in_fun_channel

from fakes import FakeInteraction


async def test_commands_are_registered(started):
    slash = {c.name: c.to_dict(started.tree) for c in started.tree.get_commands(guild=GUILD, type=discord.AppCommandType.chat_input)}
    assert set(slash) == {"settings", "schedule", "reaction", "quotes", "help"}
    # команды видны всем, доступ проверяет бот (admin_roles или «Управлять сервером»)
    assert all(p.get("default_member_permissions") is None for p in slash.values())
    # Discord отклонит описание длиннее 100 символов — проверяем всё, вместе с параметрами
    for command in slash.values():
        assert len(command["description"]) <= 100
        for sub in command.get("options", []):
            assert len(sub["description"]) <= 100, sub["name"]
            for option in sub.get("options", []):
                assert len(option["description"]) <= 100, f"{sub['name']} {option['name']}"
    menus = [c.name for c in started.tree.get_commands(guild=GUILD, type=discord.AppCommandType.message)]
    assert menus == ["Кофе", "Пиво", "Музей", "Взрыв", "Сжатие"]  # Discord разрешает не больше 5
    prefix = {c.name for c in started.commands}
    assert {"кофе", "пиво", "explode", "implode", "музей", "цитата", "анекдот", "помощь"} == prefix


@pytest.mark.parametrize("alias, name", [("взрыв", "explode"), ("сжатие", "implode"), ("латте", "кофе"),
                                         ("help", "помощь"), ("quote", "цитата")])
async def test_aliases(started, alias, name):
    assert started.get_command(alias).name == name


async def test_unexpected_error_is_answered(bot):
    it = FakeInteraction()
    error = app_commands.CommandInvokeError(it.command, KeyError("emoji"))
    await bot.on_tree_error(it, error)
    assert "Что-то пошло не так (KeyError)" in it.last


async def test_error_after_defer_goes_to_followup(bot):
    it = FakeInteraction()
    await it.response.defer()
    await bot.on_tree_error(it, app_commands.CommandInvokeError(it.command, ValueError()))
    assert it.kinds == ["defer", "followup"]


async def test_access_denied(bot):
    it = FakeInteraction()
    await bot.on_tree_error(it, app_commands.CheckFailure())
    assert "доступна только" in it.last


async def test_autocomplete_error_is_not_answered(bot):
    it = FakeInteraction()
    it.type = discord.InteractionType.autocomplete
    await bot.on_tree_error(it, app_commands.CommandInvokeError(it.command, KeyError()))
    assert it.replies == []


def test_fun_cooldown_from_settings(settings):
    settings(fun_cooldown=7)
    assert fun_cooldown(mock.Mock()).per == 7
    settings(fun_cooldown=0)
    assert fun_cooldown(mock.Mock()) is None


def test_fun_channels(settings):
    channel = mock.Mock(id=5, parent_id=None)
    thread = mock.Mock(id=50, parent_id=5)
    other = mock.Mock(id=6, parent_id=None)
    assert channel_allowed(other, set())  # пустой список — везде
    assert channel_allowed(channel, {5}) and channel_allowed(thread, {5}) and not channel_allowed(other, {5})
    settings(fun_channels=frozenset({5}))
    assert in_fun_channel(mock.Mock(channel=thread)) and not in_fun_channel(mock.Mock(channel=other))


# ---------- «Приложение не отвечает»: замер времени доставки и ответа ----------

def not_found(code):
    return discord.NotFound(mock.Mock(status=404, reason="Not Found"), {"code": code, "message": "Unknown"})


def called_ago(it, seconds):
    it.created_at = discord.utils.utcnow() - timedelta(seconds=seconds)
    return it


async def test_tree_records_delivery_time(bot, caplog):
    it = called_ago(FakeInteraction(), 0.4)
    assert await bot.tree.interaction_check(it)
    assert 0.3 < it.extras["delivered_after"] < 1.5
    assert "дошла до бота только через" not in caplog.text
    late = called_ago(FakeInteraction(), 2.6)
    await bot.tree.interaction_check(late)
    assert "дошла до бота только через 2.6 с" in caplog.text


async def test_expired_interaction_is_explained_not_answered(bot, caplog):
    it = called_ago(FakeInteraction(), 3.4)
    it.extras["delivered_after"] = 2.9
    await bot.on_tree_error(it, app_commands.CommandInvokeError(it.command, not_found(10062)))
    assert it.replies == []  # отвечать уже поздно — и не пытаемся
    assert "Discord не дождался ответа на /test" in caplog.text and "через 2.9 с после вызова" in caplog.text
    assert "ответ отклонён через 3.4 с" in caplog.text and "Traceback" not in caplog.text
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


async def test_other_not_found_is_still_an_error(bot):
    it = FakeInteraction()
    await bot.on_tree_error(it, app_commands.CommandInvokeError(it.command, not_found(10008)))
    assert "Что-то пошло не так (NotFound)" in it.last


async def test_completion_log_has_delivery_time(bot, caplog):
    caplog.set_level(logging.INFO)  # обычная запись о команде — уровня INFO, по умолчанию caplog её не ловит
    it = FakeInteraction()
    it.extras["delivered_after"] = 0.3
    await bot.on_app_command_completion(it, it.command)
    assert "Команда /test" in caplog.text and "дошла через 0.3 с" in caplog.text
