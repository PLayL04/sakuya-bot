"""Команда !кофе: картинка, нарисованная пенкой на кофе."""

from discord.ext import commands

from ..imaging.command import fun_command_checks, preload_template, reply_with_effect
from . import latte


class CoffeeCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await preload_template(latte.EFFECT)

    @commands.command(name="кофе", aliases=["coffee", "латте"])
    @fun_command_checks  # только на сервере, в каналах и с кулдауном из настроек (/settings)
    async def coffee(self, ctx: commands.Context):
        """Рисует картинку пенкой на кофе: из ответа, из вложения или последнюю выше в канале. Гифки и видео — анимацией."""
        await reply_with_effect(ctx, latte.EFFECT)
