"""Команды /schedule и фоновый цикл, который отправляет картинки по расписанию."""

import asyncio
import uuid
from datetime import datetime, timedelta

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..checks import SEND_FILES_PERMISSIONS, AdminOnly, missing_permissions
from ..config import DATA_DIR, IMG_DIR
from ..logs import log, who
from ..modlog import notify
from ..replies import respond, respond_lines
from ..settings import current
from ..storage import list_images, load_json, receive_image, save_json
from .rules import (WEEKDAYS, describe_schedule, due_occurrence, keep_sent, last_passed_day, missed_once, once_passed,
                    parse_date, parse_time)

SCHEDULES_FILE = DATA_DIR / "schedules.json"
# сколько после назначенного времени ещё можно отправить (бот перезапускался, сеть, сбой Discord);
# позже — отправка за этот день пропускается, чтобы «доброе утро» не пришло в обед
SEND_GRACE = timedelta(minutes=30)
LIST_MESSAGE_PREVIEW = 80  # сколько символов текста к картинке показывать в /schedule list
CLEAR = "-"  # в /schedule edit message:- — убрать текст

# описания общих параметров команд создания
TIME_HELP = "Время в формате ЧЧ:ММ, например 08:00"
CHANNEL_HELP = "Канал, куда отправлять"
IMAGE_HELP = "Загрузить новую картинку"
FILE_HELP = "Или выбрать уже существующую из папки img"
MESSAGE_HELP = "Текст к картинке (необязательно)"
WEEKDAY_CHOICES = [app_commands.Choice(name=name, value=i) for i, name in enumerate(WEEKDAYS)]


def shorten(text: str, limit: int = LIST_MESSAGE_PREVIEW) -> str:
    text = " ".join(text.split())  # переносы строк ломают вид списка
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summary(sched: dict) -> str:
    """«`a1b2c3` — каждый день в 08:00 в #общий: `cat.png`» — для ответов и канала логов."""
    return (f"`{sched['id']}` — **{describe_schedule(sched)}** в <#{sched['channel_id']}>: `{sched['image']}`"
            + (f" — «{shorten(sched['message'])}»" if sched.get("message") else "")
            + (" — ⏸️ на паузе" if sched.get("paused") else ""))


def channel_problem(channel: discord.abc.GuildChannel, guild: discord.Guild) -> str | None:
    """Сможет ли бот отправлять картинки в канал. Проверяем сразу, чтобы ошибка не всплыла только в момент отправки."""
    if missing := missing_permissions(channel, guild.me, SEND_FILES_PERMISSIONS):
        return (f"У бота нет прав в {channel.mention}: {', '.join(missing)}. "
                "Выдайте их боту (или его роли) в настройках канала и попробуйте снова.")
    return None


class SendFailed(Exception):
    """Отправить не получится, и повтор не поможет: нет файла и т. п. Текст — причина, для лога и канала логов."""


