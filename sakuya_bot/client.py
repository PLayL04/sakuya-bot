"""Класс бота: подключение функций (когов), общие события и обработка ошибок."""

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .coffee.cog import CoffeeCog
from .config import ADMIN_ROLE_IDS, GUILD, GUILD_ID
from .logs import log, who
from .reactions.cog import ReactionCog
from .schedules.cog import ScheduleCog


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
        # каждый ког — отдельная функция бота со своими командами, данными и событиями
        await self.add_cog(self.schedule_cog, guild=GUILD)
        await self.add_cog(self.reaction_cog, guild=GUILD)
        await self.add_cog(CoffeeCog(self))
        # синхронизация только для своего сервера — команды появляются сразу
        synced = await self.tree.sync(guild=GUILD)
        log.info("Слэш-команды синхронизированы с сервером %s: %s", GUILD_ID, ", ".join(f"/{c.name}" for c in synced))
        self.heartbeat.start()

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
        if isinstance(error, app_commands.CheckFailure):
            name = interaction.command.qualified_name if interaction.command else "?"
            log.warning("Отказано в доступе: /%s от %s (нет ни одной из ролей %s)", name, who(interaction.user),
                        ", ".join(map(str, sorted(ADMIN_ROLE_IDS))))
            await interaction.response.send_message("Эта команда доступна только участникам с ролью управления ботом.", ephemeral=True)
            return
        await app_commands.CommandTree.on_error(self.tree, interaction, error)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return  # обычное сообщение, начинающееся с «!»
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(f"Кофе ещё варится ☕ Попробуй через {error.retry_after:.0f} сек.", mention_author=False, delete_after=10)
            return
        if isinstance(error, commands.CheckFailure):
            return  # личные сообщения или канал, где команда не разрешена — молча игнорируем
        log.error("Ошибка команды !%s от %s: %s", ctx.command, who(ctx.author), error,
                  exc_info=getattr(error, "original", error))
        await ctx.reply("Что-то пошло не так, не получилось сварить кофе.", mention_author=False)

    @tasks.loop(hours=1)
    async def heartbeat(self):
        # раз в час пишет в лог, что бот жив — удобно проверять, что он не завис
        log.info("Бот работает: задержка %d мс, %s", self.latency * 1000, self.stats())

    @heartbeat.before_loop
    async def before_heartbeat(self):
        await self.wait_until_ready()
