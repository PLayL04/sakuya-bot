"""Команды /schedule и цикл отправки (schedules/cog.py) с поддельным Discord."""

from datetime import datetime, timedelta
from unittest import mock

import discord
import pytest

import sakuya_bot.schedules.cog as schedules_cog
from sakuya_bot.schedules.cog import ScheduleCog
from sakuya_bot.settings import current

from fakes import LOG_CHANNEL_ID, FakeChannel, FakeInteraction, attachment, call, fake_guild, http_error


@pytest.fixture
def cog(bot, settings):
    settings(log_channel=LOG_CHANNEL_ID)
    return bot.schedule_cog


def daily(cog, interaction=None, time="08:00", channel=None, **params):
    return call(ScheduleCog.daily, cog, interaction, time=time, channel=channel or FakeChannel(5), **params)


# ---------- создание ----------

async def test_create_with_upload_defers_then_answers(cog, img_dir, log_channel):
    it = await daily(cog, image=attachment("cat.png"), message="Доброе утро!")
    assert it.kinds == ["defer", "followup"]  # картинка скачивается — сначала «бот думает…»
    assert "Готово" in it.last and len(cog.schedules) == 1
    sched = cog.schedules[0]
    assert sched["kind"] == "daily" and sched["time"] == "08:00" and (img_dir / sched["image"]).exists()
    assert any("создаёт расписание" in t and "<@7>" in t for t in log_channel.texts)


async def test_create_with_existing_file_answers_at_once(cog, img_dir):
    (img_dir / "ready.png").write_bytes(b"x")
    it = await daily(cog, file="ready.png")
    assert it.kinds == ["response"] and cog.schedules[0]["image"] == "ready.png"


@pytest.mark.parametrize("params, needle", [
    ({"image": attachment(size=50 * 2**20)}, "слишком большой"),
    ({"image": attachment("notes.txt")}, "не картинка"),
    ({"file": "нет-такого.png"}, "не найден"),
    ({}, "Нужно загрузить картинку"),
    ({"time": "25:00", "file": "x.png"}, "формат времени"),
])
async def test_create_rejects(cog, params, needle):
    it = await daily(cog, **params)
    assert needle in it.last and not cog.schedules


async def test_create_checks_file_size_from_img(cog, img_dir):
    (img_dir / "big.png").write_bytes(b"x" * 3_000_000)
    it = await daily(cog, FakeInteraction(guild=fake_guild(filesize_limit=2 * 2**20)), file="big.png")
    assert "слишком большой" in it.last


@pytest.mark.parametrize("missing, title", [({"attach_files": False}, "Прикреплять файлы"),
                                            ({"send_messages": False}, "Отправлять сообщения"),
                                            ({"view_channel": False}, "Просматривать канал")])
async def test_create_checks_bot_permissions(cog, img_dir, missing, title):
    it = await daily(cog, channel=FakeChannel(5, **missing), image=attachment())
    assert title in it.last and not cog.schedules
    assert not list(img_dir.iterdir())  # при отказе картинка даже не скачивается


async def test_upload_failure(cog):
    it = await daily(cog, image=attachment(error=http_error()))
    assert "Не удалось скачать" in it.last and not cog.schedules


async def test_weekly_monthly(cog, img_dir):
    (img_dir / "a.png").write_bytes(b"x")
    await call(ScheduleCog.weekly, cog, weekday=4, time="18:00", channel=FakeChannel(5), file="a.png")
    await call(ScheduleCog.monthly, cog, day=31, time="10:00", channel=FakeChannel(5), file="a.png")
    assert [(s["kind"], s.get("weekday"), s.get("day")) for s in cog.schedules] == [("weekly", 4, None), ("monthly", None, 31)]


async def test_yearly_and_once(cog, img_dir):
    (img_dir / "a.png").write_bytes(b"x")
    await call(ScheduleCog.yearly, cog, date="01.01", time="00:00", channel=FakeChannel(5), file="a.png")
    await call(ScheduleCog.once, cog, date="31.12.2030", time="23:59", channel=FakeChannel(5), file="a.png")
    assert [(s["kind"], s["date"]) for s in cog.schedules] == [("yearly", "01-01"), ("once", "2030-12-31")]
    it = await call(ScheduleCog.yearly, cog, date="01.01.2027", time="00:00", channel=FakeChannel(5), file="a.png")
    assert "/schedule once" in it.last
    it = await call(ScheduleCog.once, cog, date="01.01.2020", time="10:00", channel=FakeChannel(5), file="a.png")
    assert "уже прошли" in it.last and len(cog.schedules) == 2


