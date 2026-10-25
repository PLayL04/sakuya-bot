"""Откуда взять картинку для !кофе и !пиво: поиск в сообщениях (картинки, гифки, видео, стикеры) и скачивание."""

from pathlib import Path

import aiohttp
import discord
from discord.ext import commands

from ..config import IMG_EXTENSIONS

MAX_IMAGE_BYTES = 25 * 1024 * 1024  # гифки и видео бывают тяжёлыми; из видео всё равно берётся только начало
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v"}
HISTORY_LIMIT = 50  # сколько сообщений выше просматривать в поисках картинки


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


async def download_image(source: discord.Attachment | str) -> bytes:
    if isinstance(source, discord.Attachment):
        if source.size > MAX_IMAGE_BYTES:
            raise ValueError(f"Файл больше {MAX_IMAGE_BYTES // 1024 // 1024} МБ")
        return await source.read()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.get(source) as resp:
            resp.raise_for_status()
            if (resp.content_length or 0) > MAX_IMAGE_BYTES:
                raise ValueError(f"Файл больше {MAX_IMAGE_BYTES // 1024 // 1024} МБ")
            # читаем по кускам до конца файла: resp.content.read(n) отдаёт только то, что уже пришло, и обрезает картинку
            data = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                data += chunk
                if len(data) > MAX_IMAGE_BYTES:
                    raise ValueError(f"Файл больше {MAX_IMAGE_BYTES // 1024 // 1024} МБ")
    return bytes(data)


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
    async for prev in ctx.channel.history(limit=HISTORY_LIMIT, before=message):
        if source := image_in_message(prev):
            return source, prev
    return f"Не нашёл картинку в последних {HISTORY_LIMIT} сообщениях. Ответь командой на сообщение с картинкой или приложи её."
