"""Когда отправлять расписание, разбор дат и времени, описание словами (schedules/rules.py)."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from sakuya_bot.schedules.rules import (describe_schedule, due_occurrence, is_due_today, keep_sent, last_passed_day,
                                        missed_once, once_passed, parse_date, parse_time)

TZ = ZoneInfo("Europe/Moscow")
GRACE = timedelta(minutes=30)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=TZ)


DAILY = {"kind": "daily", "time": "08:00", "last_sent": None}


@pytest.mark.parametrize("now, expected", [
    ("2026-11-04 08:00:05", "2026-11-04"),  # ровно в срок
    ("2026-11-04 07:59:50", None),          # рано
    ("2026-11-04 08:20", "2026-11-04"),     # бот перезапускался — догоняет в пределах SEND_GRACE
    ("2026-11-04 08:31", None),             # слишком поздно: «доброе утро» в обед не нужно
])
def test_daily_due(now, expected):
    assert due_occurrence(DAILY, at(now), GRACE) == expected


def test_already_sent_today():
    assert due_occurrence({**DAILY, "last_sent": "2026-11-04"}, at("2026-11-04 08:05"), GRACE) is None


def test_late_evening_catches_up_after_midnight():
    sched = {"kind": "daily", "time": "23:55", "last_sent": "2026-11-03"}
    assert due_occurrence(sched, at("2026-11-05 00:05"), GRACE) == "2026-11-04"


def test_weekly_only_on_its_day():
    sched = {"kind": "weekly", "weekday": 2, "time": "10:00", "last_sent": None}  # 2026-11-04 — среда
    assert due_occurrence(sched, at("2026-11-04 10:01"), GRACE) == "2026-11-04"
    assert due_occurrence(sched, at("2026-11-05 10:01"), GRACE) is None


@pytest.mark.parametrize("day, today, due", [
    (31, "2026-04-30", True),   # в апреле нет 31-го — отправляется в последний день
    (31, "2026-04-29", False),
    (15, "2026-04-15", True),
])
def test_monthly_short_month(day, today, due):
    assert is_due_today({"kind": "monthly", "day": day}, at(today + " 12:00")) is due


def test_yearly_feb_29_in_common_year():
    sched = {"kind": "yearly", "date": "02-29"}
    assert is_due_today(sched, at("2027-02-28 12:00"))
    assert is_due_today(sched, at("2028-02-29 12:00"))
    assert not is_due_today(sched, at("2028-02-28 12:00"))


def test_missed_once():
    once = {"kind": "once", "date": "2026-11-04", "time": "12:00", "last_sent": None}
    assert not missed_once(once, at("2026-11-04 12:10"), GRACE)
    assert missed_once(once, at("2026-11-04 12:31"), GRACE)
    assert not missed_once({**once, "date": "2027-01-01"}, at("2026-11-04 13:00"), GRACE)
    assert not missed_once({**once, "last_sent": "2026-11-04"}, at("2026-11-04 13:00"), GRACE)


@pytest.mark.parametrize("now, time", [("2026-11-04 00:10", "23:50"), ("2026-11-04 09:00", "08:00"),
                                       ("2026-11-04 07:00", "08:00")])
def test_new_schedule_is_not_sent_retroactively(now, time):
    sched = {"kind": "daily", "time": time}
    sched["last_sent"] = last_passed_day(sched, at(now))
    assert due_occurrence(sched, at(now), GRACE) is None


def test_new_schedule_same_minute_is_sent():
    sched = {"kind": "daily", "time": "08:00"}
    sched["last_sent"] = last_passed_day(sched, at("2026-11-04 08:00:10"))
    assert due_occurrence(sched, at("2026-11-04 08:00:30"), GRACE) == "2026-11-04"


@pytest.mark.parametrize("sched, expected", [
    ({"time": "15:00", "last_sent": "2026-11-04"}, "2026-11-04"),  # сегодня уже отправили — не повторять
    ({"time": "09:00", "last_sent": "2026-11-03"}, "2026-11-04"),  # новое время уже прошло — со следующего раза
    ({"time": "15:00", "last_sent": "2026-11-03"}, "2026-11-03"),  # новое время впереди — сегодня отправится
])
def test_keep_sent_after_edit_or_resume(sched, expected):
    assert keep_sent(sched, at("2026-11-04 12:00")) == expected


def test_once_passed():
    now = at("2026-11-04 12:00")
    assert once_passed({"kind": "once", "date": "2026-11-04", "time": "11:59"}, now)
    assert once_passed({"kind": "once", "date": "2026-11-04", "time": "12:00"}, now)
    assert not once_passed({"kind": "once", "date": "2026-11-04", "time": "12:01"}, now)
    assert not once_passed({"kind": "daily", "time": "08:00"}, now)


@pytest.mark.parametrize("text, expected", [("8:00", "08:00"), ("08:05", "08:05"), (" 23:59 ", "23:59"), ("0:00", "00:00")])
def test_parse_time(text, expected):
    assert parse_time(text) == expected


@pytest.mark.parametrize("text", ["24:00", "8:60", "8", "восемь", "08-00"])
def test_parse_time_rejects(text):
    with pytest.raises(ValueError, match="Пример"):
        parse_time(text)


TODAY = date(2026, 11, 4)


@pytest.mark.parametrize("text, kind, expected", [
    ("01.01", "yearly", "01-01"),
    ("1.1", "yearly", "01-01"),
    ("29.02", "yearly", "02-29"),
    ("31.12", "once", "2026-12-31"),      # без года — в этом году, если ещё не прошло
    ("01.01", "once", "2027-01-01"),      # уже прошло — в следующем году
    ("04.11", "once", "2026-11-04"),      # сегодня — сегодня
    ("29.02", "once", "2028-02-29"),      # ближайший високосный год
    ("01.01.2027", "once", "2027-01-01"),
])
def test_parse_date(text, kind, expected):
    assert parse_date(text, kind, TODAY) == expected


@pytest.mark.parametrize("text, kind, message", [
    ("01.01.2027", "yearly", "год не нужен"),
    ("31.02", "yearly", "Такой даты нет"),
    ("31.11.2026", "once", "Такой даты нет"),
    ("1 января", "once", "Пример"),
    ("2027-01-01", "yearly", "Пример"),
])
def test_parse_date_rejects(text, kind, message):
    with pytest.raises(ValueError, match=message):
        parse_date(text, kind, TODAY)


@pytest.mark.parametrize("sched, expected", [
    ({"kind": "daily", "time": "08:00"}, "каждый день в 08:00"),
    ({"kind": "weekly", "weekday": 2, "time": "10:00"}, "каждую среду в 10:00"),
    ({"kind": "monthly", "day": 1, "time": "10:00"}, "каждый месяц 1-го в 10:00"),
    ({"kind": "yearly", "date": "01-01", "time": "00:00"}, "каждый год 01.01 в 00:00"),
    ({"kind": "once", "date": "2027-01-01", "time": "00:00"}, "01.01.2027 в 00:00 (один раз)"),
    ({"time": "08:00"}, "каждый день в 08:00"),  # самые старые расписания были без kind
])
def test_describe_schedule(sched, expected):
    assert describe_schedule(sched) == expected
