"""Откуда взять картинку для !кофе: поиск в сообщениях и скачивание."""

from pathlib import Path

import aiohttp
import discord
from discord.ext import commands

from ..config import IMG_EXTENSIONS

MAX_IMAGE_BYTES = 10 * 1024 * 1024
HISTORY_LIMIT = 50  # сколько сообщений выше просматривать в поисках картинки


def image_in_message(message: discord.Message) -> discord.Attachment | str | None:
    """Вложение-картинка или ссылка на картинку (embed, стикер) из сообщения."""
    for a in message.attachments:
        if (a.content_type or "").startswith("image/") or Path(a.filename).suffix.lower() in IMG_EXTENSIONS:
            return a
    for embed in message.embeds:
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
            raise ValueError("Картинка больше 10 МБ")
        return await source.read()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        async with session.get(source) as resp:
            resp.raise_for_status()
            if (resp.content_length or 0) > MAX_IMAGE_BYTES:
                raise ValueError("Картинка больше 10 МБ")
            # читаем по кускам до конца файла: resp.content.read(n) отдаёт только то, что уже пришло, и обрезает картинку
            data = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                data += chunk
                if len(data) > MAX_IMAGE_BYTES:
                    raise ValueError("Картинка больше 10 МБ")
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
