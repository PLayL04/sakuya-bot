"""Хранение данных в JSON-файлах (data/*.json)."""

import json
from pathlib import Path


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
