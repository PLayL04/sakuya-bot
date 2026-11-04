"""Команды !explode и !implode: «рыбий глаз» и вдавливание картинки к центру."""

from discord.ext import commands

from ..config import FUN_CHANNEL_IDS
from ..imaging.command import channel_allowed, preload_template, reply_with_effect
from . import effect


class DistortCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await preload_template(effect.EXPLODE_EFFECT, FUN_CHANNEL_IDS)
        await preload_template(effect.IMPLODE_EFFECT, FUN_CHANNEL_IDS)

    @commands.command(name="explode")
    @commands.guild_only()
    @commands.check(lambda ctx: channel_allowed(ctx.channel, FUN_CHANNEL_IDS))  # проверки идут до кулдауна, так что он не тратится
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def explode(self, ctx: commands.Context):
        """Раздувает картинку от центра («рыбий глаз»): из ответа, из вложения или последнюю выше в канале. Гифки и видео — анимацией."""
        await reply_with_effect(ctx, effect.EXPLODE_EFFECT)

    @commands.command(name="implode")
    @commands.guild_only()
    @commands.check(lambda ctx: channel_allowed(ctx.channel, FUN_CHANNEL_IDS))
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def implode(self, ctx: commands.Context):
        """Вдавливает картинку к центру: из ответа, из вложения или последнюю выше в канале. Гифки и видео — анимацией."""
        await reply_with_effect(ctx, effect.IMPLODE_EFFECT)
