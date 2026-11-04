"""Общий сценарий команд с картинками: найти картинку → скачать → обработать эффектом → ответить файлом.

Используется и командами (!кофе), и контекстным меню (ПКМ по сообщению → Приложения → Кофе), см. menus.py."""

import asyncio
import io
import time

import aiohttp
import discord
from discord.ext import commands

from ..logs import log, who
from ..settings import current
from .effect import EffectResult, ImageEffect, run_effect, warm_up
from .sources import download_image, find_image

DEFAULT_UPLOAD_LIMIT = 10 * 1024 * 1024  # лимит файла в Discord без бустов сервера
# обработка анимации нагружает процессор на секунды: одновременно обрабатываем только одну картинку,
# остальные ждут очереди (в чате при этом видно «печатает…»)
_processing = asyncio.Semaphore(1)
MAX_PENDING = 4  # одна обрабатывается и три ждут; больше — «попробуй позже», иначе очередь растянется на минуты
PROCESS_TIMEOUT = 60  # секунд на одну обработку; дольше — отвечаем, что не вышло (обычно это огромная гифка)
_pending = 0  # сколько обработок идёт и ждёт очереди

# что отвечать, когда команду вызвали раньше, чем прошёл кулдаун
COOLDOWN_TEXT = {"кофе": "Кофе ещё варится ☕", "пиво": "Пиво ещё наливается 🍺",
                 "explode": "Картинка ещё раздувается 💥", "implode": "Картинка ещё сжимается 🌀",
                 "цитата": "Цитата ещё подбирается 📖",
                 "музей": "Картину ещё вешают 🖼️"}
BUSY_TEXT = "Сейчас обрабатывается много картинок — попробуй через минуту."
TIMEOUT_TEXT = "Картинка обрабатывается слишком долго — попробуй картинку поменьше или гифку покороче."


class Busy(Exception):
    """Очередь обработки заполнена."""


def channel_allowed(channel: discord.abc.Messageable, channel_ids: frozenset[int] | set[int]) -> bool:
    """Разрешена ли команда в канале. Пустой список — везде. Ветки (треды) наследуют разрешение родительского канала."""
    if not channel_ids:
        return True
    return channel.id in channel_ids or getattr(channel, "parent_id", None) in channel_ids


def in_fun_channel(ctx: commands.Context) -> bool:
    """Проверка для развлекательных команд: канал из настроек (fun.channels). Проверки идут до кулдауна — он не тратится."""
    return channel_allowed(ctx.channel, current().fun_channels)


def fun_cooldown(ctx) -> commands.Cooldown | None:
    """Кулдаун развлекательных команд из настроек (fun.cooldown): раз в N секунд на участника, 0 — без ограничения.
    Читается при каждом вызове, так что новое значение действует сразу после /settings upload."""
    seconds = current().fun_cooldown
    return commands.Cooldown(1, seconds) if seconds else None


# декораторы для развлекательных команд: @fun_command_checks над функцией команды
def fun_command_checks(func):
    func = commands.dynamic_cooldown(fun_cooldown, commands.BucketType.user)(func)
    func = commands.check(in_fun_channel)(func)
    return commands.guild_only()(func)


async def preload_template(effect: ImageEffect) -> None:
    """Готовит шаблон заранее: ошибка видна сразу при запуске, а первый вызов команды не тормозит."""
    if effect.template_file is None:
        log.info("Команда !%s без шаблона готова", effect.name)
        return
    try:
        await asyncio.to_thread(warm_up, effect)
        log.info("Шаблон для !%s загружен: %s", effect.name, effect.template_file)
    except Exception as e:
        log.error("Шаблон для !%s не загружен, команда работать не будет: %s", effect.name, e)


def queue_full() -> bool:
    return _pending >= MAX_PENDING