def test_old_date_command_is_gone(cog):
    names = {c.name for c in cog.app_command.commands}
    assert "date" not in names
    assert {"daily", "weekly", "monthly", "yearly", "once", "list", "edit", "pause", "resume", "remove", "test"} == names


# ---------- список ----------

async def test_long_list_is_split(cog, img_dir):
    (img_dir / "a.png").write_bytes(b"x")
    for _ in range(60):
        await daily(cog, file="a.png", message="Доброе утро! " * 40)
    it = await call(ScheduleCog.list_, cog)
    assert len(it.texts) > 1 and all(len(t) <= 2000 for t in it.texts)
    assert it.texts[0].startswith("Часовой пояс") and "…»" in it.texts[1]


# ---------- изменение, пауза, удаление ----------

@pytest.fixture
async def yearly(cog, img_dir):
    await call(ScheduleCog.yearly, cog, date="01.01", time="00:00", channel=FakeChannel(5), image=attachment("ny.png"),
               message="С Новым годом!")
    return cog.schedules[0]


async def test_edit_time_and_clear_message(cog, yearly, log_channel):
    it = await call(ScheduleCog.edit, cog, id=yearly["id"], time="9:30", message="-")
    assert yearly["time"] == "09:30" and yearly["message"] is None and "Готово" in it.last
    assert any("изменяет расписание" in t and "→" in t for t in log_channel.texts)


async def test_edit_date(cog, yearly):
    await call(ScheduleCog.edit, cog, id=yearly["id"], date="08.03")
    assert yearly["date"] == "03-08"


@pytest.mark.parametrize("params, needle", [
    ({"weekday": 2}, "нет дня недели"),
    ({"day": 5}, "нет дня месяца"),
    ({}, "Укажите, что изменить"),
    ({"time": "99:99"}, "формат времени"),
    ({"date": "01.01.2027"}, "год не нужен"),
])
async def test_edit_rejects(cog, yearly, params, needle):
    before = dict(yearly)
    it = await call(ScheduleCog.edit, cog, id=yearly["id"], **params)
    assert needle in it.last and yearly == before


async def test_edit_channel_checks_permissions(cog, yearly):
    it = await call(ScheduleCog.edit, cog, id=yearly["id"], channel=FakeChannel(6, attach_files=False))
    assert "Прикреплять файлы" in it.last and yearly["channel_id"] == 5
    await call(ScheduleCog.edit, cog, id=yearly["id"], channel=FakeChannel(6))
    assert yearly["channel_id"] == 6


async def test_edit_image_removes_old_upload(cog, yearly, img_dir):
    old = yearly["image"]
    await call(ScheduleCog.edit, cog, id=yearly["id"], image=attachment("new.png"))
    assert yearly["image"] != old and not (img_dir / old).exists() and (img_dir / yearly["image"]).exists()


async def test_edit_unknown(cog):
    assert "не найдено" in (await call(ScheduleCog.edit, cog, id="nope", time="10:00")).last


async def test_remove_and_cleanup(cog, yearly, img_dir, log_channel):
    image = yearly["image"]
    it = await call(ScheduleCog.remove, cog, id=yearly["id"])
    assert "удалено" in it.last and not cog.schedules and not (img_dir / image).exists()
    assert any("удаляет расписание" in t for t in log_channel.texts)


async def test_pause_and_resume(cog, yearly, log_channel):
    it = await call(ScheduleCog.pause, cog, id=yearly["id"])
    assert yearly["paused"] and "на паузе" in it.last
    assert "уже на паузе" in (await call(ScheduleCog.pause, cog, id=yearly["id"])).last
    listing = await call(ScheduleCog.list_, cog)
    assert "на паузе" in listing.last
    it = await call(ScheduleCog.resume, cog, id=yearly["id"])
    assert "снова работает" in it.last and "paused" not in yearly
    assert "и так работает" in (await call(ScheduleCog.resume, cog, id=yearly["id"])).last
    assert any("ставит на паузу" in t for t in log_channel.texts) and any("снимает с паузы" in t for t in log_channel.texts)


