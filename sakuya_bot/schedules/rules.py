"""Правила расписаний: когда отправлять и как описать расписание словами. Без Discord — легко проверять отдельно."""

import calendar
import re
from datetime import date, datetime, time, timedelta

# kind: daily — каждый день; weekly — каждую неделю в день "weekday" (0 — понедельник);
# monthly — каждый месяц в день "day";
# yearly — каждый год в дату "date" (ММ-ДД); once — один раз в дату "date" (ГГГГ-ММ-ДД)

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?$")

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]  # для выбора в /schedule
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


def keep_sent(sched: dict, now: datetime) -> str:
    """last_sent после изменения расписания или снятия с паузы: не отправлять задним числом то, что уже прошло
    (как у нового расписания), но и не отправлять повторно, если за сегодня уже отправили."""
    return max(sched.get("last_sent") or "", last_passed_day(sched, now))


def once_passed(sched: dict, now: datetime) -> bool:
    """Время одноразового расписания уже наступило — создать или включить такое нельзя."""
    return sched.get("kind") == "once" and (sched["date"], sched["time"]) <= (now.date().isoformat(), now.strftime("%H:%M"))


def parse_time(text: str) -> str:
    """«8:00» -> «08:00». ValueError с текстом для ответа, если формат неверный."""
    m = TIME_RE.match(text.strip())
    if not m:
        raise ValueError("Неверный формат времени. Пример: `08:00`")
    return f"{int(m[1]):02d}:{m[2]}"


def parse_date(text: str, kind: str, today: date) -> str:
    """Дата для /schedule yearly («ДД.ММ» -> «ММ-ДД») и /schedule once («ДД.ММ.ГГГГ» или «ДД.ММ» -> «ГГГГ-ММ-ДД»).
    У once без года — ближайшая такая дата: в этом году, а если она уже прошла — в следующем.
    ValueError с текстом для ответа, если дата неверная."""
    m = DATE_RE.match(text.strip())
    if not m:
        example = "`01.01`" if kind == "yearly" else "`01.01.2027` или `01.01` (ближайшее 1 января)"
        raise ValueError(f"Неверная дата. Пример: {example}")
    day, month, year = int(m[1]), int(m[2]), m[3]
    if kind == "yearly" and year:
        raise ValueError("Для ежегодной отправки год не нужен: `01.01`. Один раз — команда `/schedule once`.")
    try:
        if kind == "yearly":
            date(2000, month, day)  # 2000 — високосный: 29.02 — допустимая ежегодная дата (в другие годы — 28.02)
            return f"{month:02d}-{day:02d}"
        if year:
            return date(int(year), month, day).isoformat()
        found = date(today.year, month, day) if (month, day) != (2, 29) or calendar.isleap(today.year) else None
        if found is None or found < today:
            year = today.year + 1
            while (month, day) == (2, 29) and not calendar.isleap(year):
                year += 1
            found = date(year, month, day)
        return found.isoformat()
    except ValueError as e:
        raise ValueError(f"Такой даты нет: {text.strip()}") from e


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
