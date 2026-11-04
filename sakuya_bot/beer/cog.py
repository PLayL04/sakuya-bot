"""Команда !пиво: картинка внутри бокала пива."""

from discord.ext import commands

from ..imaging.command import fun_command_checks, preload_template, reply_with_effect
from . import effect


class BeerCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await preload_template(effect.EFFECT)

    @commands.command(name="пиво", aliases=["beer"])
    @fun_command_checks  # только на сервере, в каналах и с кулдауном из настроек (/settings)
    async def beer(self, ctx: commands.Context):
        """Помещает картинку внутрь бокала пива: из ответа, из вложения или последнюю выше в канале. Гифки и видео — анимацией."""
        await reply_with_effect(ctx, effect.EFFECT)
