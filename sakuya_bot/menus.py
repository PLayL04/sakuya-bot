"""Контекстное меню: ПКМ по сообщению с картинкой → «Приложения» → Кофе / Пиво / Музей / Взрыв / Сжатие.

То же, что !кофе и другие команды с картинками, но картинка берётся из выбранного сообщения, а писать команду
не нужно. Каналы и кулдаун — те же, что у команд (кулдаун общий: !кофе и «Кофе» в меню считаются вместе).
Discord разрешает боту не больше 5 пунктов меню для сообщений — их ровно 5."""

import time
from types import SimpleNamespace

import discord
from discord import app_commands
from discord.ext import commands

from .beer.effect import EFFECT as BEER
from .coffee.latte import EFFECT as COFFEE
from .config import GUILD
from .distort.effect import EXPLODE_EFFECT, IMPLODE_EFFECT
from .imaging.command import (BUSY_TEXT, COOLDOWN_TEXT, apply_effect, channel_allowed, queue_full, result_file)
from .imaging.effect import ImageEffect
from .imaging.sources import image_in_message
from .logs import log, who
from .museum.effect import EFFECT as MUSEUM
from .settings import current

# название в меню, команда (её кулдаун общий с пунктом меню), эффект
MENU_EFFECTS: list[tuple[str, str, ImageEffect]] = [
    ("Кофе", "кофе", COFFEE),
    ("Пиво", "пиво", BEER),
    ("Музей", "музей", MUSEUM),
    ("Взрыв", "explode", EXPLODE_EFFECT),
    ("Сжатие", "implode", IMPLODE_EFFECT),
]
MAX_MESSAGE_MENUS = 5


class MenuCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.menus = [self.make_menu(*item) for item in MENU_EFFECTS]

    async def cog_load(self):
        for menu in self.menus:
            self.bot.tree.add_command(menu, guild=GUILD)

    async def cog_unload(self):
        for menu in self.menus:
            self.bot.tree.remove_command(menu.name, type=menu.type, guild=GUILD)

    def make_menu(self, title: str, command_name: str, effect: ImageEffect) -> app_commands.ContextMenu:
        async def callback(interaction: discord.Interaction, message: discord.Message):
            await self.apply(interaction, message, command_name, effect)
        return app_commands.ContextMenu(name=title, callback=callback)

    def cooldown(self, command_name: str, user: discord.abc.User):
        """Кулдаун той же команды (!кофе для «Кофе»), чтобы меню не обходило ограничение. None — без кулдауна."""
        command = self.bot.get_command(command_name)
        # у команды кулдаун из настроек (fun_cooldown); ключ — участник, поэтому хватает объекта с author
        return command._buckets.get_bucket(SimpleNamespace(author=user), time.time()) if command else None

    async def apply(self, interaction: discord.Interaction, message: discord.Message, command_name: str,
                    effect: ImageEffect) -> None:
        user = interaction.user
        if not interaction.guild:
            await interaction.response.send_message("Это работает только на сервере.", ephemeral=True)
            return
        if not channel_allowed(interaction.channel, current().fun_channels):
            await interaction.response.send_message("В этом канале команды с картинками не работают.", ephemeral=True)
            return
        source = image_in_message(message)
        if source is None:
            await interaction.response.send_message("В этом сообщении нет картинки, гифки или видео.", ephemeral=True)
            return
        bucket = self.cooldown(command_name, user)
        if bucket and (retry := bucket.update_rate_limit(time.time())):
            waiting = COOLDOWN_TEXT.get(command_name, "Подожди немного ⏳")
            await interaction.response.send_message(f"{waiting} Попробуй через {retry:.0f} сек.", ephemeral=True)
            return
        if queue_full():
            if bucket:
                bucket.reset()
            await interaction.response.send_message(BUSY_TEXT, ephemeral=True)
            return
        await interaction.response.defer(thinking=True)  # «бот думает…» видно всем — потом на его месте картинка
        started = time.perf_counter()
        result = await apply_effect(effect, source, interaction.guild, command_name, user, message)
        if isinstance(result, str):
            if bucket:
                bucket.reset()  # не вышло — попытка не считается
            await interaction.followup.send(result)
            return
        await interaction.followup.send(file=result_file(result))
        log.info("Меню «%s» от %s в #%s: из сообщения %s (%s), обработка %d мс", command_name, who(user),
                 interaction.channel, message.id, result.info, (time.perf_counter() - started) * 1000)


assert len(MENU_EFFECTS) <= MAX_MESSAGE_MENUS, "Discord не покажет больше 5 пунктов меню для сообщений"
