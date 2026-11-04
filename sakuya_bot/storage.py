"""Хранение данных: JSON-файлы (data/*.json) и картинки (img/)."""

import json
import uuid
from pathlib import Path

import discord

from .config import IMG_DIR, IMG_EXTENSIONS
from .logs import log


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


async def save_image(attachment: discord.Attachment) -> str:
    """Сохраняет вложение в img/ под уникальным именем и возвращает это имя.

    Ссылки на вложения Discord со временем протухают, поэтому файл храним локально.
    """
    path = Path(attachment.filename)
    file = f"{path.stem}-{uuid.uuid4().hex[:6]}{path.suffix.lower()}"
    IMG_DIR.mkdir(exist_ok=True)
    await attachment.save(IMG_DIR / file)
    log.info("Сохранена картинка %s (%d КБ)", file, attachment.size // 1024)
    return file