async def test_resume_skips_what_was_missed(cog, yearly):
    now = datetime.now(current().tz)
    yearly.update(kind="daily", time=(now - timedelta(minutes=5)).strftime("%H:%M"), last_sent="2000-01-01", paused=True)
    yearly.pop("date")
    await call(ScheduleCog.resume, cog, id=yearly["id"])
    assert yearly["last_sent"] >= (now - timedelta(days=1)).date().isoformat()
    assert schedules_cog.due_occurrence(yearly, now, schedules_cog.SEND_GRACE) is None  # сразу после resume не уходит


async def test_resume_passed_once_is_refused(cog, yearly):
    yearly.update(kind="once", date="2020-01-01", paused=True)
    it = await call(ScheduleCog.resume, cog, id=yearly["id"])
    assert "уже прошла" in it.last and yearly["paused"]


async def test_test_command(cog, yearly, channels):
    it = await call(ScheduleCog.test, cog, id=yearly["id"])
    assert it.last == "Отправлено." and channels[5].sent[-1]["content"] == "С Новым годом!"
    yearly["image"] = "пропал.png"
    assert "не найден" in (await call(ScheduleCog.test, cog, id=yearly["id"])).last


# ---------- цикл отправки ----------

@pytest.fixture
def clock(monkeypatch):
    """Подменяет «сейчас» для цикла отправки: clock("2026-11-04 12:00:10")."""
    def set_now(text):
        moment = datetime.fromisoformat(text).replace(tzinfo=current().tz)
        monkeypatch.setattr(schedules_cog, "datetime", mock.Mock(now=lambda tz=None: moment))
    return set_now


@pytest.fixture
def once(cog, img_dir):
    (img_dir / "x.png").write_bytes(b"x")
    cog.schedules.append({"id": "a", "kind": "once", "date": "2026-11-04", "time": "12:00", "channel_id": 5,
                          "image": "x.png", "message": "Привет", "last_sent": None})
    return cog.schedules[0]


async def tick(cog):
    await ScheduleCog.scheduler.coro(cog)


async def test_once_is_sent_and_removed(cog, once, clock, channels):
    clock("2026-11-04 12:00:10")
    await tick(cog)
    assert channels[5].sent[-1]["content"] == "Привет" and cog.schedules == []


async def test_temporary_error_is_retried_then_marked_missed(cog, once, clock, monkeypatch, log_channel):
    monkeypatch.setattr(cog, "send_schedule", mock.AsyncMock(side_effect=http_error(500)))
    clock("2026-11-04 12:00:10")
    await tick(cog)
    assert once["last_sent"] is None  # повторит через 20 секунд
    clock("2026-11-04 12:31")
    await tick(cog)
    assert cog.schedules and once["last_sent"] == "2026-11-04"  # пропущено, но не удалено молча
    assert any("пропущено" in t for t in log_channel.texts)


@pytest.mark.parametrize("error, reason", [
    (discord.Forbidden(mock.Mock(status=403, reason="x"), "нет прав"), "нет канала или прав"),
    (KeyError("странно"), "неожиданная ошибка (KeyError)"),
])
async def test_permanent_errors_are_reported(cog, once, clock, monkeypatch, log_channel, error, reason):
    monkeypatch.setattr(cog, "send_schedule", mock.AsyncMock(side_effect=error))
    clock("2026-11-04 12:00:10")
    await tick(cog)  # не падает: остальные расписания продолжают работать
    assert once["last_sent"] == "2026-11-04" and cog.schedules
    assert any("не отправлено" in t and reason in t for t in log_channel.texts)


async def test_missing_file_is_reported(cog, once, clock, img_dir, log_channel):
    (img_dir / "x.png").unlink()
    clock("2026-11-04 12:00:10")
    await tick(cog)
    assert any("не найден в папке img" in t for t in log_channel.texts)


async def test_paused_is_not_sent(cog, once, clock, channels):
    once["paused"] = True
    clock("2026-11-04 12:00:10")
    await tick(cog)
    assert not channels[5].sent and once["last_sent"] is None


async def test_deleted_while_sending(cog, once, clock, monkeypatch):
    async def send_and_delete(sched):
        cog.schedules.remove(sched)  # модератор удалил расписание, пока шла отправка
    monkeypatch.setattr(cog, "send_schedule", send_and_delete)
    clock("2026-11-04 12:00:10")
    await tick(cog)  # не падает на повторном удалении
    assert cog.schedules == []
