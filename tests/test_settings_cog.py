"""Команды /settings (settings/cog.py) и проверка доступа (checks.py)."""

import pytest

from sakuya_bot.checks import is_admin
from sakuya_bot.settings import STORE, current
from sakuya_bot.settings.cog import SettingsCog, guild_warnings
from sakuya_bot.settings.model import Settings, render

from fakes import LOG_CHANNEL_ID, FakeChannel, FakeInteraction, attachment, call, fake_guild, member


@pytest.fixture
def cog(bot):
    STORE.load()
    return SettingsCog(bot)


def upload(cog, text: str, user=None, guild=None):
    it = FakeInteraction(user=user or member(manage=True), guild=guild or fake_guild(roles={111, 222, 333}))
    return call(SettingsCog.upload, cog, it, file=attachment("settings.toml", size=len(text.encode()), data=text.encode("utf-8-sig")))


async def test_upload_applies_and_reports(cog, log_channel):
    text = render(Settings(fun_cooldown=20, fun_channels=frozenset({5, 777}), log_channel=LOG_CHANNEL_ID))
    guild = fake_guild(channels=[FakeChannel(5), FakeChannel(LOG_CHANNEL_ID)])
    it = await upload(cog, text, guild=guild)
    assert current().fun_cooldown == 20 and current().fun_channels == {5, 777}
    assert "20 с" in it.last and "777" in it.last  # что изменилось и предупреждение про несуществующий канал
    assert "MadelineAlo" in it.last                 # и про эмодзи, которого на сервере нет
    assert any("изменяет настройки" in t and "20 с" in t for t in log_channel.texts)


async def test_broken_file_is_rejected(cog):
    it = await upload(cog, "[fun]\ncooldown = 2о\n")
    assert "не принят" in it.last and current() == Settings()


async def test_cannot_lock_yourself_out(cog):
    STORE.apply(render(Settings(admin_roles=frozenset({111}))))
    it = await upload(cog, render(Settings(admin_roles=frozenset({333}))), user=member(roles=[111]))
    assert "не останется доступа" in it.last and current().admin_roles == {111}


async def test_server_manager_can_clear_roles(cog):
    STORE.apply(render(Settings(admin_roles=frozenset({111}))))
    await upload(cog, render(Settings()), user=member(manage=True))
    assert current().admin_roles == frozenset()


async def test_not_utf8(cog):
    it = FakeInteraction()
    await call(SettingsCog.upload, cog, it, file=attachment("s.toml", data="[fun]\n# комментарий".encode("utf-16")))
    assert "UTF-8" in it.last


async def test_show_and_download(cog):
    it = await call(SettingsCog.show, cog)
    assert "Кулдаун развлекательных команд" in it.last and "Канал логов" in it.last
    it = await call(SettingsCog.download, cog)
    assert it.replies[-1][2]["file"].filename == "settings.toml"


def test_guild_warnings():
    guild = fake_guild(roles={1}, channels=[FakeChannel(LOG_CHANNEL_ID, send_messages=False)], emojis=["Wave"])
    assert guild_warnings(Settings(admin_roles=frozenset({1}), ping_emoji="Wave"), guild) == []
    warnings = guild_warnings(Settings(admin_roles=frozenset({2}), log_channel=LOG_CHANNEL_ID), guild)
    assert any("роли с ID `2`" in w for w in warnings)
    assert any("MadelineAlo" in w for w in warnings)
    assert any("канале логов" in w and "Отправлять сообщения" in w for w in warnings)
    assert any("канала логов" in w for w in guild_warnings(Settings(log_channel=1234), guild))


async def test_file_edited_on_disk_is_reported(cog, log_channel):
    import os, time
    STORE.path.write_text(render(Settings(fun_cooldown=42, log_channel=LOG_CHANNEL_ID)), encoding="utf-8")
    t = time.time() + 5
    os.utime(STORE.path, (t, t))
    await SettingsCog.watch_file.coro(cog)
    assert current().fun_cooldown == 42 and any("в файле на сервере" in t for t in log_channel.texts)


@pytest.mark.parametrize("user, roles, allowed", [
    (member(roles=[222]), {111, 222}, True),
    (member(roles=[999]), {111, 222}, False),
    (member(manage=True), set(), True),     # «Управлять сервером» — доступ всегда
    ("не участник", {111}, False),          # личные сообщения: не discord.Member
])
def test_is_admin(settings, user, roles, allowed):
    settings(admin_roles=frozenset(roles))
    assert is_admin(user) is allowed
