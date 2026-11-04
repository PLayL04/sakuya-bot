"""Ответы на слэш-команды: видны только вызвавшему, длинные списки — несколькими сообщениями."""

import discord

MESSAGE_LIMIT = 2000  # больше символов в одном сообщении Discord не принимает


def split_message(lines: list[str], header: str = "", limit: int = MESSAGE_LIMIT) -> list[str]:
    """Собирает строки в сообщения не длиннее limit, не разрывая строки; заголовок — в начале первого.
    Слишком длинная строка обрезается с «…»."""
    messages, current = [], header
    for line in lines:
        if len(line) > limit:
            line = line[: limit - 1] + "…"
        if current and len(current) + 1 + len(line) > limit:
            messages.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        messages.append(current)
    return messages


async def respond(interaction: discord.Interaction, content: str, **kwargs) -> None:
    """Скрытый ответ на команду: первый — через response, после defer() или первого ответа — через followup."""
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True, **kwargs)
    else:
        await interaction.response.send_message(content, ephemeral=True, **kwargs)


async def respond_lines(interaction: discord.Interaction, lines: list[str], header: str = "") -> None:
    """Список (правила, расписания) — сколько угодно длинный: разбивается на несколько сообщений."""
    for message in split_message(lines, header):
        await respond(interaction, message, allowed_mentions=discord.AllowedMentions.none())
