"""Команды /reaction и реакции на сообщения (слова из правил и пинг бота): эмодзи и/или ответ картинкой."""

import time
import uuid

import discord
from discord import app_commands
from discord.ext import commands

from ..checks import AdminOnly
from ..config import DATA_DIR, IMG_DIR
from ..logs import log, who
from ..modlog import notify
from ..replies import respond, respond_lines
from ..settings import current
from ..storage import list_images, load_json, receive_image, save_json
from .emojis import MAX_EMOJIS, custom_emoji_ids, split_emojis
from .matching import MATCH_NAMES, rule_matches

REACTIONS_FILE = DATA_DIR / "reactions.json"
PING_EMOJI_FALLBACK = "💩"
MAX_FILES = 10  # больше вложений в одном сообщении Discord не принимает
CLEAR = "-"  # в /reaction edit emoji:- или file:- — убрать эмодзи или картинку

MATCH_CHOICES = [
    app_commands.Choice(name="Все формы слова («кот» сработает на «кота», «котами», но не на «котлета»)", value="forms"),
    app_commands.Choice(name="Точное слово («кот» не сработает на «кота» и «котлета»)", value="word"),
    app_commands.Choice(name="Часть слова («кот» сработает на «кота», «котлета»)", value="contains"),
]
NO_MENTIONS = discord.AllowedMentions.none()


def ping_emoji(guild: discord.Guild) -> str:
    """Эмодзи сервера (reactions.ping_emoji в настройках) ищем по имени, чтобы не хранить его ID; если его нет — запасной 💩."""
    name = current().ping_emoji
    emoji = discord.utils.get(guild.emojis, name=name)
    if emoji is None:
        log.warning("Эмодзи :%s: не найден на сервере, на пинг ставлю %s", name, PING_EMOJI_FALLBACK)
        return PING_EMOJI_FALLBACK
    return str(emoji)  # «<:MadelineAlo:id>» или «<a:MadelineAlo:id>» для анимированного


def upgrade_rule(rule: dict) -> dict:
    """Старые правила хранили один эмодзи в «emoji», новые — список «emojis» (ставятся по порядку)."""
    if "emojis" not in rule:
        old = rule.pop("emoji", None)
        rule["emojis"] = [old] if old else []
    return rule


def describe(rule: dict, code: bool = True) -> str:
    """«🇰 🇴 🇹 + картинка `cat.gif`»: что делает правило (в старых правилах есть только эмодзи)."""
    parts = []
    if rule["emojis"]:
        parts.append(" ".join(rule["emojis"]))
    if rule.get("media"):
        parts.append(f"картинка `{rule['media']}`" if code else f"картинка {rule['media']}")
    return " + ".join(parts)


def summary(rule: dict) -> str:
    """«`a1b2c3` — **кот** → 🐱 (все формы)» — для списка, ответов и канала логов."""
    return (f"`{rule['id']}` — **{rule['word']}** → {describe(rule)} ({MATCH_NAMES[rule['match']]})"
            + (" — ⏸️ на паузе" if rule.get("paused") else ""))


