"""Канал логов для модераторов ([logs] channel в настройках): кто что создал, изменил, удалил, и что не отправилось.

Полный журнал по-прежнему пишется в logs/ на сервере; сюда — только то, что нужно модерации, коротко."""

import discord

from .logs import log
from .replies import MESSAGE_LIMIT
from .settings import current


async def notify(bot: discord.Client, text: str) -> None:
    """Пишет в канал логов, если он задан. Ошибки отправки не мешают самой команде — только запись в журнал."""
    channel_id = current().log_channel
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        log.warning("Канал логов %s не найден (удалён или бот его не видит): %s", channel_id, text)
        return
    if len(text) > MESSAGE_LIMIT:
        text = text[: MESSAGE_LIMIT - 1] + "…"
    try:
        # упоминания участников и ролей в тексте не пингуют — это просто журнал
        await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as e:
        log.warning("Не удалось написать в канал логов #%s: %s", channel, e)
