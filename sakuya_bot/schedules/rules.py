"""Правила расписаний: когда отправлять и как описать расписание словами. Без Discord — легко проверять отдельно."""

import calendar
import re
from datetime import date, datetime, time, timedelta

# kind: daily — каждый день; weekly — каждую неделю в день "weekday" (0 — понедельник);
# monthly — каждый месяц в день "day";
# yearly — каждый год в дату "date" (ММ-ДД); once — один раз в дату "date" (ГГГГ-ММ-ДД)

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?$")

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# для фразы «каждый понедельник», «каждую среду», «каждое воскресенье»
WEEKDAYS_EVERY = [
    "каждый понедельник", "каждый вторник", "каждую среду", "каждый четверг",
    "каждую пятницу", "каждую субботу", "каждое воскресенье",
]


def is_due_today(sched: dict, now: datetime) -> bool:
    kind = sched.get("kind", "daily")
    if kind == "weekly":
        return now.weekday() == sched["weekday"]
    if kind == "monthly":
        # если в месяце нет такого дня (31-е в апреле), отправляем в последний день месяца
        last_day = calendar.monthrange(now.year, now.month)[1]
        return now.day == min(sched["day"], last_day)
    if kind == "yearly":
        month, day = map(int, sched["date"].split("-"))
        if (month, day) == (2, 29) and not calendar.isleap(now.year):
            day = 28
        return (now.month, now.day) == (month, day)
    if kind == "once":
        return now.date().isoformat() == sched["date"]
    return True


def occurrence(sched: dict, day: date, tz) -> datetime:
    """Момент отправки расписания в этот день (в часовом поясе tz)."""
    h, m = map(int, sched["time"].split(":"))
    return datetime.combine(day, time(h, m), tzinfo=tz)


def due_occurrence(sched: dict, now: datetime, grace: timedelta) -> str | None:
    """День (ГГГГ-ММ-ДД), за который расписание пора отправить, или None.

    Пора — если момент отправки уже наступил, но прошло меньше grace, и за этот день ещё не отправляли (last_sent).
    Так отправка не теряется, если бот в нужную минуту переподключался или перезапускался.
    Проверяется и вчерашний день: расписание на 23:55 отправится и в 00:05, если бот был недоступен.
    """
    for day in (now.date(), now.date() - timedelta(days=1)):
        moment = occurrence(sched, day, now.tzinfo)
        if moment <= now < moment + grace and sched.get("last_sent") != day.isoformat() and is_due_today(sched, moment):
            return day.isoformat()
    return None


def missed_once(sched: dict, now: datetime, grace: timedelta) -> bool:
    """Одноразовое расписание, время которого прошло больше grace назад, а отправки так и не было."""
    if sched.get("kind") != "once" or sched.get("last_sent") == sched["date"]:
        return False
    return now >= occurrence(sched, date.fromisoformat(sched["date"]), now.tzinfo) + grace


def last_passed_day(sched: dict, now: datetime) -> str:
    """Для нового расписания: день, отправка за который уже прошла и не должна случиться задним числом.
    Сегодня, если время сегодня уже прошло (в ту же минуту — ещё отправится), иначе вчера."""
    day = now.date() if now.strftime("%H:%M") > sched["time"] else now.date() - timedelta(days=1)
    return day.isoformat()


def describe_schedule(sched: dict) -> str:
    kind = sched.get("kind", "daily")
    time = sched["time"]
    if kind == "weekly":
        return f"{WEEKDAYS_EVERY[sched['weekday']]} в {time}"
    if kind == "monthly":
        return f"каждый месяц {sched['day']}-го в {time}"
    if kind == "yearly":
        month, day = sched["date"].split("-")
        return f"каждый год {day}.{month} в {time}"
    if kind == "once":
        year, month, day = sched["date"].split("-")
        return f"{day}.{month}.{year} в {time} (один раз)"
    return f"каждый день в {time}"
