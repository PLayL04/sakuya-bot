"""Команды /reaction и реакции на сообщения (слова из правил и пинг бота)."""

import uuid

import discord
from discord import app_commands
from discord.ext import commands

from ..checks import AdminOnly
from ..config import DATA_DIR
from ..logs import log, who
from ..storage import load_json, save_json
from .matching import MATCH_NAMES, rule_matches

REACTIONS_FILE = DATA_DIR / "reactions.json"
PING_EMOJI_NAME = "MadelineAlo"  # эмодзи сервера, которым бот отвечает на пинг
PING_EMOJI_FALLBACK = "💩"


def ping_emoji(guild: discord.Guild) -> str:
    """Эмодзи сервера ищем по имени, чтобы не хранить его ID; если его нет — запасной 💩."""
    emoji = discord.utils.get(guild.emojis, name=PING_EMOJI_NAME)
    if emoji is None:
        log.warning("Эмодзи :%s: не найден на сервере, на пинг ставлю %s", PING_EMOJI_NAME, PING_EMOJI_FALLBACK)
        return PING_EMOJI_FALLBACK
    return str(emoji)  # «<:MadelineAlo:id>» или «<a:MadelineAlo:id>» для анимированного


@app_commands.guild_only()
class ReactionCog(AdminOnly, commands.GroupCog, group_name="reaction",
                  group_description="Автоматические реакции на слова в сообщениях"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reactions: list[dict] = load_json(REACTIONS_FILE)

    def save(self) -> None:
        save_json(REACTIONS_FILE, self.reactions)

    # ---------- реакции на сообщения ----------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not message.content:
            return
        text = message.content.lower()
        emojis = {}  # эмодзи -> причина (для лога)
        # пинг бота (упоминание, ответ с пингом или упоминание его роли) — заодно видно, что бот онлайн
        if self.bot.user in message.mentions or message.guild.self_role in message.role_mentions:
            emojis[ping_emoji(message.guild)] = "пинг бота"
        for rule in self.reactions:
            if rule["emoji"] not in emojis and rule_matches(rule, text):
                emojis[rule["emoji"]] = f"слово «{rule['word']}» (правило {rule['id']})"
        for emoji, reason in emojis.items():
            try:
                await message.add_reaction(discord.PartialEmoji.from_str(emoji))
                log.info("Реакция %s на сообщение %s от %s в #%s: %s", emoji, message.id, who(message.author), message.channel, reason)
            except discord.HTTPException as e:
                log.error("Не удалось поставить реакцию %s на сообщение %s в #%s: %s", emoji, message.id, message.channel, e)

    # ---------- команды ----------

    @app_commands.command(name="add", description="Ставить реакцию на сообщения с этим словом")
    @app_commands.describe(
        word="Слово или фраза (регистр не важен)",
        emoji="Эмодзи: обычный 😀 или эмодзи этого сервера",
        match="Как искать слово",
    )
    @app_commands.choices(match=[
        app_commands.Choice(name="Все формы слова («кот» сработает на «кота», «котами», но не на «котлета»)", value="forms"),
        app_commands.Choice(name="Точное слово («кот» не сработает на «кота» и «котлета»)", value="word"),
        app_commands.Choice(name="Часть слова («кот» сработает на «кота», «котлета»)", value="contains"),
    ])
    async def add(self, interaction: discord.Interaction, word: str, emoji: str, match: str = "forms"):
        word = word.strip().lower()
        emoji = emoji.strip()
        if not word:
            await interaction.response.send_message("Слово не может быть пустым.", ephemeral=True)
            return

        partial = discord.PartialEmoji.from_str(emoji)
        if partial.id:
            # эмодзи сервера: бот должен иметь к нему доступ
            if not self.bot.get_emoji(partial.id):
                await interaction.response.send_message("Бот не видит этот эмодзи — используйте эмодзи этого сервера.", ephemeral=True)
                return
        elif not emoji or any(ch.isascii() and ch.isalnum() for ch in emoji):
            await interaction.response.send_message("Это не похоже на эмодзи. Выберите его через панель эмодзи Discord.", ephemeral=True)
            return

        rule = {"id": uuid.uuid4().hex[:6], "word": word, "emoji": emoji, "match": match}
        self.reactions.append(rule)
        self.save()
        log.info("Создано правило реакции %s: «%s» → %s (%s), автор %s", rule["id"], word, emoji, match, who(interaction.user))
        await interaction.response.send_message(
            f"Готово! На сообщения со словом **{word}** бот будет ставить {emoji}. ID: `{rule['id']}`",
            ephemeral=True,
        )

    @app_commands.command(name="list", description="Показать все правила реакций")
    async def list_(self, interaction: discord.Interaction):
        if not self.reactions:
            await interaction.response.send_message("Правил пока нет.", ephemeral=True)
            return
        lines = [
            f"`{r['id']}` — **{r['word']}** → {r['emoji']} ({MATCH_NAMES[r['match']]})"
            for r in sorted(self.reactions, key=lambda r: r["word"])
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    async def id_autocomplete(self, interaction: discord.Interaction, current: str):
        current = current.lower()
        return [
            app_commands.Choice(name=f"{r['id']} — {r['word']} {r['emoji']}"[:100], value=r["id"])
            for r in self.reactions
            if current in r["id"] or current in r["word"]
        ][:25]

    @app_commands.command(name="remove", description="Удалить правило реакции")
    @app_commands.describe(id="ID правила (см. /reaction list)")
    @app_commands.autocomplete(id=id_autocomplete)
    async def remove(self, interaction: discord.Interaction, id: str):
        rule = next((r for r in self.reactions if r["id"] == id), None)
        if not rule:
            await interaction.response.send_message("Правило не найдено.", ephemeral=True)
            return
        self.reactions.remove(rule)
        self.save()
        log.info("Удалено правило реакции %s («%s» → %s) пользователем %s", id, rule["word"], rule["emoji"], who(interaction.user))
        await interaction.response.send_message(f"Правило `{id}` удалено.", ephemeral=True)
