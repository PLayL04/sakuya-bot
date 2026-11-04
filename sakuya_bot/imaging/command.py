"""Общий сценарий команд с картинками: найти картинку → скачать → обработать эффектом → ответить файлом.

Используется и командами (!кофе), и контекстным меню (ПКМ по сообщению → Приложения → Кофе), см. menus.py."""

import asyncio
import io
import time
from pathlib import Path

import discord
from discord.ext import commands

from ..logs import log, who
from ..settings import current
from .effect import EffectResult, ImageEffect, run_effect, warm_up
from .sources import DownloadError, download_media, find_image, remove_file, source_url

DEFAULT_UPLOAD_LIMIT = 10 * 1024 * 1024  # лимит файла в Discord без бустов сервера
# обработка анимации нагружает процессор на секунды: одновременно обрабатываем только одну картинку,
# остальные ждут очереди (в чате при этом видно «печатает…»)
_processing = asyncio.Semaphore(1)
# скачивание — тоже в очереди: одновременно не больше MAX_DOWNLOADS, чтобы много тяжёлых видео разом
# не забивали сеть и диск
MAX_DOWNLOADS = 2
_downloads = asyncio.Semaphore(MAX_DOWNLOADS)
MAX_PENDING = 4  # всего в очереди (скачиваются, ждут, обрабатываются); больше — «попробуй позже»
PROCESS_TIMEOUT = 60  # секунд на одну обработку; дольше — отвечаем, что не вышло (обычно это огромная гифка)
_pending = 0  # сколько обработок идёт и ждёт очереди
_downloading = 0  # сколько скачиваний идёт и ждёт очереди

# что отвечать, когда команду вызвали раньше, чем прошёл кулдаун
COOLDOWN_TEXT = {"кофе": "Кофе ещё варится ☕", "пиво": "Пиво ещё наливается 🍺",
                 "explode": "Картинка ещё раздувается 💥", "implode": "Картинка ещё сжимается 🌀",
                 "цитата": "Цитата ещё подбирается 📖",
                 "музей": "Картину ещё вешают 🖼️"}
BUSY_TEXT = "Сейчас обрабатывается много картинок — попробуй через минуту."
TIMEOUT_TEXT = "Картинка обрабатывается слишком долго — попробуй картинку поменьше или гифку покороче."
MEMORY_TEXT = "Картинка слишком тяжёлая для обработки — попробуй картинку поменьше или гифку покороче."
FAILED_TEXT = "Что-то пошло не так при обработке картинки. Подробности — в логе бота, попробуй ещё раз позже."


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
    return _pending + _downloading >= MAX_PENDING


async def download(source: discord.Attachment | str) -> Path:
    """Скачивает исходник в очереди: занимает место в MAX_PENDING, одновременно не больше MAX_DOWNLOADS.
    Busy — очередь заполнена; DownloadError — скачать не вышло."""
    global _downloading
    if queue_full():
        raise Busy
    _downloading += 1
    try:
        async with _downloads:
            return await download_media(source)
    finally:
        # место в очереди сразу переходит к обработке (render): между ними нет await, так что его никто не займёт
        _downloading -= 1


async def render(effect: ImageEffect, source: bytes | Path, limit: int) -> EffectResult:
    """Обрабатывает картинку в очереди: по одной за раз, не больше MAX_PENDING в очереди, не дольше PROCESS_TIMEOUT.

    Скачанный файл (source — Path) render удаляет сам, когда обработка закончится или не начнётся.
    Busy — очередь заполнена; asyncio.TimeoutError — обработка не уложилась. Поток с обработкой остановить нельзя,
    поэтому после таймаута он дорабатывает в фоне, а следующая картинка ждёт его: иначе две тяжёлые обработки
    шли бы одновременно и бот тормозил бы ещё сильнее. Файл тоже удаляется только тогда — поток его ещё читает."""
    global _pending

    def cleanup() -> None:
        if isinstance(source, Path):
            remove_file(source)

    if queue_full():
        cleanup()
        raise Busy
    _pending += 1
    try:
        await _processing.acquire()
    except BaseException:
        _pending -= 1
        cleanup()
        raise
    started = time.perf_counter()
    task = asyncio.create_task(asyncio.to_thread(run_effect, effect, source, limit))

    def finished(t: asyncio.Task) -> None:
        global _pending
        _pending -= 1
        _processing.release()
        cleanup()
        seconds = time.perf_counter() - started
        if seconds > PROCESS_TIMEOUT:
            failure = t.exception() if not t.cancelled() else None  # забираем ошибку, чтобы asyncio не ругался
            log.warning("!%s: обработка закончилась через %.0f с, уже после таймаута%s",
                        getattr(effect, "name", effect), seconds, f" (с ошибкой: {failure!r})" if failure else "")

    task.add_done_callback(finished)
    # shield: по таймауту перестаём ждать, но не отменяем задачу — её поток всё равно доработает
    return await asyncio.wait_for(asyncio.shield(task), PROCESS_TIMEOUT)


