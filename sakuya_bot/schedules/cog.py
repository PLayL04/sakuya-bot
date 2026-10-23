"""Команды /schedule и фоновый цикл, который отправляет картинки по расписанию."""

import uuid
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..checks import AdminOnly
from ..config import DATA_DIR, IMG_DIR, IMG_EXTENSIONS, TZ
from ..logs import log, who
from ..storage import load_json, save_json
from .rules import DATE_RE, TIME_RE, WEEKDAYS, describe_schedule, is_due_today

SCHEDULES_FILE = DATA_DIR / "schedules.json"


def list_images() -> list[str]:
    IMG_DIR.mkdir(exist_ok=True)
    return sorted(p.name for p in IMG_DIR.iterdir() if p.suffix.lower() in IMG_EXTENSIONS)


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

    async def send_schedule(self, sched: dict) -> bool:
        channel = self.bot.get_channel(sched["channel_id"]) or await self.bot.fetch_channel(sched["channel_id"])
        path = IMG_DIR / sched["image"]
        if not path.exists():
            log.error("Расписание %s: файл не найден: %s", sched["id"], path)
            return False
        await channel.send(content=sched.get("message") or None, file=discord.File(path))
        log.info("Расписание %s (%s): отправлена картинка %s в #%s", sched["id"], describe_schedule(sched), sched["image"], channel)
        return True

    @tasks.loop(seconds=20)
    async def scheduler(self):
        now = datetime.now(TZ)
        today = now.date().isoformat()
        hhmm = now.strftime("%H:%M")
        changed = False
        for sched in list(self.schedules):
            # last_sent защищает от двойной отправки в течение одной минуты
            if sched["time"] == hhmm and sched.get("last_sent") != today and is_due_today(sched, now):
                try:
                    await self.send_schedule(sched)
                except discord.DiscordException as e:
                    log.error("Расписание %s: ошибка отправки в канал %s: %s", sched["id"], sched["channel_id"], e)
                sched["last_sent"] = today
                if sched.get("kind") == "once":
                    self.schedules.remove(sched)
                    log.info("Расписание %s было одноразовым и удалено", sched["id"])
                changed = True
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
        **extra,
    ) -> None:
        """Общая часть /schedule daily, weekly, monthly и date: проверка времени и картинки, сохранение."""
        m = TIME_RE.match(time.strip())
        if not m:
            await interaction.response.send_message("Неверный формат времени. Пример: `08:00`", ephemeral=True)
            return
        time = f"{int(m[1]):02d}:{m[2]}"

        now = datetime.now(TZ)
        if extra["kind"] == "once" and (extra["date"], time) <= (now.date().isoformat(), now.strftime("%H:%M")):
            await interaction.response.send_message("Эта дата и время уже прошли.", ephemeral=True)
            return

        if image:
            ext = Path(image.filename).suffix.lower()
            if ext not in IMG_EXTENSIONS:
                await interaction.response.send_message("Это не картинка.", ephemeral=True)
                return
            # ссылки на вложения Discord со временем протухают, поэтому сохраняем файл локально
            file = f"{Path(image.filename).stem}-{uuid.uuid4().hex[:6]}{ext}"
            IMG_DIR.mkdir(exist_ok=True)
            await image.save(IMG_DIR / file)
            log.info("Сохранена картинка %s (%d КБ)", file, image.size // 1024)
        elif file:
            if file not in list_images():
                await interaction.response.send_message(f"Файл `{file}` не найден в папке img.", ephemeral=True)
                return
        else:
            await interaction.response.send_message("Нужно загрузить картинку (`image`) или выбрать файл (`file`).", ephemeral=True)
            return

        sched = {
            "id": uuid.uuid4().hex[:6],
            **extra,
            "time": time,
            "channel_id": channel.id,
            "image": file,
            "message": message,
            # не отправлять сегодня, если время уже прошло
            "last_sent": now.date().isoformat() if now.strftime("%H:%M") > time else None,
        }
        self.schedules.append(sched)
        self.save()
        when = describe_schedule(sched)
        log.info("Создано расписание %s: %s в #%s, картинка %s (автор %s)", sched["id"], when, channel, file, who(interaction.user))
        await interaction.response.send_message(
            f"Готово! {when[0].upper() + when[1:]} ({TZ.key}) в {channel.mention} будет отправляться `{file}`. ID: `{sched['id']}`",
            ephemeral=True,
        )

    @app_commands.command(name="daily", description="Отправлять картинку каждый день")
    @app_commands.describe(
        time="Время в формате ЧЧ:ММ, например 08:00",
        channel="Канал, куда отправлять",
        image="Загрузить новую картинку",
        file="Или выбрать уже существующую из папки img",
        message="Текст к картинке (необязательно)",
    )
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
    @app_commands.describe(
        weekday="День недели",
        time="Время в формате ЧЧ:ММ, например 08:00",
        channel="Канал, куда отправлять",
        image="Загрузить новую картинку",
        file="Или выбрать уже существующую из папки img",
        message="Текст к картинке (необязательно)",
    )
    @app_commands.choices(weekday=[app_commands.Choice(name=name, value=i) for i, name in enumerate(WEEKDAYS)])
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
    @app_commands.describe(
        day="День месяца (1–31). Если в месяце нет такого дня, отправится в последний день",
        time="Время в формате ЧЧ:ММ, например 08:00",
        channel="Канал, куда отправлять",
        image="Загрузить новую картинку",
        file="Или выбрать уже существующую из папки img",
        message="Текст к картинке (необязательно)",
    )
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

    @app_commands.command(name="date", description="Отправить картинку в определённую дату (например, на Новый год)")
    @app_commands.describe(
        date="ДД.ММ — каждый год (01.01), ДД.ММ.ГГГГ — один раз (01.01.2027)",
        time="Время в формате ЧЧ:ММ, например 00:00",
        channel="Канал, куда отправлять",
        image="Загрузить новую картинку",
        file="Или выбрать уже существующую из папки img",
        message="Текст к картинке (необязательно)",
    )
    @app_commands.autocomplete(file=image_autocomplete)
    async def date(
        self,
        interaction: discord.Interaction,
        date: str,
        time: str,
        channel: discord.TextChannel,
        image: discord.Attachment | None = None,
        file: str | None = None,
        message: str | None = None,
    ):
        m = DATE_RE.match(date.strip())
        try:
            # 2000 — високосный год, чтобы 29.02 было допустимой ежегодной датой
            parsed = datetime(int(m[3] or 2000), int(m[2]), int(m[1])) if m else None
        except ValueError:
            parsed = None
        if not parsed:
            await interaction.response.send_message("Неверная дата. Примеры: `01.01` (каждый год) или `01.01.2027` (один раз)", ephemeral=True)
            return
        if m[3]:
            await self.create_schedule(interaction, time, channel, image, file, message, kind="once", date=parsed.strftime("%Y-%m-%d"))
        else:
            await self.create_schedule(interaction, time, channel, image, file, message, kind="yearly", date=parsed.strftime("%m-%d"))

    # ---------- просмотр и удаление ----------

    @app_commands.command(name="list", description="Показать все расписания")
    async def list_(self, interaction: discord.Interaction):
        if not self.schedules:
            await interaction.response.send_message("Расписаний пока нет.", ephemeral=True)
            return
        lines = [
            f"`{s['id']}` — **{describe_schedule(s)}** в <#{s['channel_id']}>: `{s['image']}`"
            + (f" — «{s['message']}»" if s.get("message") else "")
            for s in sorted(self.schedules, key=lambda s: s["time"])
        ]
        await interaction.response.send_message(f"Часовой пояс: {TZ.key}\n" + "\n".join(lines), ephemeral=True)

    async def id_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=f"{s['id']} — {describe_schedule(s)} {s['image']}"[:100], value=s["id"])
            for s in self.schedules
            if current.lower() in s["id"]
        ][:25]

    def find(self, sched_id: str) -> dict | None:
        return next((s for s in self.schedules if s["id"] == sched_id), None)

    @app_commands.command(name="remove", description="Удалить расписание")
    @app_commands.describe(id="ID расписания (см. /schedule list)")
    @app_commands.autocomplete(id=id_autocomplete)
    async def remove(self, interaction: discord.Interaction, id: str):
        sched = self.find(id)
        if not sched:
            await interaction.response.send_message("Расписание не найдено.", ephemeral=True)
            return
        self.schedules.remove(sched)
        self.save()
        log.info("Удалено расписание %s (%s) пользователем %s", id, describe_schedule(sched), who(interaction.user))
        await interaction.response.send_message(f"Расписание `{id}` удалено.", ephemeral=True)

    @app_commands.command(name="test", description="Отправить картинку из расписания прямо сейчас")
    @app_commands.describe(id="ID расписания")
    @app_commands.autocomplete(id=id_autocomplete)
    async def test(self, interaction: discord.Interaction, id: str):
        sched = self.find(id)
        if not sched:
            await interaction.response.send_message("Расписание не найдено.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            ok = await self.send_schedule(sched)
        except discord.DiscordException as e:
            await interaction.followup.send(f"Ошибка отправки: {e}", ephemeral=True)
            return
        await interaction.followup.send("Отправлено." if ok else f"Файл `{sched['image']}` не найден.", ephemeral=True)
