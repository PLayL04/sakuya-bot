"""Команда !пиво: картинка внутри бокала пива."""

from discord.ext import commands

from ..config import FUN_CHANNEL_IDS
from ..imaging.command import channel_allowed, preload_template, reply_with_effect
from . import effect


class BeerCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await preload_template(effect.EFFECT, FUN_CHANNEL_IDS)

    @commands.command(name="пиво", aliases=["beer"])
    @commands.guild_only()
    @commands.check(lambda ctx: channel_allowed(ctx.channel, FUN_CHANNEL_IDS))  # проверки идут до кулдауна, так что он не тратится
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def beer(self, ctx: commands.Context):
        """Помещает картинку внутрь бокала пива: из ответа, из вложения или последнюю выше в канале. Гифки и видео — анимацией."""
        await reply_with_effect(ctx, effect.EFFECT)
