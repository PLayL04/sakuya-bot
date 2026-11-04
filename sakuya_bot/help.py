"""Справка: /help (видна только вызвавшему; модераторам — и команды управления) и !помощь в чате."""

import discord
from discord import app_commands
from discord.ext import commands

from .checks import is_admin
from .menus import MENU_EFFECTS
from .settings import current

# что делает каждая команда; алиасы берутся из самих команд, так что справка не отстаёт от кода
FUN_COMMANDS = {
    "кофе": "картинка, нарисованная пенкой на кофе",
    "пиво": "картинка в бокале пива",
    "музей": "картинка в раме музея",
    "explode": "раздуть картинку от центра («рыбий глаз»)",
    "implode": "вдавить картинку к центру",
    "цитата": "случайная цитата из книг сервера",
    "анекдот": "случайный анекдот с anekdot.ru; со словами — найденный по ним: `!анекдот прапорщик гвозди`",
}
IMAGE_COMMANDS = ["кофе", "пиво", "музей", "explode", "implode"]
HELP_COMMAND = "помощь"

MANAGEMENT = """\
**Управление ботом** (видно только тем, кто управляет ботом)
• `/schedule daily | weekly | monthly | yearly | once` — отправлять картинку по расписанию
• `/schedule list | edit | pause | resume | remove | test` — посмотреть, изменить, приостановить, удалить, проверить
• `/reaction add` — ставить эмодзи (можно несколько — по порядку) и/или отвечать картинкой на слово в сообщении
• `/reaction list | edit | pause | resume | remove`
• `/settings show | download | upload` — роли управления, каналы, кулдауны, часовой пояс, канал логов"""


def command_line(bot: commands.Bot, name: str) -> str:
    command = bot.get_command(name)
    names = " ".join(f"`!{n}`" for n in [name, *(command.aliases if command else [])])
    return f"• {names} — {FUN_COMMANDS[name]}"


def help_text(bot: commands.Bot, admin: bool) -> str:
    s = current()
    image_lines = [command_line(bot, n) for n in IMAGE_COMMANDS]
    other_lines = [command_line(bot, n) for n in FUN_COMMANDS if n not in IMAGE_COMMANDS]
    menus = " / ".join(title for title, _, _ in MENU_EFFECTS)
    limits = []
    if s.fun_cooldown:
        limits.append(f"каждую команду — не чаще раза в {s.fun_cooldown} с")
    if s.fun_channels:
        limits.append("только в каналах " + ", ".join(f"<#{c}>" for c in sorted(s.fun_channels)))
    parts = [
        "**Команды с картинками**",
        "Приложи картинку к команде, ответь командой на сообщение с картинкой или просто напиши команду после "
        "картинки — бот возьмёт последнюю выше. Гифки и видео превращаются в анимацию (первые 6 секунд).",
        *image_lines,
        f"Или без команды: ПКМ по сообщению с картинкой → **Приложения** → {menus}.",
        "",
        "**Другое**",
        *other_lines,
        f"• `!{HELP_COMMAND}`, `/help` — эта справка",
    ]
    if limits:
        parts += ["", "Ограничения: " + "; ".join(limits) + "."]
    if admin:
        parts += ["", MANAGEMENT]
    return "\n".join(parts)


class HelpCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Какие команды есть у бота и как ими пользоваться")
    @app_commands.guild_only()
    async def help_slash(self, interaction: discord.Interaction):
        await interaction.response.send_message(help_text(self.bot, is_admin(interaction.user)), ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name=HELP_COMMAND, aliases=["help", "команды"])
    @commands.guild_only()
    async def help_prefix(self, ctx: commands.Context):
        """Справка в чат — без команд управления: их список видят только модераторы через /help."""
        await ctx.reply(help_text(self.bot, admin=False), mention_author=False,
                        allowed_mentions=discord.AllowedMentions.none())
