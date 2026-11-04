"""Класс бота (client.py): регистрация команд, обработка ошибок, проверки развлекательных команд."""

from unittest import mock

import discord
import pytest
from discord import app_commands

from sakuya_bot.config import GUILD
from sakuya_bot.imaging.command import channel_allowed, fun_cooldown, in_fun_channel

from fakes import FakeInteraction


async def test_commands_are_registered(started):
    slash = {c.name: c.to_dict(started.tree) for c in started.tree.get_commands(guild=GUILD, type=discord.AppCommandType.chat_input)}
    assert set(slash) == {"settings", "schedule", "reaction", "help"}
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
    assert {"кофе", "пиво", "explode", "implode", "музей", "цитата", "помощь"} == prefix


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
