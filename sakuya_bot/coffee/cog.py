"""Команда !кофе: картинка, нарисованная пенкой на кофе."""

from discord.ext import commands

from ..config import COFFEE_CHANNEL_IDS
from ..imaging.command import channel_allowed, preload_template, reply_with_effect
from . import latte


class CoffeeCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await preload_template(latte.TEMPLATE_FILE, "кофе", COFFEE_CHANNEL_IDS)

    @commands.command(name="кофе", aliases=["coffee", "латте"])
    @commands.guild_only()
    @commands.check(lambda ctx: channel_allowed(ctx.channel, COFFEE_CHANNEL_IDS))  # проверки идут до кулдауна, так что он не тратится
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def coffee(self, ctx: commands.Context):
        """Рисует картинку пенкой на кофе: из ответа, из вложения или последнюю выше в канале."""
        await reply_with_effect(ctx, latte.make_latte, "coffee.png")
