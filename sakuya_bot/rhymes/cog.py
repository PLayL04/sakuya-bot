"""Ответ гифкой на «да» и «нет» (template/to-yes.gif и to-no.gif) — с шансом из настроек, без пинга автора."""

import random
from collections.abc import Callable

import discord
from discord.ext import commands

from ..config import TEMPLATE_DIR
from ..logs import log, who
from ..settings import current
from .detect import rhyme_kind

FILES = {"yes": TEMPLATE_DIR / "to-yes.gif", "no": TEMPLATE_DIR / "to-no.gif"}


class RhymeCog(commands.Cog):

    def __init__(self, bot: commands.Bot, roll: Callable[[], float] = random.random):
        self.bot = bot
        self.roll = roll  # случайное число 0..1; в тестах подменяется

    async def cog_load(self):
        if missing := [p.name for p in FILES.values() if not p.exists()]:
            log.warning("Нет гифок для ответа на «да» и «нет» в %s: %s", TEMPLATE_DIR, ", ".join(missing))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        chance = current().yes_no_chance
        if chance <= 0 or message.author.bot or not message.guild or not message.content:
            return  # выключено — текст даже не проверяем
        kind = rhyme_kind(message.content)
        if kind is None or self.roll() * 100 >= chance:
            return
        path = FILES[kind]
        if not path.exists():
            log.error("Рифма на сообщение %s: нет файла %s", message.id, path)
            return
        try:
            await message.reply(file=discord.File(path), mention_author=False)
            log.info("Рифма %s на сообщение %s от %s в #%s", path.name, message.id, who(message.author), message.channel)
        except discord.HTTPException as e:
            log.error("Не удалось ответить рифмой %s на сообщение %s в #%s: %s", path.name, message.id, message.channel, e)
