"""Откуда взять картинку для !кофе и !пиво: поиск в сообщениях (картинки, гифки, видео, стикеры) и скачивание."""

import asyncio
import os
import tempfile
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands

from ..config import DOWNLOAD_DIR, IMG_EXTENSIONS
from ..logs import log

VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v"}
HISTORY_LIMIT = 50  # сколько сообщений выше просматривать в поисках картинки
# размер файла не ограничен: он пишется на диск по кускам, а перед обработкой всё равно уменьшается (media.py).
# Ограничено только время: слишком медленная загрузка обрывается
DOWNLOAD_TIMEOUT = 120  # секунд на всё скачивание
READ_TIMEOUT = 30  # секунд без новых данных — соединение считается зависшим
CHUNK = 1024 * 1024


class DownloadError(Exception):
    """Скачать не вышло. Текст — для ответа участнику."""


def image_in_message(message: discord.Message) -> discord.Attachment | str | None:
    """Вложение (картинка, гифка, видео) или ссылка на картинку (embed, стикер) из сообщения."""
    for a in message.attachments:
        kind = (a.content_type or "").split("/")[0]
        if kind in ("image", "video") or Path(a.filename).suffix.lower() in IMG_EXTENSIONS | VIDEO_EXTENSIONS:
            return a
    for embed in message.embeds:
        # гифки из Tenor и Giphy приходят как embed «gifv»: настоящая анимация в нём — mp4-видео, а не превью-картинка
        if embed.type == "gifv" and embed.video and (embed.video.proxy_url or embed.video.url):
            return embed.video.proxy_url or embed.video.url
        for part in (embed.image, embed.thumbnail):
            if part and (part.proxy_url or part.url):
                return part.proxy_url or part.url
    for sticker in message.stickers:
        # lottie — векторная анимация, не картинка; её не поддерживаем
        if sticker.format in (discord.StickerFormatType.png, discord.StickerFormatType.apng, discord.StickerFormatType.gif):
            return sticker.url
    return None


def source_url(source: discord.Attachment | str) -> str:
    return source.url if isinstance(source, discord.Attachment) else source


async def fetch(url: str, dest: Path) -> None:
    """Скачивает url в файл dest по кускам: в памяти не держится весь файл, сколько бы он ни весил."""
    timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT, sock_read=READ_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            with dest.open("wb") as f:
                # по кускам до конца файла: resp.content.read(n) отдаёт только то, что уже пришло, и обрезает картинку
                async for chunk in resp.content.iter_chunked(CHUNK):
                    f.write(chunk)


def new_download_path() -> Path:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="media-", suffix=".tmp", dir=DOWNLOAD_DIR)
    os.close(fd)
    return Path(name)


def remove_file(path: Path) -> None:
    """Удаляет скачанный файл; если не вышло (на Windows файл бывает занят) — его уберёт clear_downloads при запуске."""
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        log.warning("Не удалось удалить временный файл %s: %s", path, e)


def clear_downloads() -> None:
    """При запуске: файлы, оставшиеся от сбоя посреди обработки."""
    if DOWNLOAD_DIR.is_dir():
        for path in DOWNLOAD_DIR.glob("media-*.tmp"):
            remove_file(path)


async def download_media(source: discord.Attachment | str) -> Path:
    """Скачивает картинку, анимацию или видео во временный файл и возвращает путь к нему (удалить — remove_file).
    DownloadError с текстом для участника — если не вышло; недокачанный файл при этом удаляется."""
    path = new_download_path()
    try:
        await fetch(source_url(source), path)
        return path
    except aiohttp.ClientResponseError as e:
        reason = ("картинку удалили или ссылка на неё устарела" if e.status in (403, 404, 410)
                  else f"Discord ответил ошибкой {e.status}, попробуй ещё раз")
        error = DownloadError(f"Не получилось скачать картинку: {reason}.")
    except (asyncio.TimeoutError, aiohttp.ServerTimeoutError):
        error = DownloadError("Картинка скачивается слишком долго — попробуй ещё раз или файл поменьше.")
    except aiohttp.ClientError:
        error = DownloadError("Не получилось скачать картинку: ошибка сети, попробуй ещё раз.")
    except OSError:
        log.exception("Не удалось записать скачанный файл %s", path)
        error = DownloadError("Не получилось сохранить картинку на сервере бота — попробуй позже.")
    except BaseException:
        remove_file(path)  # в том числе отмена задачи
        raise
    remove_file(path)
    raise error


async def find_image(ctx: commands.Context) -> tuple[discord.Attachment | str, discord.Message] | str:
    """Ищет картинку: в самом сообщении, в сообщении-ответе, иначе последнюю выше. Возвращает (картинка, сообщение) или текст ошибки."""
    message = ctx.message
    if source := image_in_message(message):
        return source, message
    if message.reference and message.reference.message_id:
        ref = message.reference.resolved
        if not isinstance(ref, discord.Message):
            try:
                ref = await ctx.channel.fetch_message(message.reference.message_id)
            except discord.HTTPException:
                return "Не могу найти сообщение, на которое ты ответил."
        if source := image_in_message(ref):
            return source, ref
        return "В сообщении, на которое ты ответил, нет картинки."
    try:
        async for prev in ctx.channel.history(limit=HISTORY_LIMIT, before=message):
            if source := image_in_message(prev):
                return source, prev
    except discord.HTTPException as e:
        log.warning("Не удалось прочитать историю #%s в поисках картинки: %s", ctx.channel, e)
        return "Не могу посмотреть сообщения выше (нет права «Читать историю сообщений»?). Ответь командой на сообщение с картинкой или приложи её."
    return f"Не нашёл картинку в последних {HISTORY_LIMIT} сообщениях. Ответь командой на сообщение с картинкой или приложи её."
