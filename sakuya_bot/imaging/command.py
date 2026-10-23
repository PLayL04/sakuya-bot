"""Общий сценарий команд с картинками: найти картинку → скачать → обработать эффектом → ответить файлом."""

import asyncio
import io
import time
from collections.abc import Callable
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands

from ..logs import log, who
from .sources import download_image, find_image
from .template import load_template


def channel_allowed(channel: discord.abc.Messageable, channel_ids: set[int]) -> bool:
    """Разрешена ли команда в канале. Пустой список — везде. Ветки (треды) наследуют разрешение родительского канала."""
    if not channel_ids:
        return True
    return channel.id in channel_ids or getattr(channel, "parent_id", None) in channel_ids


async def preload_template(path: Path, command: str, channel_ids: set[int]) -> None:
    """Загружает шаблон заранее: ошибка видна сразу при запуске, а первый вызов команды не тормозит."""
    try:
        await asyncio.to_thread(load_template, path)
        log.info("Шаблон для !%s загружен: %s; команда работает %s", command, path,
                 f"в каналах {', '.join(map(str, sorted(channel_ids)))}" if channel_ids else "во всех каналах")
    except Exception as e:
        log.error("Шаблон для !%s не загружен, команда работать не будет: %s", command, e)


async def reply_with_effect(ctx: commands.Context, effect: Callable[[bytes], bytes], filename: str) -> None:
    """Находит картинку для команды, применяет effect(байты картинки) -> байты PNG и отвечает результатом."""
    name = ctx.command.name
    found = await find_image(ctx)
    if isinstance(found, str):
        ctx.command.reset_cooldown(ctx)  # без картинки попытка не считается
        log.info("!%s от %s в #%s: %s", name, who(ctx.author), ctx.channel, found)
        await ctx.reply(found, mention_author=False)
        return
    source, source_msg = found
    async with ctx.typing():
        started = time.perf_counter()
        data = b""
        try:
            data = await download_image(source)
            # обработка тяжёлая — в отдельном потоке, чтобы бот не замирал
            result = await asyncio.to_thread(effect, data)
        except (ValueError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            ctx.command.reset_cooldown(ctx)
            log.warning("!%s от %s: не удалось обработать картинку из сообщения %s (%s, %s байт): %s%s",
                        name, who(ctx.author), source_msg.id, getattr(source, "url", source), len(data),
                        e, f" — причина: {e.__cause__!r}" if e.__cause__ else "")
            await ctx.reply(f"Не получилось обработать картинку: {e}", mention_author=False)
            return
        await ctx.reply(file=discord.File(io.BytesIO(result), filename), mention_author=False)
    log.info("!%s от %s в #%s: картинка из сообщения %s, обработка %d мс",
             name, who(ctx.author), ctx.channel, source_msg.id, (time.perf_counter() - started) * 1000)
