"""Команда !кофе: находит картинку, обрабатывает её в latte.py и отвечает результатом."""

import asyncio
import io
import time

import aiohttp
import discord
from discord.ext import commands

from ..config import COFFEE_CHANNEL_IDS
from ..logs import log, who
from . import latte
from .sources import download_image, find_image


def in_coffee_channel(channel: discord.abc.Messageable) -> bool:
    """Разрешён ли !кофе в этом канале. Ветки (треды) наследуют разрешение родительского канала."""
    if not COFFEE_CHANNEL_IDS:
        return True
    return channel.id in COFFEE_CHANNEL_IDS or getattr(channel, "parent_id", None) in COFFEE_CHANNEL_IDS


class CoffeeCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # шаблон чашки загружаем заранее: ошибка видна сразу, а первый !кофе не тормозит
        try:
            await asyncio.to_thread(latte.load_template)
            log.info("Шаблон латте-арта загружен: %s", latte.TEMPLATE_FILE)
            log.info("!кофе работает %s", f"в каналах {', '.join(map(str, sorted(COFFEE_CHANNEL_IDS)))}"
                     if COFFEE_CHANNEL_IDS else "во всех каналах (COFFEE_CHANNEL_ID не задан)")
        except Exception as e:
            log.error("Шаблон латте-арта не загружен, !кофе работать не будет: %s", e)

    @commands.command(name="кофе", aliases=["coffee", "латте"])
    @commands.guild_only()
    @commands.check(lambda ctx: in_coffee_channel(ctx.channel))  # проверки идут до кулдауна, так что он не тратится
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def coffee(self, ctx: commands.Context):
        """Рисует картинку пенкой на кофе: из ответа, из вложения или последнюю выше в канале."""
        found = await find_image(ctx)
        if isinstance(found, str):
            ctx.command.reset_cooldown(ctx)  # без картинки попытка не считается
            log.info("!кофе от %s в #%s: %s", who(ctx.author), ctx.channel, found)
            await ctx.reply(found, mention_author=False)
            return
        source, source_msg = found
        async with ctx.typing():
            started = time.perf_counter()
            data = b""
            try:
                data = await download_image(source)
                # обработка тяжёлая — в отдельном потоке, чтобы бот не замирал
                result = await asyncio.to_thread(latte.make_latte, data)
            except (ValueError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                ctx.command.reset_cooldown(ctx)
                log.warning("!кофе от %s: не удалось обработать картинку из сообщения %s (%s, %s байт): %s%s",
                            who(ctx.author), source_msg.id, getattr(source, "url", source), len(data),
                            e, f" — причина: {e.__cause__!r}" if e.__cause__ else "")
                await ctx.reply(f"Не получилось обработать картинку: {e}", mention_author=False)
                return
            await ctx.reply(file=discord.File(io.BytesIO(result), "coffee.png"), mention_author=False)
        log.info("!кофе от %s в #%s: картинка из сообщения %s, обработка %d мс",
                 who(ctx.author), ctx.channel, source_msg.id, (time.perf_counter() - started) * 1000)
