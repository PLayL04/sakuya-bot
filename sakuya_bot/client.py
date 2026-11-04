"""Класс бота: подключение функций (когов), общие события и обработка ошибок."""

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .beer.cog import BeerCog
from .coffee.cog import CoffeeCog
from .config import GUILD, GUILD_ID
from .distort.cog import DistortCog
from .help import HelpCog
from .imaging.command import COOLDOWN_TEXT
from .imaging.sources import clear_downloads
from .logs import log, who
from .menus import MenuCog
from .museum.cog import MuseumCog
from .quotes.cog import QuoteCog
from .reactions.cog import ReactionCog
from .replies import respond
from .schedules.cog import ScheduleCog
from .settings import current
from .settings.cog import SettingsCog
from .storage import remove_unused_images


class SakuyaBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        # нужно, чтобы видеть текст сообщений; включается в Developer Portal -> Bot -> Message Content Intent
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents, case_insensitive=True, help_command=None)
        self.schedule_cog = ScheduleCog(self)
        self.reaction_cog = ReactionCog(self)

    async def setup_hook(self):
        self.tree.on_error = self.on_tree_error
        # каждый ког — отдельная функция бота со своими командами, данными и событиями;
        # настройки — первыми: остальные коги читают их уже при загрузке
        await self.add_cog(SettingsCog(self), guild=GUILD)
        await self.add_cog(self.schedule_cog, guild=GUILD)
        await self.add_cog(self.reaction_cog, guild=GUILD)
        await self.add_cog(CoffeeCog(self))
        await self.add_cog(BeerCog(self))
        await self.add_cog(DistortCog(self))
        await self.add_cog(MuseumCog(self))
        await self.add_cog(QuoteCog(self))
        # после команд с картинками: меню берёт их кулдауны, а /help — их алиасы
        await self.add_cog(MenuCog(self))
        await self.add_cog(HelpCog(self), guild=GUILD)
        # синхронизация только для своего сервера — команды появляются сразу
        synced = await self.tree.sync(guild=GUILD)
        log.info("Слэш-команды синхронизированы с сервером %s: %s", GUILD_ID, ", ".join(f"/{c.name}" for c in synced))
        self.cleanup_images()  # картинки, оставшиеся от сбоя посреди команды
        clear_downloads()  # и скачанные для команд с картинками
        self.heartbeat.start()

    def cleanup_images(self) -> None:
        """Удаляет из img/ загруженные через бота картинки, которые больше не нужны ни одному правилу и расписанию.
        Сбой уборки (файл занят, нет прав, испорчен uploads.json) не мешает команде или циклу, которые её вызвали:
        ненужная картинка просто удалится в следующий раз."""
        try:
            in_use = {s.get("image") for s in self.schedule_cog.schedules}
            in_use |= {r["media"] for r in self.reaction_cog.reactions if r.get("media")}
            remove_unused_images(in_use)
        except Exception:
            log.exception("Не удалось убрать неиспользуемые картинки из img/")

    def stats(self) -> str:
        return f"расписаний: {len(self.schedule_cog.schedules)}, правил реакций: {len(self.reaction_cog.reactions)}"

    async def on_ready(self):
        log.info("Бот онлайн как %s, %s", who(self.user), self.stats())

    async def on_disconnect(self):
        log.warning("Соединение с Discord потеряно, переподключаемся...")

    async def on_resumed(self):
        log.info("Соединение с Discord восстановлено")

    async def on_app_command_completion(self, interaction: discord.Interaction, command: app_commands.Command):
        params = ", ".join(f"{k}={getattr(v, 'filename', v)}" for k, v in interaction.namespace)
        log.info("Команда /%s от %s в #%s%s", command.qualified_name, who(interaction.user), interaction.channel,
                 f" ({params})" if params else "")

    async def on_tree_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        name = interaction.command.qualified_name if interaction.command else "?"
        if isinstance(error, app_commands.CheckFailure):
            log.warning("Отказано в доступе: /%s от %s (нет ни одной из ролей %s и права «Управлять сервером»)",
                        name, who(interaction.user), ", ".join(map(str, sorted(current().admin_roles))) or "—")
            text = "Эта команда доступна только участникам с ролью управления ботом или правом «Управлять сервером»."
        else:
            original = getattr(error, "original", error)
            log.error("Ошибка команды /%s от %s: %s", name, who(interaction.user), original, exc_info=original)
            # иначе у модератора просто повиснет «Приложение не отвечает»
            text = f"Что-то пошло не так ({type(original).__name__}). Подробности — в логе бота, попробуйте ещё раз позже."
        if interaction.type == discord.InteractionType.autocomplete:
            return  # на подсказки при вводе сообщением не ответить
        try:
            await respond(interaction, text)
        except discord.HTTPException as e:
            log.error("Не удалось сообщить об ошибке команды /%s: %s", name, e)  # например, ответ уже просрочен

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return  # обычное сообщение, начинающееся с «!»
        if isinstance(error, commands.CommandOnCooldown):
            waiting = COOLDOWN_TEXT.get(ctx.command.name, "Подожди немного ⏳")
            await ctx.reply(f"{waiting} Попробуй через {error.retry_after:.0f} сек.", mention_author=False, delete_after=10)
            return
        if isinstance(error, commands.CheckFailure):
            return  # личные сообщения или канал, где команда не разрешена — молча игнорируем
        log.error("Ошибка команды !%s от %s: %s", ctx.command, who(ctx.author), error,
                  exc_info=getattr(error, "original", error))
        await ctx.reply("Что-то пошло не так, попробуй ещё раз позже.", mention_author=False)

    @tasks.loop(hours=1)
    async def heartbeat(self):
        # раз в час пишет в лог, что бот жив — удобно проверять, что он не завис
        log.info("Бот работает: задержка %d мс, %s", self.latency * 1000, self.stats())

    @heartbeat.before_loop
    async def before_heartbeat(self):
        await self.wait_until_ready()
