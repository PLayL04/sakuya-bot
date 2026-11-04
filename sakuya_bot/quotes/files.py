"""Команды /quotes: тексты для !цитата — посмотреть, добавить, скачать, удалить. Только для тех, кто управляет ботом.

Файлы лежат в папке цитат (QUOTES_DIR) как обычные *.txt — их по-прежнему можно класть туда и вручную.
!цитата перечитывает папку при каждом вызове, поэтому новые и удалённые файлы действуют сразу, без перезапуска."""

import io
import re
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from ..checks import AdminOnly
from ..logs import log, who
from ..modlog import notify
from ..replies import respond, respond_lines
from .picker import QuoteLibrary, QuoteSource, decode, parse_text

MAX_FILE_BYTES = 2 * 1024 * 1024  # тексты книг весят десятки-сотни КБ; больше — скорее всего, не тот файл
MAX_NAME_CHARS = 60
# в имени файла — только буквы, цифры, пробел, дефис, подчёркивание, точка: никаких папок («../») и спецсимволов
UNSAFE_NAME_CHARS = re.compile(r"[^\w\s.-]")
NO_MENTIONS = discord.AllowedMentions.none()


def safe_name(name: str) -> str | None:
    """«Ночное такси.txt», «../../bot.py» -> имя файла *.txt без папок и опасных символов; None — имя не годится."""
    stem = Path(name.replace("\\", "/")).name
    if stem.lower().endswith(".txt"):
        stem = stem[:-4]
    stem = " ".join(UNSAFE_NAME_CHARS.sub("", stem).split()).strip(". ")[:MAX_NAME_CHARS].strip(". ")
    return f"{stem}.txt" if stem else None


def describe(path: Path, source: QuoteSource, total: int) -> str:
    """«`taxi.txt` — «Ночное такси», 54 000 символов, 312 абзацев, 44 % выдачи» — для списка и ответов."""
    if not source.paragraphs:
        return f"`{path.name}` — «{source.name}»: ⚠️ не нашлось ни одного предложения, в цитатах не участвует"
    share = f", {source.size * 100 / total:.0f} % выдачи" if total else ""
    chars = f"{source.size:,}".replace(",", " ")  # 54 000
    return f"`{path.name}` — «{source.name}», {chars} символов, {len(source.paragraphs)} абзацев{share}"


