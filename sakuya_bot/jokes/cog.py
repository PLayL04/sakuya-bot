"""Команда !анекдот: случайный анекдот с anekdot.ru, а со словами — найденный по ним (из запаса, см. cache.py)."""

import discord
from discord.ext import commands

from ..imaging.command import fun_command_checks
from ..logs import log, who
from ..quotes.picker import escape
from .cache import JokeCache, JokesUnavailable, NothingFound, normalize_query
from .parser import format_joke

UNAVAILABLE_TEXT = "Анекдоты сейчас не загружаются — попробуй чуть позже."


class JokeCog(commands.Cog):

    def __init__(self, bot: commands.Bot, cache: JokeCache | None = None):
        self.bot = bot
        self.cache = cache or JokeCache()

    @commands.command(name="анекдот", aliases=["анек", "joke", "шутка"])
    @fun_command_checks  # только на сервере, в каналах и с кулдауном из настроек (/settings)
    async def joke(self, ctx: commands.Context, *, words: str = ""):
        """Случайный анекдот с anekdot.ru; со словами (!анекдот прапорщик гвозди) — анекдот, найденный по ним."""
        query = normalize_query(words) or None
        async with ctx.typing():  # если запас кончился, страница скачивается — видно «печатает…»
            try:
                joke = await self.cache.next(query)
            except (JokesUnavailable, NothingFound) as e:
                ctx.command.reset_cooldown(ctx)  # не вышло — попытка не считается
                if isinstance(e, NothingFound):
                    log.info("!анекдот от %s: по запросу «%s» ничего не найдено", who(ctx.author), query)
                    text = f"Не нашёл анекдотов по запросу «{escape(query)}»."
                else:
                    log.warning("!анекдот от %s: %s", who(ctx.author), e)
                    text = UNAVAILABLE_TEXT
                await ctx.reply(text, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
                return
        # в тексте может оказаться @everyone или упоминание — не пингуем никого
        await ctx.send(format_joke(joke), allowed_mentions=discord.AllowedMentions.none())
        stock = self.cache.stock(query)
        log.info("!анекдот от %s в #%s%s: %s, в запасе ещё %d", who(ctx.author), ctx.channel,
                 f" по запросу «{query}»" if query else "", joke.id, len(stock.jokes))
