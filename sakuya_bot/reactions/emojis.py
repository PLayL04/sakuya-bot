"""Разбор поля emoji в /reaction add: один или несколько эмодзи, которые бот ставит на сообщение по порядку.

Без зависимости от Discord, чтобы проверять отдельно."""

import re
import unicodedata

MAX_EMOJIS = 20  # больше разных реакций на одно сообщение Discord не ставит

CUSTOM_RE = re.compile(r"<a?:\w{2,32}:(\d{15,21})>")  # эмодзи сервера: <:имя:ID> или <a:имя:ID> (анимированный)
SEPARATORS = {"​", "⁠"}  # невидимые пробелы, которые бывают между эмодзи при копировании
ZWJ = "‍"  # склейка: 👨‍👩‍👧 — один эмодзи из нескольких
KEYCAP = "⃣"  # рамка «клавиша»: 1️⃣, #️⃣, *️⃣


def _is_modifier(ch: str) -> bool:
    """Символы, которые меняют предыдущий эмодзи, а не начинают новый: вариант показа, цвет кожи, рамка, флаги регионов."""
    code = ord(ch)
    return ch in "︎️" or ch == KEYCAP or 0x1F3FB <= code <= 0x1F3FF or 0xE0020 <= code <= 0xE007F


def _is_regional(ch: str) -> bool:
    return 0x1F1E6 <= ord(ch) <= 0x1F1FF  # буквы 🇦–🇿; две подряд — флаг (🇷🇺)


def _cluster(text: str, i: int) -> int:
    """Конец эмодзи, начинающегося с позиции i."""
    if _is_regional(text[i]):
        return i + 2 if i + 1 < len(text) and _is_regional(text[i + 1]) else i + 1
    i += 1
    while i < len(text):
        if _is_modifier(text[i]):
            i += 1
        elif text[i] == ZWJ and i + 1 < len(text):
            i += 2  # склейка и следующий за ней эмодзи
        else:
            break
    return i


def _is_emoji(token: str) -> bool:
    """Похоже ли на эмодзи: есть символ-картинка (So) или рамка клавиши. Буквы, цифры и знаки — нет."""
    return KEYCAP in token or any(unicodedata.category(ch) == "So" for ch in token)


def emoji_key(emoji: str) -> str:
    """Для поиска повторов: эмодзи сервера сравниваются по ID, обычные — без невидимого знака варианта показа."""
    if m := CUSTOM_RE.fullmatch(emoji):
        return m[1]
    return emoji.replace("️", "")


def split_emojis(text: str) -> list[str]:
    """«🇰 🇴 🇹», «😀😎» или «<:cat:123…> 👍» -> список эмодзи по порядку. ValueError с текстом для ответа — если что-то не так.

    Пробелы между эмодзи не обязательны, но буквы-флажки (🇦–🇿) лучше разделять пробелом:
    две такие буквы подряд по правилам Unicode — это флаг страны (🇷 + 🇺 = 🇷🇺)."""
    emojis, i = [], 0
    while i < len(text):
        if text[i].isspace() or text[i] in SEPARATORS:
            i += 1
            continue
        if m := CUSTOM_RE.match(text, i):
            emojis.append(m[0])
            i = m.end()
            continue
        end = _cluster(text, i)
        token = text[i:end]
        if not _is_emoji(token):
            bad = text[i:].split(maxsplit=1)[0]
            raise ValueError(f"«{bad}» — это не эмодзи. Выберите эмодзи через панель эмодзи Discord.")
        emojis.append(token)
        i = end
    if not emojis:
        raise ValueError("Не указано ни одного эмодзи.")
    seen = set()
    for emoji in emojis:
        if emoji_key(emoji) in seen:
            raise ValueError(f"{emoji} указан дважды: одинаковую реакцию Discord ставит на сообщение только один раз.")
        seen.add(emoji_key(emoji))
    if len(emojis) > MAX_EMOJIS:
        raise ValueError(f"Слишком много эмодзи ({len(emojis)}): Discord ставит на сообщение не больше {MAX_EMOJIS} разных реакций.")
    return emojis


def custom_emoji_ids(emojis: list[str]) -> list[int]:
    """ID эмодзи сервера из списка — их бот должен видеть, иначе не сможет поставить."""
    return [int(m[1]) for e in emojis if (m := CUSTOM_RE.fullmatch(e))]
