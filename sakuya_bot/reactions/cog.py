"""Команды /reaction и реакции на сообщения (слова из правил и пинг бота): эмодзи и/или ответ картинкой."""

import time
import uuid

import discord
from discord import app_commands
from discord.ext import commands

from ..checks import AdminOnly
from ..config import DATA_DIR, IMG_DIR
from ..logs import log, who
from ..storage import is_image, list_images, load_json, save_image, save_json
from .matching import MATCH_NAMES, rule_matches

REACTIONS_FILE = DATA_DIR / "reactions.json"
PING_EMOJI_NAME = "MadelineAlo"  # эмодзи сервера, которым бот отвечает на пинг
PING_EMOJI_FALLBACK = "💩"
MEDIA_COOLDOWN = 60  # секунд: одно правило отвечает картинкой в канале не чаще этого (0 — без ограничения)
MAX_FILES = 10  # больше вложений в одном сообщении Discord не принимает


def ping_emoji(guild: discord.Guild) -> str:
    """Эмодзи сервера ищем по имени, чтобы не хранить его ID; если его нет — запасной 💩."""
    emoji = discord.utils.get(guild.emojis, name=PING_EMOJI_NAME)
    if emoji is None:
        log.warning("Эмодзи :%s: не найден на сервере, на пинг ставлю %s", PING_EMOJI_NAME, PING_EMOJI_FALLBACK)
        return PING_EMOJI_FALLBACK
    return str(emoji)  # «<:MadelineAlo:id>» или «<a:MadelineAlo:id>» для анимированного


def describe(rule: dict, code: bool = True) -> str:
    """«🐱 + картинка `cat.gif`»: что делает правило (в старых правилах есть только эмодзи)."""
    parts = []
    if rule.get("emoji"):
        parts.append(rule["emoji"])
    if rule.get("media"):
        parts.append(f"картинка `{rule['media']}`" if code else f"картинка {rule['media']}")
    return " + ".join(parts)


