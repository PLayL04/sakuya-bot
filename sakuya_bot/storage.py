"""Хранение данных: JSON-файлы (data/*.json) и картинки (img/)."""

import json
import uuid
from pathlib import Path

import discord

from .config import DATA_DIR, IMG_DIR, IMG_EXTENSIONS
from .logs import log
from .replies import respond

UPLOADS_FILE = DATA_DIR / "uploads.json"  # картинки, загруженные через команды: только их бот удаляет сам


def load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: list[dict]) -> None:
    # сначала пишем во временный файл, потом подменяем: если бот упадёт посреди записи, данные не испортятся
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def list_images() -> list[str]:
    IMG_DIR.mkdir(exist_ok=True)
    return sorted(p.name for p in IMG_DIR.iterdir() if p.suffix.lower() in IMG_EXTENSIONS)


def is_image(attachment: discord.Attachment) -> bool:
    return Path(attachment.filename).suffix.lower() in IMG_EXTENSIONS


def size_problem(size: int, guild: discord.Guild) -> str | None:
    """Влезет ли файл в лимит отправки на этом сервере (зависит от бустов); None — влезет, иначе текст ответа."""
    if size > guild.filesize_limit:
        return f"Файл слишком большой: на этом сервере бот может отправлять до {guild.filesize_limit // 2**20} МБ."
    return None


def image_problem(image: discord.Attachment | None, file: str | None, guild: discord.Guild) -> str | None:
    """Проверка картинки для /schedule и /reaction: загруженная (image) или из папки img (file)."""
    if image:
        if not is_image(image):
            return "Это не картинка (подходят PNG, JPG, GIF, WEBP)."
        return size_problem(image.size, guild)
    if file:
        if file not in list_images():
            return f"Файл `{file}` не найден в папке img."
        return size_problem((IMG_DIR / file).stat().st_size, guild)
    return None


async def save_image(attachment: discord.Attachment) -> str:
    """Сохраняет вложение в img/ под уникальным именем и возвращает это имя.

    Ссылки на вложения Discord со временем протухают, поэтому файл храним локально.
    """
    path = Path(attachment.filename)
    file = f"{path.stem}-{uuid.uuid4().hex[:6]}{path.suffix.lower()}"
    IMG_DIR.mkdir(exist_ok=True)
    await attachment.save(IMG_DIR / file)
    save_json(UPLOADS_FILE, load_json(UPLOADS_FILE) + [file])
    log.info("Сохранена картинка %s (%d КБ)", file, attachment.size // 1024)
    return file


def remove_unused_images(in_use: set[str]) -> list[str]:
    """Удаляет из img/ картинки, загруженные через бота, которые больше не нужны ни одному правилу и расписанию.
    Файлы, положенные в img/ вручную, не трогаются: удаляются только те, что записаны в data/uploads.json."""
    uploads = load_json(UPLOADS_FILE)
    unused = [f for f in uploads if f not in in_use]
    if not unused:
        return []
    removed = []
    for file in unused:
        try:
            (IMG_DIR / file).unlink(missing_ok=True)
        except OSError as e:
            # на Windows файл бывает занят (антивирус, открыт в просмотрщике): остаётся в списке, удалится в другой раз
            log.warning("Не удалось удалить ненужную картинку %s, попробую позже: %s", file, e)
            continue
        removed.append(file)
        log.info("Удалена картинка %s: она больше не используется ни в одном правиле или расписании", file)
    save_json(UPLOADS_FILE, [f for f in uploads if f not in removed])
    return removed


async def receive_image(interaction: discord.Interaction, image: discord.Attachment | None, file: str | None) -> str | None:
    """Картинка для правила или расписания: проверяет её и, если загружена новая, сохраняет в img/.
    Возвращает имя файла в img/; None — модератору уже ответили, что не так.
    Скачивание может занять больше 3 секунд, которые Discord ждёт ответа, — поэтому сначала «бот думает…» (defer)."""
    if problem := image_problem(image, file, interaction.guild):
        await respond(interaction, problem)
        return None
    if not image:
        return file
    await interaction.response.defer(ephemeral=True)
    try:
        return await save_image(image)
    except (discord.HTTPException, OSError) as e:
        log.error("Не удалось сохранить картинку %s: %s", image.filename, e)
        await respond(interaction, "Не удалось скачать картинку, попробуйте ещё раз.")
        return None
