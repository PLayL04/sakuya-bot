"""Контекстное меню для картинок (menus.py), справка (help.py), очередь обработки и русские названия."""

import asyncio
import io
import time

import pytest
from PIL import Image

import sakuya_bot.imaging.command as command
from sakuya_bot.help import FUN_COMMANDS, help_text
from sakuya_bot.menus import MENU_EFFECTS
from sakuya_bot.schedules.cog import WEEKDAY_CHOICES

from fakes import FakeChannel, FakeInteraction, image_attachment, member, message_with


def png_bytes(size=(120, 80)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 100, 50)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def menu(started):
    return started.get_cog("MenuCog")


def effect(title: str):
    return next((name, eff) for t, name, eff in MENU_EFFECTS if t == title)


_user_ids = iter(range(1000, 10**6))


def new_user():
    """Каждый раз другой участник: кулдаун discord.py хранит по участнику и общий для всех копий команды."""
    return member(member_id=next(_user_ids))


async def use_menu(menu, title="Взрыв", msg=None, interaction=None):
    interaction = interaction or FakeInteraction(user=new_user())
    name, eff = effect(title)
    await menu.apply(interaction, msg or message_with(image_attachment(png_bytes())), name, eff)
    return interaction


# ---------- контекстное меню ----------

async def test_menu_replies_with_picture(menu):
    it = await use_menu(menu)
    kind, _, params = it.replies[-1]
    assert it.kinds == ["defer", "followup"] and params["file"].filename == "explode.png"


async def test_menu_without_picture(menu):
    it = await use_menu(menu, msg=message_with())
    assert "нет картинки" in it.last and it.replies[0][2]["ephemeral"]


async def test_menu_respects_fun_channels(menu, settings):
    settings(fun_channels=frozenset({77}))
    it = await use_menu(menu)
    assert "не работают" in it.last
    it = FakeInteraction(user=new_user())
    it.channel = FakeChannel(77)
    assert (await use_menu(menu, interaction=it)).kinds == ["defer", "followup"]


async def test_menu_shares_cooldown_with_command(menu, settings):
    settings(fun_cooldown=15)
    user = new_user()
    await use_menu(menu, "Кофе", interaction=FakeInteraction(user=user))
    second = await use_menu(menu, "Кофе", interaction=FakeInteraction(user=user))
    assert "Кофе ещё варится" in second.last
    other = await use_menu(menu, "Пиво", interaction=FakeInteraction(user=user))  # у другой команды — свой кулдаун
    assert other.kinds == ["defer", "followup"]


async def test_failed_menu_does_not_spend_cooldown(menu, settings):
    settings(fun_cooldown=15)
    user = new_user()
    broken = message_with(image_attachment(b"not a picture"))
    first = await use_menu(menu, msg=broken, interaction=FakeInteraction(user=user))
    assert "Не получилось обработать" in first.last
    retry = await use_menu(menu, interaction=FakeInteraction(user=user))
    assert retry.kinds == ["defer", "followup"]


# ---------- очередь и ограничение времени ----------

@pytest.fixture
def slow_effect(monkeypatch):
    """Обработка, которая идёт delay секунд (вместо настоящего эффекта)."""
    def set_delay(delay):
        def fake_run(effect, data, limit):
            time.sleep(delay)
            return "готово"
        monkeypatch.setattr(command, "run_effect", fake_run)
    return set_delay


async def test_queue_limit(slow_effect, monkeypatch):
    slow_effect(0.3)
    monkeypatch.setattr(command, "MAX_PENDING", 2)
    first = asyncio.create_task(command.render(None, b"", 0))
    second = asyncio.create_task(command.render(None, b"", 0))
    await asyncio.sleep(0.05)
    assert command.queue_full()
    with pytest.raises(command.Busy):
        await command.render(None, b"", 0)
    assert await first == "готово" and await second == "готово"
    assert not command.queue_full() and command._pending == 0


async def test_timeout_keeps_queue_until_thread_finishes(slow_effect, monkeypatch):
    slow_effect(0.4)
    monkeypatch.setattr(command, "PROCESS_TIMEOUT", 0.1)
    with pytest.raises(asyncio.TimeoutError):
        await command.render(None, b"", 0)
    assert command._pending == 1  # поток ещё работает — следующая картинка подождёт его
    await asyncio.sleep(0.5)
    assert command._pending == 0 and not command._processing.locked()


async def test_busy_and_timeout_texts(slow_effect, monkeypatch):
    attachment = image_attachment(png_bytes())
    msg = message_with(attachment)
    user = member()
    monkeypatch.setattr(command, "_pending", command.MAX_PENDING)
    assert await command.apply_effect(None, attachment, None, "кофе", user, msg) == command.BUSY_TEXT
    monkeypatch.setattr(command, "_pending", 0)
    slow_effect(0.3)
    monkeypatch.setattr(command, "PROCESS_TIMEOUT", 0.05)
    assert await command.apply_effect(None, attachment, None, "кофе", user, msg) == command.TIMEOUT_TEXT
    await asyncio.sleep(0.4)


async def test_menu_when_queue_is_full(menu, monkeypatch):
    monkeypatch.setattr(command, "_pending", command.MAX_PENDING)
    monkeypatch.setattr("sakuya_bot.menus.queue_full", command.queue_full)
    it = await use_menu(menu)
    assert it.last == command.BUSY_TEXT and it.kinds == ["response"]


# ---------- справка ----------

async def test_help_lists_every_fun_command_with_aliases(started):
    text = help_text(started, admin=False)
    for name in FUN_COMMANDS:
        assert f"`!{name}`" in text
    for alias in ("`!взрыв`", "`!сжатие`", "`!латте`", "`!quote`"):
        assert alias in text
    assert "Кофе / Пиво / Музей / Взрыв / Сжатие" in text
    assert "Управление ботом" not in text


async def test_help_covers_all_commands(started):
    """Новая !команда должна попасть в справку — иначе участники о ней не узнают."""
    assert {c.name for c in started.commands} - {"помощь"} == set(FUN_COMMANDS)


async def test_help_for_admin_and_limits(started, settings):
    settings(fun_channels=frozenset({5}), fun_cooldown=20)
    text = help_text(started, admin=True)
    assert "Управление ботом" in text and "/schedule" in text and "/settings" in text
    assert "<#5>" in text and "20 с" in text


async def test_slash_help_is_private(started):
    help_cog = started.get_cog("HelpCog")
    it = FakeInteraction(user=member(manage=True))
    await type(help_cog).help_slash.callback(help_cog, it)
    assert it.replies[0][2]["ephemeral"] and "Управление ботом" in it.last
    it = FakeInteraction(user=member(roles=[1]))
    await type(help_cog).help_slash.callback(help_cog, it)
    assert "Управление ботом" not in it.last


def test_help_fits_discord_message(started, settings):
    settings(fun_channels=frozenset(range(10**17, 10**17 + 20)))
    assert len(help_text(started, admin=True)) <= 2000


# ---------- русские названия ----------

def test_weekdays_in_russian():
    assert [c.name for c in WEEKDAY_CHOICES] == ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота",
                                                 "Воскресенье"]
    assert [c.value for c in WEEKDAY_CHOICES] == list(range(7))  # сохранённые расписания не меняются
