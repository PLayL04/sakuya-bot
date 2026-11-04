"""Разбор поля emoji в /reaction: один или несколько эмодзи по порядку (reactions/emojis.py)."""

import pytest

from sakuya_bot.reactions.emojis import MAX_EMOJIS, custom_emoji_ids, split_emojis

CAT = "<:cat:123456789012345678>"
DANCE = "<a:dance:223456789012345678>"


@pytest.mark.parametrize("text, expected", [
    ("🐱", ["🐱"]),
    ("1️⃣", ["1️⃣"]),                                # цифра в рамке — раньше отклонялась
    ("#️⃣ *️⃣ 🔟", ["#️⃣", "*️⃣", "🔟"]),
    ("🇰 🇴 🇹", ["🇰", "🇴", "🇹"]),                   # буквы через пробел — комбинация
    ("🇷🇺", ["🇷🇺"]),                                # две буквы подряд — флаг
    ("😀😎👍🏽", ["😀", "😎", "👍🏽"]),                 # без пробелов, с цветом кожи
    ("👨‍👩‍👧 ❤️", ["👨‍👩‍👧", "❤️"]),                     # склеенный эмодзи
    ("🏳️‍🌈", ["🏳️‍🌈"]),
    ("©️ ™️", ["©️", "™️"]),
    ("🇰​🇴", ["🇰", "🇴"]),                      # невидимый пробел при копировании
    (f"{CAT} {DANCE}👍", [CAT, DANCE, "👍"]),        # эмодзи сервера
])
def test_split(text, expected):
    assert split_emojis(text) == expected


@pytest.mark.parametrize("text, message", [
    ("кот", "«кот» — это не эмодзи"),
    (":smile:", "«:smile:» — это не эмодзи"),
    ("🐱 abc", "«abc»"),
    ("1", "не эмодзи"),
    ("🐱 🐱", "дважды"),
    ("❤ ❤️", "дважды"),                               # тот же эмодзи, с вариантом показа и без
    (f"{CAT} <a:other:123456789012345678>", "дважды"),  # тот же ID — тот же эмодзи
    ("   ", "ни одного"),
    (" ".join(chr(0x1F600 + i) for i in range(MAX_EMOJIS + 1)), "Слишком много"),
])
def test_rejects(text, message):
    with pytest.raises(ValueError, match=message):
        split_emojis(text)


def test_max_emojis_allowed():
    assert len(split_emojis(" ".join(chr(0x1F600 + i) for i in range(MAX_EMOJIS)))) == MAX_EMOJIS


def test_custom_emoji_ids():
    assert custom_emoji_ids([CAT, "👍", DANCE]) == [123456789012345678, 223456789012345678]
