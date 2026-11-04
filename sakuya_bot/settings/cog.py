"""Команды /settings: посмотреть настройки, скачать файл настроек, загрузить исправленный. И слежение за файлом на диске."""

import io

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..checks import SEND_PERMISSIONS, AdminOnly, missing_permissions
from ..logs import log, who
from ..modlog import notify
from . import STORE, current
from .model import FIELDS, Settings, SettingsError, changes, describe_value, parse

MAX_FILE_BYTES = 64 * 1024
NO_MENTIONS = discord.AllowedMentions.none()


def mention(kind: str, item_id: int) -> str:
    return f"<@&{item_id}>" if kind == "roles" else f"<#{item_id}>"


def guild_warnings(settings: Settings, guild: discord.Guild) -> list[str]:
    """Что в настройках не нашлось на сервере: опечатка в ID или удалённая роль / канал. Это не ошибка, но стоит знать."""
    warnings = [f"роли с ID `{i}` нет на сервере" for i in sorted(settings.admin_roles) if guild.get_role(i) is None]
    warnings += [f"канала с ID `{i}` нет на сервере (или бот его не видит)"
                 for i in sorted(settings.fun_channels) if guild.get_channel_or_thread(i) is None]
    if discord.utils.get(guild.emojis, name=settings.ping_emoji) is None:
        warnings.append(f"эмодзи `:{settings.ping_emoji}:` нет на сервере — на пинг будет ставиться 💩")
    if settings.log_channel:
        channel = guild.get_channel_or_thread(settings.log_channel)
        if channel is None:
            warnings.append(f"канала логов с ID `{settings.log_channel}` нет на сервере (или бот его не видит)")
        elif missing := missing_permissions(channel, guild.me, SEND_PERMISSIONS):
            warnings.append(f"в канале логов {channel.mention} у бота нет прав: {', '.join(missing)}")
    return warnings


def change_lines(old: Settings, new: Settings, diff: list) -> list[str]:
    return [f"• **{f.title}:** {describe_value(f, getattr(old, f.attr), mention)} → "
            f"{describe_value(f, getattr(new, f.attr), mention)}" for f in diff]


def keeps_access(member: discord.Member, settings: Settings) -> bool:
    return member.guild_permissions.manage_guild or any(r.id in settings.admin_roles for r in member.roles)


@app_commands.guild_only()
class SettingsCog(AdminOnly, commands.GroupCog, group_name="settings",
                  group_description="Настройки бота на этом сервере: роли, каналы, кулдауны, часовой пояс"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        STORE.load()
        log.info("Настройки: %s", "; ".join(f"{f.title} — {describe_value(f, getattr(current(), f.attr))}" for f in FIELDS))
        self.watch_file.start()

    async def cog_unload(self):
        self.watch_file.cancel()

    @tasks.loop(seconds=30)
    async def watch_file(self):
        # необработанная ошибка навсегда остановила бы цикл (так устроен tasks.loop) — ловим её здесь
        try:
            await self.check_file()
        except Exception:
            log.exception("Сбой при проверке файла настроек, попробую снова через 30 секунд")

    async def check_file(self) -> None:
        # файл можно поправить и прямо на сервере (VPS): изменения подхватываются без перезапуска бота
        old = current()
        if STORE.reload_if_changed():
            diff = changes(old, current())
            for f in diff:
                log.info("Настройка «%s»: %s → %s", f.title, describe_value(f, getattr(old, f.attr)),
                         describe_value(f, getattr(current(), f.attr)))
            if diff:
                await notify(self.bot, "⚙️ Настройки изменены в файле на сервере:\n" + "\n".join(change_lines(old, current(), diff)))

    @app_commands.command(name="show", description="Показать текущие настройки бота")
    async def show(self, interaction: discord.Interaction):
        s = current()
        lines = [f"**{f.title}:** {describe_value(f, getattr(s, f.attr), mention)}" for f in FIELDS]
        warnings = guild_warnings(s, interaction.guild)
        if warnings:
            lines.append("\n⚠️ " + "\n⚠️ ".join(warnings))
        lines.append("\nИзменить:`/settings download` → поправить файл → `/settings upload`.")
        await interaction.response.send_message("\n".join(lines), ephemeral=True, allowed_mentions=NO_MENTIONS)

    @app_commands.command(name="download", description="Скачать файл настроек, чтобы его исправить")
    async def download(self, interaction: discord.Interaction):
        data = STORE.read_text().encode("utf-8")
        await interaction.response.send_message(
            "Файл настроек. Откройте в текстовом редакторе (Блокнот подойдёт), исправьте значения — подсказки в строках "
            "с `#` — и загрузите обратно командой `/settings upload`.",
            file=discord.File(io.BytesIO(data), "settings.toml"), ephemeral=True)

    @app_commands.command(name="upload", description="Загрузить исправленный файл настроек")
    @app_commands.describe(file="Файл settings.toml (скачать текущий — /settings download)")
    async def upload(self, interaction: discord.Interaction, file: discord.Attachment):
        await interaction.response.defer(ephemeral=True)
        if file.size > MAX_FILE_BYTES:
            await interaction.followup.send(f"Файл слишком большой для файла настроек (больше {MAX_FILE_BYTES // 1024} КБ).",
                                            ephemeral=True)
            return
        try:
            text = (await file.read()).decode("utf-8-sig")
        except UnicodeDecodeError:
            await interaction.followup.send("Файл не в кодировке UTF-8. В Блокноте: «Сохранить как» → кодировка UTF-8.",
                                            ephemeral=True)
            return
        old = current()
        try:
            new = parse(text)
        except SettingsError as e:
            log.warning("Файл настроек от %s не принят: %s", who(interaction.user), e)
            await interaction.followup.send("Файл не принят, настройки не изменились:\n• " + "\n• ".join(e.problems),
                                            ephemeral=True)
            return
        if not keeps_access(interaction.user, new):
            # иначе модератор одной загрузкой отрежет доступ себе (а если у остальных тоже нет роли — и им)
            await interaction.followup.send(
                "Файл не принят: после него у вас не останется доступа к боту — нет ни одной из ролей в `admin_roles` "
                "и нет права «Управлять сервером». Добавьте в список свою роль.", ephemeral=True)
            return
        STORE.apply(text)

        diff = changes(old, new)
        for f in diff:
            log.info("Настройка «%s» изменена пользователем %s: %s → %s", f.title, who(interaction.user),
                     describe_value(f, getattr(old, f.attr)), describe_value(f, getattr(new, f.attr)))
        if diff:
            lines = ["Настройки сохранены. Изменено:"] + change_lines(old, new, diff)
            await notify(self.bot, f"⚙️ {interaction.user.mention} изменяет настройки:\n" + "\n".join(change_lines(old, new, diff)))
        else:
            log.info("Файл настроек загружен пользователем %s без изменений значений", who(interaction.user))
            lines = ["Файл сохранён, значения настроек не изменились."]
        if warnings := guild_warnings(new, interaction.guild):
            lines.append("\n⚠️ " + "\n⚠️ ".join(warnings))
        await interaction.followup.send("\n".join(lines), ephemeral=True, allowed_mentions=NO_MENTIONS)