def upload_limit(guild: discord.Guild | None) -> int:
    """Результат должен влезть в лимит файла этого сервера (зависит от бустов); берём с запасом."""
    return int((guild.filesize_limit if guild else DEFAULT_UPLOAD_LIMIT) * 0.95)


def file_size(path: Path) -> str:
    try:
        return f"{path.stat().st_size // 1024} КБ"
    except OSError:
        return "размер неизвестен"


async def apply_effect(effect: ImageEffect, source: discord.Attachment | str, guild: discord.Guild | None,
                       name: str, user: discord.abc.User, source_msg: discord.Message) -> EffectResult | str:
    """Скачивает картинку и обрабатывает эффектом. Результат — или текст, который ответить вместо картинки.
    Любая ошибка скачивания и обработки превращается в текст для участника (и запись в лог), а не в исключение."""
    what = f"картинка из сообщения {source_msg.id} ({source_url(source)})"
    size = "не скачана"
    try:
        path = await download(source)
        size = file_size(path)
        return await render(effect, path, upload_limit(guild))
    except Busy:
        log.warning("!%s от %s: очередь обработки заполнена (%d)", name, who(user), MAX_PENDING)
        return BUSY_TEXT
    except DownloadError as e:
        log.warning("!%s от %s: %s не скачана: %s%s", name, who(user), what, e,
                    f" — причина: {e.__cause__!r}" if e.__cause__ else "")
        return str(e)
    except asyncio.TimeoutError:
        log.warning("!%s от %s: %s, %s — обработка дольше %d с", name, who(user), what, size, PROCESS_TIMEOUT)
        return TIMEOUT_TEXT
    except ValueError as e:
        log.warning("!%s от %s: не удалось обработать: %s, %s: %s%s", name, who(user), what, size,
                    e, f" — причина: {e.__cause__!r}" if e.__cause__ else "")
        return f"Не получилось обработать картинку: {e}"
    except MemoryError:
        log.error("!%s от %s: не хватило памяти: %s, %s", name, who(user), what, size)
        return MEMORY_TEXT
    except Exception:
        # неожиданная ошибка (сломанный шаблон, сбой библиотеки) — участнику понятный ответ, подробности в лог
        log.exception("!%s от %s: неожиданная ошибка обработки: %s, %s", name, who(user), what, size)
        return FAILED_TEXT


def result_file(result: EffectResult) -> discord.File:
    return discord.File(io.BytesIO(result.data), result.filename)


def send_failed_text(error: discord.HTTPException) -> str:
    if error.status == 413:
        return "Результат получился слишком большим для Discord — попробуй картинку поменьше."
    return "Не получилось отправить результат — попробуй ещё раз."


async def reply_with_effect(ctx: commands.Context, effect: ImageEffect) -> None:
    """Находит картинку, анимацию или видео для команды, обрабатывает эффектом и отвечает результатом.
    Если не вышло на любом шаге — участнику ответ, что не так, а кулдаун не тратится."""
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
        try:
            await ctx.reply(file=result_file(result), mention_author=False)
        except discord.HTTPException as e:
            ctx.command.reset_cooldown(ctx)
            log.warning("!%s от %s в #%s: результат (%s, %d КБ) не отправлен: %s", name, who(ctx.author), ctx.channel,
                        result.info, len(result.data) // 1024, e)
            await ctx.reply(send_failed_text(e), mention_author=False)
            return
    log.info("!%s от %s в #%s: из сообщения %s (%s), обработка %d мс",
             name, who(ctx.author), ctx.channel, source_msg.id, result.info, (time.perf_counter() - started) * 1000)