@app_commands.guild_only()
class QuoteFilesCog(AdminOnly, commands.GroupCog, group_name="quotes",
                    group_description="Тексты для !цитата: посмотреть, добавить, скачать, удалить"):

    def __init__(self, bot: commands.Bot, library: QuoteLibrary):
        self.bot = bot
        self.library = library

    @property
    def folder(self) -> Path:
        return self.library.folder

    def total(self, files: list[tuple[Path, QuoteSource]]) -> int:
        return sum(s.size for _, s in files if s.paragraphs)

    async def name_autocomplete(self, interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=path.name, value=path.name)
                for path, _ in self.library.files() if current.lower() in path.name.lower()][:25]

    def find(self, name: str) -> Path | None:
        return next((path for path, _ in self.library.files() if path.name == name), None)

    @app_commands.command(name="list", description="Тексты для !цитата: сколько в каждом символов и какая доля выдачи")
    async def list_(self, interaction: discord.Interaction):
        files = self.library.files()
        if not files:
            await respond(interaction, "Текстов для цитат пока нет. Добавить — `/quotes upload`.")
            return
        total = self.total(files)
        await respond_lines(interaction, [describe(p, s, total) for p, s in files],
                            header=f"Тексты для !цитата ({len(files)}):")

    @app_commands.command(name="upload", description="Добавить или заменить текст для !цитата (файл .txt)")
    @app_commands.describe(file="Текстовый файл .txt в UTF-8 или Windows-1251; первая строка «# Автор — Название» — подпись",
                           name="Имя файла на сервере (по умолчанию — как у загруженного)",
                           replace="Заменить файл с таким же именем, если он уже есть")
    async def upload(self, interaction: discord.Interaction, file: discord.Attachment, name: str | None = None,
                     replace: bool = False):
        if not file.filename.lower().endswith(".txt"):
            await respond(interaction, "Нужен текстовый файл `.txt`.")
            return
        target = safe_name(name or file.filename)
        if target is None:
            await respond(interaction, "Не получилось составить имя файла — укажите его в `name`, буквами или цифрами.")
            return
        if file.size > MAX_FILE_BYTES:
            await respond(interaction, f"Файл слишком большой: больше {MAX_FILE_BYTES // 1024 // 1024} МБ.")
            return
        path = self.folder / target
        if path.exists() and not replace:
            await respond(interaction, f"Файл `{target}` уже есть. Чтобы заменить его, добавьте `replace:True`, "
                                       "или выберите другое имя в `name`.")
            return
        await interaction.response.defer(ephemeral=True)  # скачивание может занять больше 3 секунд
        try:
            data = await file.read()
        except discord.HTTPException as e:
            log.warning("/quotes upload от %s: не удалось скачать %s: %s", who(interaction.user), file.filename, e)
            await respond(interaction, "Не получилось скачать файл, попробуйте ещё раз.")
            return
        try:
            text = decode(data)
        except UnicodeDecodeError:
            text = None
        if text is None or "\x00" in text:
            await respond(interaction, "Это не похоже на текст. Нужен обычный текстовый файл (.txt) в UTF-8 "
                                       "или Windows-1251 — например, сохранённый из Блокнота.")
            return
        title, paragraphs = parse_text(text)
        if not paragraphs:
            await respond(interaction, "В файле не нашлось ни одного предложения для цитат: заголовки и строки "
                                       "без точки в конце не считаются. Файл не сохранён.")
            return
        replaced = path.exists()
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            # временный файл и подмена: !цитата не прочитает полузаписанный текст
            tmp = path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        except OSError as e:
            log.exception("/quotes upload от %s: не удалось записать %s", who(interaction.user), path)
            await respond(interaction, f"Не получилось сохранить файл на сервере ({type(e).__name__}).")
            return
        files = self.library.files()
        source = next(s for p, s in files if p == path)
        summary = describe(path, source, self.total(files))
        action = "заменяет" if replaced else "добавляет"
        log.info("Текст для цитат %s пользователем %s: %s", "заменён" if replaced else "добавлен", who(interaction.user),
                 summary)
        await respond(interaction, f"Готово! {summary}", allowed_mentions=NO_MENTIONS)
        await notify(self.bot, f"📖 {interaction.user.mention} {action} текст для цитат: {summary}")

    @app_commands.command(name="download", description="Скачать текст для !цитата")
    @app_commands.describe(name="Имя файла (см. /quotes list)")
    @app_commands.autocomplete(name=name_autocomplete)
    async def download(self, interaction: discord.Interaction, name: str):
        path = self.find(name)
        if path is None:
            await respond(interaction, f"Файла `{safe_name(name) or name}` нет.")
            return
        try:
            data = path.read_bytes()
        except OSError as e:
            await respond(interaction, f"Не получилось прочитать файл ({type(e).__name__}).")
            return
        if len(data) > interaction.guild.filesize_limit:
            await respond(interaction, "Файл больше лимита Discord на этом сервере — скачайте его с сервера бота.")
            return
        await respond(interaction, f"`{path.name}`:", file=discord.File(io.BytesIO(data), path.name))

    @app_commands.command(name="remove", description="Удалить текст для !цитата")
    @app_commands.describe(name="Имя файла (см. /quotes list)")
    @app_commands.autocomplete(name=name_autocomplete)
    async def remove(self, interaction: discord.Interaction, name: str):
        path = self.find(name)
        if path is None:
            await respond(interaction, f"Файла `{safe_name(name) or name}` нет.")
            return
        files = self.library.files()
        summary = describe(path, next(s for p, s in files if p == path), self.total(files))
        try:
            data = path.read_bytes()
            path.unlink()
        except OSError as e:
            log.warning("/quotes remove от %s: не удалось удалить %s: %s", who(interaction.user), path, e)
            await respond(interaction, f"Не получилось удалить файл ({type(e).__name__}) — попробуйте позже.")
            return
        log.info("Текст для цитат удалён пользователем %s: %s", who(interaction.user), summary)
        # удалённый файл — в ответе: удалили по ошибке — его можно загрузить обратно через /quotes upload
        backup = {"file": discord.File(io.BytesIO(data), path.name)} if len(data) <= interaction.guild.filesize_limit else {}
        await respond(interaction, f"Удалено: {summary}." + (" Копия файла — в этом сообщении." if backup else ""),
                      allowed_mentions=NO_MENTIONS, **backup)
        await notify(self.bot, f"🗑️ {interaction.user.mention} удаляет текст для цитат: {summary}")
