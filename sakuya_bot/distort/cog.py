"""Команды !explode и !implode: «рыбий глаз» и вдавливание картинки к центру."""

from discord.ext import commands

from ..imaging.command import fun_command_checks, preload_template, reply_with_effect
from . import effect


class DistortCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await preload_template(effect.EXPLODE_EFFECT)
        await preload_template(effect.IMPLODE_EFFECT)

    @commands.command(name="explode", aliases=["взрыв"])
    @fun_command_checks  # только на сервере, в каналах и с кулдауном из настроек (/settings)
    async def explode(self, ctx: commands.Context):
        """Раздувает картинку от центра («рыбий глаз»): из ответа, из вложения или последнюю выше в канале. Гифки и видео — анимацией."""
        await reply_with_effect(ctx, effect.EXPLODE_EFFECT)

    @commands.command(name="implode", aliases=["сжатие"])
    @fun_command_checks  # только на сервере, в каналах и с кулдауном из настроек (/settings)
    async def implode(self, ctx: commands.Context):
        """Вдавливает картинку к центру: из ответа, из вложения или последнюю выше в канале. Гифки и видео — анимацией."""
        await reply_with_effect(ctx, effect.IMPLODE_EFFECT)
