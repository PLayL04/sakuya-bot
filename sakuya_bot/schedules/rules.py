"""Правила расписаний: когда отправлять и как описать расписание словами. Без Discord — легко проверять отдельно."""

import calendar
import re
from datetime import datetime

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
