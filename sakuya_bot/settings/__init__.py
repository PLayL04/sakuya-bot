"""Настройки сервера, которые меняет модерация (data/settings.toml), и текущие значения для всего бота: current()."""

import logging
from pathlib import Path

from ..config import DATA_DIR, ENV_DEFAULTS
from .model import Settings, SettingsError, complete, parse, render

SETTINGS_FILE = DATA_DIR / "settings.toml"
log = logging.getLogger("sakuya-bot")  # не через ..logs: logs сам берёт отсюда часовой пояс


class SettingsStore:
    """Файл настроек и действующие значения. Файл — документ модерации: бот его не переписывает
    (комментарии сохраняются), а только создаёт при первом запуске, заменяет целиком при /settings upload
    и дописывает ключи, появившиеся в новой версии бота."""

    def __init__(self, path: Path, initial: Settings):
        self.path = path
        self.settings = initial
        self._mtime: float | None = None

    def load(self) -> None:
        """При запуске: читает файл, а если его нет — создаёт из начальных значений (старые ключи .env)."""
        if not self.path.exists():
            self._write(render(self.settings))
            log.info("Создан файл настроек %s (значения взяты из .env или по умолчанию)", self.path)
            return
        try:
            text, added = complete(self.read_text())
        except UnicodeDecodeError:
            added = []
        if added:
            # в новой версии бота появились настройки: дописываем их в файл, чтобы модерация их видела
            self._write(text)
            log.info("В файл настроек добавлены новые настройки: %s", ", ".join(f"[{f.section}] {f.key}" for f in added))
        self._mtime = None  # при запуске файл читаем всегда (_write уже запомнил его время изменения)
        self.reload_if_changed()

    def reload_if_changed(self) -> bool:
        """Перечитывает файл, если его поменяли на диске. Файл с ошибкой не применяется: остаются прежние настройки."""
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return False
        if mtime == self._mtime:
            return False
        try:
            text = self.read_text()
        except OSError as e:
            # файл занят (редактор как раз сохраняет его) — время не запоминаем, прочитаем на следующей проверке
            log.warning("Не удалось прочитать файл настроек %s, попробую позже: %s", self.path, e)
            return False
        except UnicodeDecodeError:
            text = None
        self._mtime = mtime
        try:
            if text is None:
                raise SettingsError(["файл не в кодировке UTF-8"])
            self.settings = parse(text)
        except SettingsError as e:
            problems = e.problems
            log.error("Файл настроек %s с ошибкой, действуют прежние настройки: %s", self.path, "; ".join(problems))
            return False
        log.info("Настройки перечитаны из %s", self.path)
        return True

    def read_text(self) -> str:
        return self.path.read_text(encoding="utf-8-sig")

    def apply(self, text: str) -> Settings:
        """Проверяет и применяет новый файл (из /settings upload). При ошибке — SettingsError, ничего не меняется."""
        new = parse(text)
        self._write(text)
        self.settings = new
        return new

    def _write(self, text: str) -> None:
        # как и данные в storage.py: временный файл и подмена — файл не испортится, если бот упадёт посреди записи
        self.path.parent.mkdir(exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.path)
        self._mtime = self.path.stat().st_mtime


STORE = SettingsStore(SETTINGS_FILE, Settings(**ENV_DEFAULTS))


def current() -> Settings:
    """Действующие настройки. Читать каждый раз заново (не сохранять в переменную): их меняют на ходу."""
    return STORE.settings