async def render(effect: ImageEffect, data: bytes, limit: int) -> EffectResult:
    """Обрабатывает картинку в очереди: по одной за раз, не больше MAX_PENDING в очереди, не дольше PROCESS_TIMEOUT.

    Busy — очередь заполнена; asyncio.TimeoutError — обработка не уложилась. Поток с обработкой остановить нельзя,
    поэтому после таймаута он дорабатывает в фоне, а следующая картинка ждёт его: иначе две тяжёлые обработки
    шли бы одновременно и бот тормозил бы ещё сильнее."""
    global _pending
    if queue_full():
        raise Busy
    _pending += 1
    try:
        await _processing.acquire()
    except BaseException:
        _pending -= 1
        raise
    started = time.perf_counter()
    task = asyncio.create_task(asyncio.to_thread(run_effect, effect, data, limit))

    def finished(t: asyncio.Task) -> None:
        global _pending
        _pending -= 1
        _processing.release()
        seconds = time.perf_counter() - started
        if seconds > PROCESS_TIMEOUT:
            failure = t.exception() if not t.cancelled() else None  # забираем ошибку, чтобы asyncio не ругался
            log.warning("!%s: обработка закончилась через %.0f с, уже после таймаута%s",
                        effect.name, seconds, f" (с ошибкой: {failure})" if failure else "")

    task.add_done_callback(finished)
    # shield: по таймауту перестаём ждать, но не отменяем задачу — её поток всё равно доработает
    return await asyncio.wait_for(asyncio.shield(task), PROCESS_TIMEOUT)


def upload_limit(guild: discord.Guild | None) -> int:
    """Результат должен влезть в лимит файла этого сервера (зависит от бустов); берём с запасом."""
    return int((guild.filesize_limit if guild else DEFAULT_UPLOAD_LIMIT) * 0.95)


async def apply_effect(effect: ImageEffect, source: discord.Attachment | str, guild: discord.Guild | None,
                       name: str, user: discord.abc.User, source_msg: discord.Message) -> EffectResult | str:
    """Скачивает картинку и обрабатывает эффектом. Результат — или текст, который ответить вместо картинки."""
    data = b""
    try:
        data = await download_image(source)
        return await render(effect, data, upload_limit(guild))
    except Busy:
        log.warning("!%s от %s: очередь обработки заполнена (%d)", name, who(user), MAX_PENDING)
        return BUSY_TEXT
    except asyncio.TimeoutError:
        log.warning("!%s от %s: обработка картинки из сообщения %s (%s байт) дольше %d с",
                    name, who(user), source_msg.id, len(data), PROCESS_TIMEOUT)
        return TIMEOUT_TEXT
    except (ValueError, aiohttp.ClientError) as e:
        log.warning("!%s от %s: не удалось обработать картинку из сообщения %s (%s, %s байт): %s%s",
                    name, who(user), source_msg.id, getattr(source, "url", source), len(data),
                    e, f" — причина: {e.__cause__!r}" if e.__cause__ else "")
        return f"Не получилось обработать картинку: {e}"


def result_file(result: EffectResult) -> discord.File:
    return discord.File(io.BytesIO(result.data), result.filename)


async def reply_with_effect(ctx: commands.Context, effect: ImageEffect) -> None:
    """Находит картинку, анимацию или видео для команды, обрабатывает эффектом и отвечает результатом."""
    name = ctx.command.name
    found = await find_image(ctx)
    if isinstance(found, str):
        ctx.command.reset_cooldown(ctx)  # без картинки попытка не считается
        log.info("!%s от %s в #%s: %s", name, who(ctx.author), ctx.channel, found)
        await ctx.reply(found, mention_author=False)
        return
    if queue_full():  # сразу, не скачивая картинку
        ctx.command.reset_cooldown(ctx)
        await ctx.reply(BUSY_TEXT, mention_author=False)
        return
    source, source_msg = found
    async with ctx.typing():
        started = time.perf_counter()
        result = await apply_effect(effect, source, ctx.guild, name, ctx.author, source_msg)
        if isinstance(result, str):
            ctx.command.reset_cooldown(ctx)
            await ctx.reply(result, mention_author=False)
            return
        await ctx.reply(file=result_file(result), mention_author=False)
    log.info("!%s от %s в #%s: из сообщения %s (%s), обработка %d мс",
             name, who(ctx.author), ctx.channel, source_msg.id, result.info, (time.perf_counter() - started) * 1000)
