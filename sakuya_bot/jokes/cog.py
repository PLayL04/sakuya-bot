"""Команда !анекдот: случайный анекдот с anekdot.ru (из запаса, см. cache.py)."""

import discord
from discord.ext import commands

from ..imaging.command import fun_command_checks
from ..logs import log, who
from .cache import JokeCache, JokesUnavailable
from .parser import format_joke

UNAVAILABLE_TEXT = "Анекдоты сейчас не загружаются — попробуй чуть позже."


class JokeCog(commands.Cog):

    def __init__(self, bot: commands.Bot, cache: JokeCache | None = None):
        self.bot = bot
        self.cache = cache or JokeCache()

    @commands.command(name="анекдот", aliases=["анек", "joke", "шутка"])
    @fun_command_checks  # только на сервере, в каналах и с кулдауном из настроек (/settings)
    async def joke(self, ctx: commands.Context):
        """Отправляет случайный анекдот с anekdot.ru."""
        async with ctx.typing():  # если запас кончился, страница скачивается — видно «печатает…»
            try:
                joke = await self.cache.next()
            except JokesUnavailable as e:
                ctx.command.reset_cooldown(ctx)  # не вышло — попытка не считается
                log.warning("!анекдот от %s: %s", who(ctx.author), e)
                await ctx.reply(UNAVAILABLE_TEXT, mention_author=False)
                return
        # в тексте может оказаться @everyone или упоминание — не пингуем никого
        await ctx.send(format_joke(joke), allowed_mentions=discord.AllowedMentions.none())
        log.info("!анекдот от %s в #%s: %s, в запасе ещё %d", who(ctx.author), ctx.channel, joke.id, len(self.cache.jokes))