@app_commands.guild_only()
class ReactionCog(AdminOnly, commands.GroupCog, group_name="reaction",
                  group_description="Автоматические реакции на слова в сообщениях"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reactions: list[dict] = [upgrade_rule(r) for r in load_json(REACTIONS_FILE)]
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
        cooldown = current().image_reply_cooldown  # одно правило отвечает картинкой в канале не чаще (0 — без ограничения)
        for rule in self.reactions:
            if rule.get("paused"):
                continue
            file = rule.get("media")
            # эмодзи правила ставятся по порядку (так из них складывается комбинация, например 🇰 🇴 🇹);
            # уже поставленные другим правилом пропускаются — одинаковую реакцию Discord дважды не ставит
            new_emojis = [e for e in rule["emojis"] if e not in emojis][: MAX_EMOJIS - len(emojis)]
            new_file = file and file not in media and len(media) < MAX_FILES
            if not (new_emojis or new_file) or not rule_matches(rule, text):
                continue
            for emoji in new_emojis:
                emojis[emoji] = f"слово «{rule['word']}» (правило {rule['id']})"
            if new_file:
                key = (rule["id"], message.channel.id)
                if key not in self.last_media or now - self.last_media[key] >= cooldown:
                    self.last_media[key] = now
                    media[file] = rule
                else:
                    log.info("Правило %s: картинку в #%s не отправляю — кулдаун %d с", rule["id"], message.channel, cooldown)
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

    async def read_emojis(self, interaction: discord.Interaction, text: str) -> list[str] | None:
        """Поле emoji -> список эмодзи по порядку; None — модератору уже ответили, что не так."""
        try:
            emojis = split_emojis(text)
        except ValueError as e:
            await respond(interaction, str(e))
            return None
        # эмодзи сервера: бот должен иметь к нему доступ
        if unseen := [i for i in custom_emoji_ids(emojis) if not self.bot.get_emoji(i)]:
            bad = next(e for e in emojis if str(unseen[0]) in e)
            await respond(interaction, f"Бот не видит эмодзи {bad} — используйте эмодзи этого сервера.")
            return None
        return emojis

    @app_commands.command(name="add", description="Ставить реакцию и/или отвечать картинкой на сообщения с этим словом")
    @app_commands.describe(
        word="Слово или фраза (регистр не важен)",
        emoji="Эмодзи: один или несколько (бот поставит их по порядку). Буквы 🇰 🇴 🇹 — через пробел",
        match="Как искать слово",
        image="Отвечать на сообщение этой картинкой или GIF (загрузить новую)",
        file="Или отвечать картинкой из папки img",
    )
    @app_commands.autocomplete(file=image_autocomplete)
    @app_commands.choices(match=MATCH_CHOICES)
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
        if not word:
            await respond(interaction, "Слово не может быть пустым.")
            return
        if not (emoji or "").strip() and not image and not file:
            await respond(interaction, "Укажите эмодзи (`emoji`) и/или картинку для ответа (`image` или `file`).")
            return
        emojis = []
        if (emoji or "").strip() and (emojis := await self.read_emojis(interaction, emoji)) is None:
            return
        if (image or file) and not (file := await receive_image(interaction, image, file)):
            return

        rule = {"id": uuid.uuid4().hex[:6], "word": word, "emojis": emojis, "media": file, "match": match}
        self.reactions.append(rule)
        self.save()
        log.info("Создано правило реакции %s: «%s» → %s (%s), автор %s", rule["id"], word, describe(rule), match, who(interaction.user))
        actions = []
        if emojis:
            actions.append(f"ставить {' '.join(emojis)}" + (" (по порядку)" if len(emojis) > 1 else ""))
        if file:
            actions.append(f"отвечать картинкой `{file}`")
        await respond(interaction, f"Готово! На сообщения со словом **{word}** бот будет {' и '.join(actions)}. ID: `{rule['id']}`")
        await notify(self.bot, f"💬 {interaction.user.mention} создаёт правило реакции: {summary(rule)}")

    @app_commands.command(name="list", description="Показать все правила реакций")
    async def list_(self, interaction: discord.Interaction):
        if not self.reactions:
            await respond(interaction, "Правил пока нет.")
            return
        await respond_lines(interaction, [summary(r) for r in sorted(self.reactions, key=lambda r: r["word"])])

    async def id_autocomplete(self, interaction: discord.Interaction, current: str):
        current = current.lower()
        return [
            app_commands.Choice(name=f"{r['id']} — {r['word']} {describe(r, code=False)}"
                                     f"{' (на паузе)' if r.get('paused') else ''}"[:100], value=r["id"])
            for r in self.reactions
            if current in r["id"] or current in r["word"]
        ][:25]

    def find(self, rule_id: str) -> dict | None:
        return next((r for r in self.reactions if r["id"] == rule_id), None)

    @app_commands.command(name="edit", description="Изменить правило реакции: слово, эмодзи, способ поиска или картинку")
    @app_commands.describe(
        id="ID правила (см. /reaction list)",
        word="Новое слово или фраза",
        emoji="Новые эмодзи (по порядку); «-» — убрать эмодзи",
        match="Как искать слово",
        image="Загрузить новую картинку для ответа",
        file="Или выбрать другую из папки img; «-» — убрать картинку",
    )
    @app_commands.autocomplete(id=id_autocomplete, file=image_autocomplete)
    @app_commands.choices(match=MATCH_CHOICES)
    async def edit(
        self,
        interaction: discord.Interaction,
        id: str,
        word: str | None = None,
        emoji: str | None = None,
        match: str | None = None,
        image: discord.Attachment | None = None,
        file: str | None = None,
    ):
        rule = self.find(id)
        if not rule:
            await respond(interaction, "Правило не найдено.")
            return
        if all(v is None for v in (word, emoji, match, image, file)):
            await respond(interaction, "Укажите, что изменить: слово, эмодзи, способ поиска или картинку.")
            return
        new = dict(rule)
        if word is not None:
            new["word"] = word.strip().lower()
            if not new["word"]:
                await respond(interaction, "Слово не может быть пустым.")
                return
        if match is not None:
            new["match"] = match
        if emoji is not None:
            emojis = [] if emoji.strip() == CLEAR else await self.read_emojis(interaction, emoji)
            if emojis is None:
                return
            new["emojis"] = emojis
        clear_media = not image and file is not None and file.strip() == CLEAR
        new_media = bool(image) or (file is not None and not clear_media)  # будет новая картинка
        if clear_media:
            new["media"] = None
        if not new["emojis"] and not new["media"] and not new_media:
            await respond(interaction, "У правила не останется ни эмодзи, ни картинки — тогда его проще удалить: `/reaction remove`.")
            return
        if new_media:
            if not (new_file := await receive_image(interaction, image, file)):
                return
            new["media"] = new_file
        if rule not in self.reactions:  # удалили, пока скачивалась картинка
            await respond(interaction, "Правило удалили, пока вы его меняли.")
            self.bot.cleanup_images()
            return
        before = summary(rule)
        rule.update(new)
        self.save()
        self.bot.cleanup_images()
        log.info("Изменено правило реакции %s пользователем %s: %s → %s", id, who(interaction.user), before, summary(rule))
        await respond(interaction, f"Готово! {summary(rule)}")
        await notify(self.bot, f"✏️ {interaction.user.mention} изменяет правило реакции:\n{before}\n→ {summary(rule)}")

    @app_commands.command(name="pause", description="Временно выключить правило реакции (не удаляя его)")
    @app_commands.describe(id="ID правила (см. /reaction list)")
    @app_commands.autocomplete(id=id_autocomplete)
    async def pause(self, interaction: discord.Interaction, id: str):
        rule = self.find(id)
        if not rule:
            await respond(interaction, "Правило не найдено.")
            return
        if rule.get("paused"):
            await respond(interaction, f"Правило `{id}` уже на паузе. Включить — `/reaction resume`.")
            return
        rule["paused"] = True
        self.save()
        log.info("Правило реакции %s поставлено на паузу пользователем %s", id, who(interaction.user))
        await respond(interaction, f"Правило `{id}` на паузе и не срабатывает. Включить — `/reaction resume`.")
        await notify(self.bot, f"⏸️ {interaction.user.mention} ставит на паузу правило реакции: {summary(rule)}")

    @app_commands.command(name="resume", description="Снова включить правило реакции")
    @app_commands.describe(id="ID правила (см. /reaction list)")
    @app_commands.autocomplete(id=id_autocomplete)
    async def resume(self, interaction: discord.Interaction, id: str):
        rule = self.find(id)
        if not rule:
            await respond(interaction, "Правило не найдено.")
            return
        if not rule.get("paused"):
            await respond(interaction, f"Правило `{id}` и так работает.")
            return
        del rule["paused"]
        self.save()
        log.info("Правило реакции %s снято с паузы пользователем %s", id, who(interaction.user))
        await respond(interaction, f"Правило `{id}` снова работает.")
        await notify(self.bot, f"▶️ {interaction.user.mention} снимает с паузы правило реакции: {summary(rule)}")

    @app_commands.command(name="remove", description="Удалить правило реакции")
    @app_commands.describe(id="ID правила (см. /reaction list)")
    @app_commands.autocomplete(id=id_autocomplete)
    async def remove(self, interaction: discord.Interaction, id: str):
        rule = self.find(id)
        if not rule:
            await respond(interaction, "Правило не найдено.")
            return
        self.reactions.remove(rule)
        self.save()
        self.bot.cleanup_images()
        log.info("Удалено правило реакции %s («%s» → %s) пользователем %s", id, rule["word"], describe(rule), who(interaction.user))
        await respond(interaction, f"Правило `{id}` удалено.")
        await notify(self.bot, f"🗑️ {interaction.user.mention} удаляет правило реакции: {summary(rule)}")
