"""Когда отвечать рифмой: короткая реплика, которая кончается на «да» или «нет». Без Discord — легко проверять отдельно.

Смысл слова тут не важен — рифма работает на последнем слове короткого ответа: «да», «ну да», «точно нет», «да?».
А «да, давай завтра» или «кот да пёс» не подходят: слово не последнее, рифмовать не к чему."""

import re

WORDS = {"да": "yes", "нет": "no"}
MAX_WORDS = 3  # длиннее — это уже не короткий ответ, а рассказ, где «да» случайно оказалось в конце

# разметка Discord, которая не считается словами: упоминания <@123>, <@&123>, <#123>, эмодзи сервера <:имя:123>, ссылки
DISCORD_MARKUP = re.compile(r"<(?:@[!&]?|#|a?:\w+:)\d+>|https?://\S+")
WORD = re.compile(r"[^\W_]+")  # буквы и цифры: знаки препинания, смайлы «)))» и эмодзи отбрасываются
REPEATS = re.compile(r"(\w)\1+")  # «даааа» -> «да», «неееет» -> «нет»


def rhyme_kind(text: str) -> str | None:
    """«yes» — сообщение кончается на «да», «no» — на «нет», None — не повод для рифмы."""
    # цитаты («> да») — чужие слова, их не считаем
    lines = [line for line in text.splitlines() if not line.lstrip().startswith(">")]
    text = DISCORD_MARKUP.sub(" ", "\n".join(lines)).lower().replace("ё", "е")
    if text.lstrip().startswith("!"):
        return None  # команда бота, а не реплика
    words = WORD.findall(text)
    if not words or len(words) > MAX_WORDS:
        return None
    return WORDS.get(REPEATS.sub(r"\1", words[-1]))
