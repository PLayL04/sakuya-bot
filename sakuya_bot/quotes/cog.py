"""Команда !цитата: случайный отрывок из текстов в папке quotes/."""

import asyncio

import discord
from discord.ext import commands

from ..config import QUOTES_DIR
from ..imaging.command import fun_command_checks
from ..logs import log, who
from .picker import QuoteLibrary, format_quote


class QuoteCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.library = QuoteLibrary(QUOTES_DIR)

    async def cog_load(self):
        # читаем тексты сразу: ошибка видна при запуске, а первый вызов не тормозит на большом файле
        sources = await asyncio.to_thread(self.library.sources)
        if sources:
            log.info("Цитаты загружены из %s: %s", QUOTES_DIR, ", ".join(s.name for s in sources))
        else:
            log.warning("В %s нет текстов (*.txt) — !цитата будет отвечать, что цитат нет", QUOTES_DIR)

    @commands.command(name="цитата", aliases=["quote"])
    @fun_command_checks  # только на сервере, в каналах и с кулдауном из настроек (/settings)
    async def quote(self, ctx: commands.Context):
        """Отправляет случайную цитату из текстов в папке quotes/: от одного предложения до нескольких абзацев."""
        quote = await asyncio.to_thread(self.library.pick)
        if quote is None:
            ctx.command.reset_cooldown(ctx)
            log.warning("!цитата от %s: в %s нет текстов", who(ctx.author), QUOTES_DIR)
            await ctx.reply("Цитат пока нет: в папке `quotes/` нет текстов.", mention_author=False)
            return
        # в тексте может оказаться @everyone или упоминание — не пингуем никого
        await ctx.send(format_quote(quote), allowed_mentions=discord.AllowedMentions.none())
        log.info("!цитата от %s в #%s: «%s», %d символов", who(ctx.author), ctx.channel, quote.source, len(quote.text))
