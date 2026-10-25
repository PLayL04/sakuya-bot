"""Общий сценарий команд с картинками: найти картинку → скачать → обработать эффектом → ответить файлом."""

import asyncio
import io
import time

import aiohttp
import discord
from discord.ext import commands

from ..logs import log, who
from .effect import ImageEffect, run_effect, warm_up
from .sources import download_image, find_image

DEFAULT_UPLOAD_LIMIT = 10 * 1024 * 1024  # лимит файла в Discord без бустов сервера
# обработка анимации нагружает процессор на секунды: одновременно обрабатываем только одну картинку,
# остальные ждут очереди (в чате при этом видно «печатает…»)
_processing = asyncio.Semaphore(1)


def channel_allowed(channel: discord.abc.Messageable, channel_ids: set[int]) -> bool:
    """Разрешена ли команда в канале. Пустой список — везде. Ветки (треды) наследуют разрешение родительского канала."""
    if not channel_ids:
        return True
    return channel.id in channel_ids or getattr(channel, "parent_id", None) in channel_ids


async def preload_template(effect: ImageEffect, channel_ids: set[int]) -> None:
    """Готовит шаблон заранее: ошибка видна сразу при запуске, а первый вызов команды не тормозит."""
    try:
        await asyncio.to_thread(warm_up, effect)
        log.info("Шаблон для !%s загружен: %s; команда работает %s", effect.name, effect.template_file,
                 f"в каналах {', '.join(map(str, sorted(channel_ids)))}" if channel_ids else "во всех каналах")
    except Exception as e:
        log.error("Шаблон для !%s не загружен, команда работать не будет: %s", effect.name, e)


async def reply_with_effect(ctx: commands.Context, effect: ImageEffect) -> None:
    """Находит картинку, анимацию или видео для команды, обрабатывает эффектом и отвечает результатом."""
    name = ctx.command.name
    found = await find_image(ctx)
    if isinstance(found, str):
        ctx.command.reset_cooldown(ctx)  # без картинки попытка не считается
        log.info("!%s от %s в #%s: %s", name, who(ctx.author), ctx.channel, found)
        await ctx.reply(found, mention_author=False)
        return
    source, source_msg = found
    # результат должен влезть в лимит файла этого сервера (зависит от бустов); берём с запасом
    limit = int((ctx.guild.filesize_limit if ctx.guild else DEFAULT_UPLOAD_LIMIT) * 0.95)
    async with ctx.typing():
        started = time.perf_counter()
        data = b""
        try:
            data = await download_image(source)
            async with _processing:
                # обработка тяжёлая — в отдельном потоке, чтобы бот не замирал
                result = await asyncio.to_thread(run_effect, effect, data, limit)
        except (ValueError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            ctx.command.reset_cooldown(ctx)
            log.warning("!%s от %s: не удалось обработать картинку из сообщения %s (%s, %s байт): %s%s",
                        name, who(ctx.author), source_msg.id, getattr(source, "url", source), len(data),
                        e, f" — причина: {e.__cause__!r}" if e.__cause__ else "")
            await ctx.reply(f"Не получилось обработать картинку: {e}", mention_author=False)
            return
        await ctx.reply(file=discord.File(io.BytesIO(result.data), result.filename), mention_author=False)
    log.info("!%s от %s в #%s: из сообщения %s (%s), обработка %d мс",
             name, who(ctx.author), ctx.channel, source_msg.id, result.info, (time.perf_counter() - started) * 1000)