@app_commands.guild_only()
class ReactionCog(AdminOnly, commands.GroupCog, group_name="reaction",
                  group_description="Автоматические реакции на слова в сообщениях"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reactions: list[dict] = load_json(REACTIONS_FILE)
        self.last_media: dict[tuple[str, int], float] = {}  # (ID правила, ID канала) -> когда отвечали картинкой

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
        media = {}  # файл -> правило (для лога)
        now = time.monotonic()
        for rule in self.reactions:
            emoji, file = rule.get("emoji"), rule.get("media")
            new_emoji = emoji and emoji not in emojis
            new_file = file and file not in media and len(media) < MAX_FILES
            if not (new_emoji or new_file) or not rule_matches(rule, text):
                continue
            if new_emoji:
                emojis[emoji] = f"слово «{rule['word']}» (правило {rule['id']})"
            if new_file:
                key = (rule["id"], message.channel.id)
                if now - self.last_media.get(key, -MEDIA_COOLDOWN) >= MEDIA_COOLDOWN:
                    self.last_media[key] = now
                    media[file] = rule
                else:
                    log.info("Правило %s: картинку в #%s не отправляю — кулдаун %d с", rule["id"], message.channel, MEDIA_COOLDOWN)
        for emoji, reason in emojis.items():
            try:
                await message.add_reaction(discord.PartialEmoji.from_str(emoji))
                log.info("Реакция %s на сообщение %s от %s в #%s: %s", emoji, message.id, who(message.author), message.channel, reason)
            except discord.HTTPException as e:
                log.error("Не удалось поставить реакцию %s на сообщение %s в #%s: %s", emoji, message.id, message.channel, e)
        if media:
            await self.reply_media(message, media)

    async def reply_media(self, message: discord.Message, media: dict[str, dict]) -> None:
        """Отвечает на сообщение картинками из правил, не пингуя автора."""
        files = []
        for file, rule in media.items():
            path = IMG_DIR / file
            if path.exists():
                files.append(discord.File(path))
            else:
                log.error("Правило %s: файл не найден: %s", rule["id"], path)
        if not files:
            return
        names = ", ".join(f.filename for f in files)
        try:
            await message.reply(files=files, mention_author=False)
            log.info("Ответ картинкой %s на сообщение %s от %s в #%s (правила %s)",
                     names, message.id, who(message.author), message.channel, ", ".join(r["id"] for r in media.values()))
        except discord.HTTPException as e:
            log.error("Не удалось ответить картинкой %s на сообщение %s в #%s: %s", names, message.id, message.channel, e)

    # ---------- команды ----------

    async def image_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=name, value=name)
            for name in list_images()
            if current.lower() in name.lower()
        ][:25]

    @app_commands.command(name="add", description="Ставить реакцию и/или отвечать картинкой на сообщения с этим словом")
    @app_commands.describe(
        word="Слово или фраза (регистр не важен)",
        emoji="Эмодзи: обычный 😀 или эмодзи этого сервера",
        match="Как искать слово",
        image="Отвечать на сообщение этой картинкой или GIF (загрузить новую)",
        file="Или отвечать картинкой из папки img",
    )
    @app_commands.autocomplete(file=image_autocomplete)
    @app_commands.choices(match=[
        app_commands.Choice(name="Все формы слова («кот» сработает на «кота», «котами», но не на «котлета»)", value="forms"),
        app_commands.Choice(name="Точное слово («кот» не сработает на «кота» и «котлета»)", value="word"),
        app_commands.Choice(name="Часть слова («кот» сработает на «кота», «котлета»)", value="contains"),
    ])
    async def add(
        self,
        interaction: discord.Interaction,
        word: str,
        emoji: str | None = None,
        match: str = "forms",
        image: discord.Attachment | None = None,
        file: str | None = None,
    ):
        word = word.strip().lower()
        emoji = (emoji or "").strip() or None
        if not word:
            await interaction.response.send_message("Слово не может быть пустым.", ephemeral=True)
            return
        if not emoji and not image and not file:
            await interaction.response.send_message(
                "Укажите эмодзи (`emoji`) и/или картинку для ответа (`image` или `file`).", ephemeral=True)
            return

        partial = discord.PartialEmoji.from_str(emoji) if emoji else None
        if partial and partial.id:
            # эмодзи сервера: бот должен иметь к нему доступ
            if not self.bot.get_emoji(partial.id):
                await interaction.response.send_message("Бот не видит этот эмодзи — используйте эмодзи этого сервера.", ephemeral=True)
                return
        elif emoji and any(ch.isascii() and ch.isalnum() for ch in emoji):
            await interaction.response.send_message("Это не похоже на эмодзи. Выберите его через панель эмодзи Discord.", ephemeral=True)
            return

        if image:
            if not is_image(image):
                await interaction.response.send_message("Это не картинка (подходят PNG, JPG, GIF, WEBP).", ephemeral=True)
                return
            if image.size > interaction.guild.filesize_limit:
                await interaction.response.send_message(
                    f"Файл слишком большой: на этом сервере бот может отправлять до {interaction.guild.filesize_limit // 2**20} МБ.",
                    ephemeral=True)
                return
            file = await save_image(image)
        elif file:
            if file not in list_images():
                await interaction.response.send_message(f"Файл `{file}` не найден в папке img.", ephemeral=True)
                return

        rule = {"id": uuid.uuid4().hex[:6], "word": word, "emoji": emoji, "media": file, "match": match}
        self.reactions.append(rule)
        self.save()
        log.info("Создано правило реакции %s: «%s» → %s (%s), автор %s", rule["id"], word, describe(rule), match, who(interaction.user))
        actions = []
        if emoji:
            actions.append(f"ставить {emoji}")
        if file:
            actions.append(f"отвечать картинкой `{file}`")
        await interaction.response.send_message(
            f"Готово! На сообщения со словом **{word}** бот будет {' и '.join(actions)}. ID: `{rule['id']}`",
            ephemeral=True,
        )

    @app_commands.command(name="list", description="Показать все правила реакций")
    async def list_(self, interaction: discord.Interaction):
        if not self.reactions:
            await interaction.response.send_message("Правил пока нет.", ephemeral=True)
            return
        lines = [
            f"`{r['id']}` — **{r['word']}** → {describe(r)} ({MATCH_NAMES[r['match']]})"
            for r in sorted(self.reactions, key=lambda r: r["word"])
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    async def id_autocomplete(self, interaction: discord.Interaction, current: str):
        current = current.lower()
        return [
            app_commands.Choice(name=f"{r['id']} — {r['word']} {describe(r, code=False)}"[:100], value=r["id"])
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
        log.info("Удалено правило реакции %s («%s» → %s) пользователем %s", id, rule["word"], describe(rule), who(interaction.user))
        await interaction.response.send_message(f"Правило `{id}` удалено.", ephemeral=True)