@app_commands.guild_only()
class ScheduleCog(AdminOnly, commands.GroupCog, group_name="schedule",
                  group_description="Автоматическая отправка картинок по расписанию"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.schedules: list[dict] = load_json(SCHEDULES_FILE)

    def save(self) -> None:
        save_json(SCHEDULES_FILE, self.schedules)

    async def cog_load(self):
        self.scheduler.start()

    async def cog_unload(self):
        self.scheduler.cancel()

    # ---------- отправка ----------

    async def send_schedule(self, sched: dict) -> None:
        path = IMG_DIR / sched["image"]
        if not path.exists():
            raise SendFailed(f"файл `{sched['image']}` не найден в папке img")
        channel = self.bot.get_channel(sched["channel_id"]) or await self.bot.fetch_channel(sched["channel_id"])
        await channel.send(content=sched.get("message") or None, file=discord.File(path))
        log.info("Расписание %s (%s): отправлена картинка %s в #%s", sched["id"], describe_schedule(sched), sched["image"], channel)

    async def try_send(self, sched: dict) -> bool | None:
        """Отправка из цикла: True — отправлено; False — не получится и повтор не поможет (нет канала, прав или файла);
        None — временная ошибка (сеть, сбой Discord), стоит повторить на следующем круге."""
        try:
            await self.send_schedule(sched)
            return True
        except SendFailed as e:
            reason = str(e)
        except (discord.Forbidden, discord.NotFound) as e:
            reason = f"нет канала или прав у бота ({e})"
        except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            log.warning("Расписание %s: временная ошибка отправки в канал %s, повторю: %s", sched["id"], sched["channel_id"], e)
            return None
        except Exception as e:
            # любая другая ошибка не должна останавливать цикл: иначе перестанут отправляться все расписания
            log.exception("Расписание %s: неожиданная ошибка отправки", sched["id"])
            reason = f"неожиданная ошибка ({type(e).__name__}), подробности в логе бота"
        log.error("Расписание %s: не удалось отправить в канал %s: %s", sched["id"], sched["channel_id"], reason)
        await notify(self.bot, f"⚠️ Расписание не отправлено: {summary(sched)}\nПричина: {reason}")
        return False

    @tasks.loop(seconds=20)
    async def scheduler(self):
        now = datetime.now(current().tz)
        changed = False
        for sched in list(self.schedules):
            if sched.get("paused"):
                continue
            # last_sent — день, за который расписание уже отправлено (или отправить не получилось): защита от повторов
            if day := due_occurrence(sched, now, SEND_GRACE):
                sent = await self.try_send(sched)
                if sent is None:
                    continue  # временная ошибка: повторим через 20 секунд, пока не выйдет SEND_GRACE
                sched["last_sent"] = day
                changed = True
                # пока шла отправка, расписание могли удалить командой — тогда удалять уже нечего
                if sent and sched.get("kind") == "once" and sched in self.schedules:
                    self.schedules.remove(sched)
                    log.info("Расписание %s было одноразовым и удалено", sched["id"])
                    self.bot.cleanup_images()
            elif missed_once(sched, now, SEND_GRACE):
                # бот был выключен, или все попытки не удались. Расписание не удаляем молча: оно остаётся в /schedule list
                log.error("Расписание %s (%s) пропущено: в нужное время отправить не удалось", sched["id"], describe_schedule(sched))
                sched["last_sent"] = sched["date"]
                changed = True
                await notify(self.bot, f"⚠️ Разовое расписание пропущено (бот был недоступен в это время): {summary(sched)}")
        if changed:
            self.save()

    @scheduler.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()

    # ---------- создание расписаний ----------

    async def image_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=name, value=name)
            for name in list_images()
            if current.lower() in name.lower()
        ][:25]

    async def create_schedule(
        self,
        interaction: discord.Interaction,
        time: str,
        channel: discord.TextChannel,
        image: discord.Attachment | None,
        file: str | None,
        message: str | None,
        kind: str,
        **extra,
    ) -> None:
        """Общая часть /schedule daily, weekly, monthly, yearly и once: проверки, сохранение, ответ."""
        now = datetime.now(current().tz)
        try:
            time = parse_time(time)
            if "date" in extra:
                extra["date"] = parse_date(extra["date"], kind, now.date())
        except ValueError as e:
            await respond(interaction, str(e))
            return
        sched = {"id": uuid.uuid4().hex[:6], "kind": kind, **extra, "time": time, "channel_id": channel.id,
                 "image": None, "message": message}
        if once_passed(sched, now):
            await respond(interaction, "Эта дата и время уже прошли.")
            return
        if problem := channel_problem(channel, interaction.guild):
            await respond(interaction, problem)
            return
        if not image and not file:
            await respond(interaction, "Нужно загрузить картинку (`image`) или выбрать файл (`file`).")
            return
        if not (file := await receive_image(interaction, image, file)):
            return

        sched["image"] = file
        # не отправлять задним числом: сегодня, если время уже прошло, и вчера (цикл догоняет пропуски до SEND_GRACE)
        sched["last_sent"] = last_passed_day(sched, now)
        self.schedules.append(sched)
        self.save()
        when = describe_schedule(sched)
        log.info("Создано расписание %s: %s в #%s, картинка %s (автор %s)", sched["id"], when, channel, file, who(interaction.user))
        await respond(interaction, f"Готово! {when[0].upper() + when[1:]} ({current().timezone}) в {channel.mention} "
                                   f"будет отправляться `{file}`. ID: `{sched['id']}`")
        await notify(self.bot, f"🗓️ {interaction.user.mention} создаёт расписание: {summary(sched)}")

    @app_commands.command(name="daily", description="Отправлять картинку каждый день")
    @app_commands.describe(time=TIME_HELP, channel=CHANNEL_HELP, image=IMAGE_HELP, file=FILE_HELP, message=MESSAGE_HELP)
    @app_commands.autocomplete(file=image_autocomplete)
    async def daily(
        self,
        interaction: discord.Interaction,
        time: str,
        channel: discord.TextChannel,
        image: discord.Attachment | None = None,
        file: str | None = None,
        message: str | None = None,
    ):
        await self.create_schedule(interaction, time, channel, image, file, message, kind="daily")

    @app_commands.command(name="weekly", description="Отправлять картинку каждую неделю в определённый день")
    @app_commands.describe(weekday="День недели", time=TIME_HELP, channel=CHANNEL_HELP, image=IMAGE_HELP, file=FILE_HELP,
                           message=MESSAGE_HELP)
    @app_commands.choices(weekday=WEEKDAY_CHOICES)
    @app_commands.autocomplete(file=image_autocomplete)
    async def weekly(
        self,
        interaction: discord.Interaction,
        weekday: int,
        time: str,
        channel: discord.TextChannel,
        image: discord.Attachment | None = None,
        file: str | None = None,
        message: str | None = None,
    ):
        await self.create_schedule(interaction, time, channel, image, file, message, kind="weekly", weekday=weekday)

    @app_commands.command(name="monthly", description="Отправлять картинку каждый месяц в определённый день")
    @app_commands.describe(day="День месяца (1–31). Если в месяце нет такого дня, отправится в последний день",
                           time=TIME_HELP, channel=CHANNEL_HELP, image=IMAGE_HELP, file=FILE_HELP, message=MESSAGE_HELP)
    @app_commands.autocomplete(file=image_autocomplete)
    async def monthly(
        self,
        interaction: discord.Interaction,
        day: app_commands.Range[int, 1, 31],
        time: str,
        channel: discord.TextChannel,
        image: discord.Attachment | None = None,
        file: str | None = None,
        message: str | None = None,
    ):
        await self.create_schedule(interaction, time, channel, image, file, message, kind="monthly", day=day)

    @app_commands.command(name="yearly", description="Отправлять картинку каждый год в определённую дату (Новый год, день рождения)")
    @app_commands.describe(date="Дата ДД.ММ, например 01.01. 29.02 в невисокосный год — 28.02",
                           time=TIME_HELP, channel=CHANNEL_HELP, image=IMAGE_HELP, file=FILE_HELP, message=MESSAGE_HELP)
    @app_commands.autocomplete(file=image_autocomplete)
    async def yearly(
        self,
        interaction: discord.Interaction,
        date: str,
        time: str,
        channel: discord.TextChannel,
        image: discord.Attachment | None = None,
        file: str | None = None,
        message: str | None = None,
    ):
        await self.create_schedule(interaction, time, channel, image, file, message, kind="yearly", date=date)

    @app_commands.command(name="once", description="Отправить картинку один раз в определённую дату и время")
    @app_commands.describe(date="Дата ДД.ММ.ГГГГ, например 31.12.2026, или ДД.ММ — ближайшая такая дата",
                           time=TIME_HELP, channel=CHANNEL_HELP, image=IMAGE_HELP, file=FILE_HELP, message=MESSAGE_HELP)
    @app_commands.autocomplete(file=image_autocomplete)
    async def once(
        self,
        interaction: discord.Interaction,
        date: str,
        time: str,
        channel: discord.TextChannel,
        image: discord.Attachment | None = None,
        file: str | None = None,
        message: str | None = None,
    ):
        await self.create_schedule(interaction, time, channel, image, file, message, kind="once", date=date)

    # ---------- просмотр, изменение и удаление ----------

    @app_commands.command(name="list", description="Показать все расписания")
    async def list_(self, interaction: discord.Interaction):
        if not self.schedules:
            await respond(interaction, "Расписаний пока нет.")
            return
        lines = [summary(s) for s in sorted(self.schedules, key=lambda s: s["time"])]
        await respond_lines(interaction, lines, header=f"Часовой пояс: {current().timezone}")

    async def id_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=f"{s['id']} — {describe_schedule(s)} {s['image']}"
                                     f"{' (на паузе)' if s.get('paused') else ''}"[:100], value=s["id"])
            for s in self.schedules
            if current.lower() in s["id"]
        ][:25]

    def find(self, sched_id: str) -> dict | None:
        return next((s for s in self.schedules if s["id"] == sched_id), None)

    @app_commands.command(name="edit", description="Изменить расписание: время, канал, картинку, текст или день")
    @app_commands.describe(
        id="ID расписания (см. /schedule list)",
        time="Новое время ЧЧ:ММ",
        channel="Новый канал",
        image="Загрузить новую картинку",
        file="Или выбрать другую из папки img",
        message="Новый текст к картинке; «-» — убрать текст",
        weekday="Новый день недели (у еженедельного расписания)",
        day="Новый день месяца (у ежемесячного)",
        date="Новая дата: ДД.ММ у ежегодного, ДД.ММ.ГГГГ у разового",
    )
    @app_commands.choices(weekday=WEEKDAY_CHOICES)
    @app_commands.autocomplete(id=id_autocomplete, file=image_autocomplete)
    async def edit(
        self,
        interaction: discord.Interaction,
        id: str,
        time: str | None = None,
        channel: discord.TextChannel | None = None,
        image: discord.Attachment | None = None,
        file: str | None = None,
        message: str | None = None,
        weekday: int | None = None,
        day: app_commands.Range[int, 1, 31] | None = None,
        date: str | None = None,
    ):
        sched = self.find(id)
        if not sched:
            await respond(interaction, "Расписание не найдено.")
            return
        if all(v is None for v in (time, channel, image, file, message, weekday, day, date)):
            await respond(interaction, "Укажите, что изменить: время, канал, картинку, текст или день.")
            return
        kind, now = sched.get("kind", "daily"), datetime.now(current().tz)
        new = dict(sched)
        try:
            if time is not None:
                new["time"] = parse_time(time)
            for value, key, kinds, what in ((weekday, "weekday", ("weekly",), "дня недели"),
                                            (day, "day", ("monthly",), "дня месяца"),
                                            (date, "date", ("yearly", "once"), "даты")):
                if value is None:
                    continue
                if kind not in kinds:
                    raise ValueError(f"У этого расписания нет {what}: оно {describe_schedule(sched)}. "
                                     "Чтобы поменять вид расписания, создайте новое и удалите старое.")
                new[key] = parse_date(value, kind, now.date()) if key == "date" else value
        except ValueError as e:
            await respond(interaction, str(e))
            return
        if once_passed(new, now):
            await respond(interaction, "Эта дата и время уже прошли.")
            return
        if channel is not None:
            if problem := channel_problem(channel, interaction.guild):
                await respond(interaction, problem)
                return
            new["channel_id"] = channel.id
        if message is not None:
            new["message"] = None if message.strip() == CLEAR else message
        if image or file:
            if not (new_file := await receive_image(interaction, image, file)):
                return
            new["image"] = new_file
        if sched not in self.schedules:  # удалили, пока скачивалась картинка
            await respond(interaction, "Расписание удалили, пока вы его меняли.")
            self.bot.cleanup_images()
            return
        if any(new.get(k) != sched.get(k) for k in ("time", "weekday", "day", "date")):
            # новое время уже прошло сегодня — отправится со следующего раза, а если сегодня уже отправляли — не повторится
            new["last_sent"] = keep_sent(new, now)
        before = summary(sched)
        sched.update(new)  # на месте: цикл отправки может держать это же расписание
        self.save()
        self.bot.cleanup_images()
        log.info("Изменено расписание %s пользователем %s: %s → %s", id, who(interaction.user), before, summary(sched))
        await respond(interaction, f"Готово! {summary(sched)}", allowed_mentions=discord.AllowedMentions.none())
        await notify(self.bot, f"✏️ {interaction.user.mention} изменяет расписание:\n{before}\n→ {summary(sched)}")

    @app_commands.command(name="pause", description="Приостановить расписание (не удаляя его)")
    @app_commands.describe(id="ID расписания (см. /schedule list)")
    @app_commands.autocomplete(id=id_autocomplete)
    async def pause(self, interaction: discord.Interaction, id: str):
        sched = self.find(id)
        if not sched:
            await respond(interaction, "Расписание не найдено.")
            return
        if sched.get("paused"):
            await respond(interaction, f"Расписание `{id}` уже на паузе. Включить — `/schedule resume`.")
            return
        sched["paused"] = True
        self.save()
        log.info("Расписание %s поставлено на паузу пользователем %s", id, who(interaction.user))
        await respond(interaction, f"Расписание `{id}` на паузе и не будет отправляться. Включить — `/schedule resume`.")
        await notify(self.bot, f"⏸️ {interaction.user.mention} ставит на паузу расписание: {summary(sched)}")

    @app_commands.command(name="resume", description="Снова включить приостановленное расписание")
    @app_commands.describe(id="ID расписания (см. /schedule list)")
    @app_commands.autocomplete(id=id_autocomplete)
    async def resume(self, interaction: discord.Interaction, id: str):
        sched = self.find(id)
        if not sched:
            await respond(interaction, "Расписание не найдено.")
            return
        if not sched.get("paused"):
            await respond(interaction, f"Расписание `{id}` и так работает.")
            return
        now = datetime.now(current().tz)
        if once_passed(sched, now):
            await respond(interaction, f"Дата этого разового расписания уже прошла — поменяйте её: `/schedule edit id:{id} date:…`")
            return
        del sched["paused"]
        sched["last_sent"] = keep_sent(sched, now)  # пропущенное за время паузы не отправляется задним числом
        self.save()
        log.info("Расписание %s снято с паузы пользователем %s", id, who(interaction.user))
        await respond(interaction, f"Расписание `{id}` снова работает: {describe_schedule(sched)}.")
        await notify(self.bot, f"▶️ {interaction.user.mention} снимает с паузы расписание: {summary(sched)}")

    @app_commands.command(name="remove", description="Удалить расписание")
    @app_commands.describe(id="ID расписания (см. /schedule list)")
    @app_commands.autocomplete(id=id_autocomplete)
    async def remove(self, interaction: discord.Interaction, id: str):
        sched = self.find(id)
        if not sched:
            await respond(interaction, "Расписание не найдено.")
            return
        self.schedules.remove(sched)
        self.save()
        self.bot.cleanup_images()
        log.info("Удалено расписание %s (%s) пользователем %s", id, describe_schedule(sched), who(interaction.user))
        await respond(interaction, f"Расписание `{id}` удалено.")
        await notify(self.bot, f"🗑️ {interaction.user.mention} удаляет расписание: {summary(sched)}")

    @app_commands.command(name="test", description="Отправить картинку из расписания прямо сейчас")
    @app_commands.describe(id="ID расписания")
    @app_commands.autocomplete(id=id_autocomplete)
    async def test(self, interaction: discord.Interaction, id: str):
        sched = self.find(id)
        if not sched:
            await respond(interaction, "Расписание не найдено.")
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.send_schedule(sched)
        except SendFailed as e:
            await respond(interaction, f"Не отправлено: {e}.")
            return
        except discord.DiscordException as e:
            await respond(interaction, f"Ошибка отправки: {e}")
            return
        await respond(interaction, "Отправлено.")
