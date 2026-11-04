"""Настройки сервера, которые меняет модерация: файл data/settings.toml. Чтение, проверка, шаблон файла. Без Discord."""

import tomllib
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class Settings:
    admin_roles: frozenset[int] = frozenset()
    fun_channels: frozenset[int] = frozenset()
    fun_cooldown: int = 15
    image_reply_cooldown: int = 60
    ping_emoji: str = "MadelineAlo"
    timezone: str = "Europe/Moscow"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)  # ZoneInfo сам кеширует пояса, создавать заново дёшево


@dataclass(frozen=True)
class Field:
    section: str
    key: str
    attr: str  # поле Settings
    kind: str  # roles | channels | seconds | name | timezone
    title: str  # для /settings show и списка изменений
    comment: str  # комментарий над строкой в файле
    max: int = 0  # для seconds


FIELDS = [
    Field("access", "admin_roles", "admin_roles", "roles", "Роли управления ботом",
          "Роли, участники с которыми управляют ботом (/schedule, /reaction, /settings): список ID через запятую.\n"
          "Участники с правом «Управлять сервером» управляют ботом всегда — даже если список пуст или роль удалили."),
    Field("fun", "channels", "fun_channels", "channels", "Каналы развлекательных команд",
          "Каналы, где работают !кофе, !пиво, !explode, !implode, !музей, !цитата: список ID через запятую.\n"
          "Пустой список [] — во всех каналах. Ветки внутри разрешённого канала тоже подходят.\n"
          "В остальных каналах эти команды молча игнорируются."),
    Field("fun", "cooldown", "fun_cooldown", "seconds", "Кулдаун развлекательных команд",
          "Как часто один участник может вызывать каждую из этих команд, в секундах. 0 — без ограничения.", max=3600),
    Field("reactions", "image_cooldown", "image_reply_cooldown", "seconds", "Кулдаун ответов картинкой",
          "Одно правило /reaction отвечает картинкой в одном канале не чаще, чем раз в столько секунд. 0 — без ограничения.",
          max=86400),
    Field("reactions", "ping_emoji", "ping_emoji", "name", "Эмодзи на пинг бота",
          "Эмодзи сервера (имя без двоеточий), которым бот отвечает на упоминание. Если такого нет — ставится 💩."),
    Field("schedule", "timezone", "timezone", "timezone", "Часовой пояс расписаний",
          "Часовой пояс, в котором указывается время в /schedule (и время в логах бота).\n"
          "Например \"Europe/Moscow\", \"Europe/Kaliningrad\", \"Asia/Yekaterinburg\", \"Asia/Novosibirsk\".\n"
          "Уже созданные расписания после смены пояса срабатывают в то же время, но по новому поясу."),
]

HEADER = """\
# Настройки sakuya-bot для этого сервера.
#
# Как поменять: скачайте файл командой /settings download, исправьте в любом текстовом редакторе
# и загрузите обратно командой /settings upload. Бот проверит файл: если в нём ошибка, ничего не изменится,
# а бот напишет, в какой строке ошибка. Посмотреть текущие настройки — /settings show.
#
# Строки, начинающиеся с #, — комментарии, бот их не читает (их можно дописывать, например, чья это роль).
# ID роли или канала: включите режим разработчика (Настройки Discord → Расширенные), затем ПКМ по роли
# или каналу → «Копировать ID». Текст пишется в кавычках, числа и списки [1, 2] — без.
"""


class SettingsError(ValueError):
    """Файл настроек не принят. problems — что именно не так, по-русски, для ответа модератору."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def parse(text: str) -> Settings:
    """Текст settings.toml -> Settings. Ключи, которых нет в файле, получают значения по умолчанию.
    Неизвестные разделы и ключи — ошибка: так сразу видна опечатка, а не молча действующее значение по умолчанию."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise SettingsError([f"Файл не читается как TOML: {e}. Проверьте кавычки, запятые и скобки в этой строке."]) from e
    problems: list[str] = []
    known = {(f.section, f.key): f for f in FIELDS}
    sections = {f.section for f in FIELDS}
    values = {}
    for section, table in data.items():
        if section not in sections:
            problems.append(f"Неизвестный раздел [{section}]. Есть разделы: {', '.join(f'[{s}]' for s in dict.fromkeys(f.section for f in FIELDS))}.")
            continue
        if not isinstance(table, dict):
            problems.append(f"«{section}» должен быть разделом [{section}], а не значением.")
            continue
        for key, value in table.items():
            field = known.get((section, key))
            if field is None:
                options = ", ".join(f.key for f in FIELDS if f.section == section)
                problems.append(f"Неизвестный ключ «{key}» в разделе [{section}]. В нём есть: {options}.")
                continue
            try:
                values[field.attr] = _convert(field, value)
            except ValueError as e:
                problems.append(f"[{section}] {key}: {e}")
    if problems:
        raise SettingsError(problems)
    return replace(Settings(), **values)


def _convert(field: Field, value):
    if field.kind in ("roles", "channels"):
        if not isinstance(value, list):
            raise ValueError("нужен список ID в квадратных скобках, например [123456789012345678] или []")
        ids = set()
        for item in value:
            # ID можно написать и числом, и строкой в кавычках — оба варианта встречаются при копировании
            if isinstance(item, bool) or not isinstance(item, (int, str)) or not str(item).strip().isdigit():
                raise ValueError(f"«{item}» не похоже на ID — это должно быть число из 17–20 цифр")
            ids.add(int(str(item).strip()))
        return frozenset(ids)
    if field.kind == "seconds":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= field.max:
            raise ValueError(f"нужно целое число секунд от 0 до {field.max}, а указано {value!r}")
        return value
    if not isinstance(value, str):
        raise ValueError(f"нужен текст в кавычках, а указано {value!r}")
    if field.kind == "name":
        name = value.strip().strip(":")
        if not name:
            raise ValueError("имя не может быть пустым")
        return name
    if field.kind == "timezone":
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"неизвестный часовой пояс «{value}» (пример: \"Europe/Moscow\")") from e
        return value
    raise AssertionError(field.kind)


def _toml_value(value) -> str:
    if isinstance(value, frozenset):
        return "[" + ", ".join(str(v) for v in sorted(value)) + "]"
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return str(value)


def render(settings: Settings) -> str:
    """Файл настроек с комментариями к каждому ключу: так он создаётся при первом запуске."""
    lines = [HEADER]
    section = None
    for f in FIELDS:
        if f.section != section:
            section = f.section
            lines.append(f"\n[{section}]")
        lines += [f"# {line}" for line in f.comment.split("\n")]
        lines.append(f"{f.key} = {_toml_value(getattr(settings, f.attr))}")
    return "\n".join(lines) + "\n"


def describe_value(field: Field, value, mention: Callable[[str, int], str] = lambda kind, i: str(i)) -> str:
    """Значение для людей: роли и каналы — через mention (в Discord это <@&id> и <#id>), секунды — с «с»."""
    if field.kind in ("roles", "channels"):
        if not value:
            return "во всех каналах" if field.kind == "channels" else "нет (только «Управлять сервером»)"
        return ", ".join(mention(field.kind, i) for i in sorted(value))
    if field.kind == "seconds":
        return f"{value} с" if value else "без ограничения"
    return str(value)


def changes(old: Settings, new: Settings) -> list[Field]:
    """Какие настройки отличаются."""
    return [f for f in FIELDS if getattr(old, f.attr) != getattr(new, f.attr)]


assert {f.attr for f in FIELDS} == {f.name for f in fields(Settings)}, "у каждой настройки должна быть строка в FIELDS"
