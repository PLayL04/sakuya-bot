"""Разбор страницы случайных анекдотов anekdot.ru и оформление анекдота в сообщение Discord. Без Discord и сети.

На странице несколько анекдотов, каждый — блок <div class="topicbox" data-id="…"> с текстом в <div class="text">,
строки текста разделены <br>. Блоки без data-id (заголовок страницы) пропускаются. Так же устроена страница поиска —
там найденные слова ещё подсвечены <span>, их разбор просто пропускает, оставляя текст."""

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from ..quotes.picker import escape

SITE = "https://www.anekdot.ru"
MAX_JOKE_CHARS = 1800  # длиннее — не влезет в сообщение Discord вместе с подписью и экранированием


@dataclass(frozen=True)
class Joke:
    id: str
    text: str

    @property
    def url(self) -> str:
        return f"{SITE}/id/{self.id}/"


class _JokePage(HTMLParser):
    def __init__(self):
        super().__init__()  # convert_charrefs: &quot; и прочее сразу превращаются в символы
        self.jokes: list[Joke] = []
        self._id: str | None = None  # data-id блока анекдота, внутри которого мы сейчас
        self._depth = 0  # вложенность <div> внутри текста анекдота; 0 — не внутри текста
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if self._depth:
            if tag == "br":
                self._parts.append("\n")
            elif tag == "div":
                self._depth += 1
        elif tag == "div" and "topicbox" in classes:
            self._id = a.get("data-id")
        elif tag == "div" and "text" in classes and self._id:
            self._depth, self._parts = 1, []

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)  # <br/>

    def handle_endtag(self, tag):
        if self._depth and tag == "div":
            self._depth -= 1
            if not self._depth:
                text = tidy("".join(self._parts))
                if text:
                    self.jokes.append(Joke(self._id, text))
                self._id = None

    def handle_data(self, data):
        if self._depth:
            self._parts.append(data)


# реплика: дефис или тире в начале строки (с пробелом или без)
DASH_START = re.compile(r"^[-–—]\s*")
# новая реплика, приклеенная к концу предыдущей фразы: «прапорщику:- Товарищ», «строит.- Ну-ка»
GLUED_REPLY = re.compile(r"([.!?…:])\s*[-–—]\s*(?=[«\"„(]?[А-ЯЁA-Z0-9])")
# дефис, окружённый пробелами, — это тире: «Ну что я - даун?»
SPACED_HYPHEN = re.compile(r"(?<=\s)[-–](?=\s)")


def _starts_line(line: str) -> bool:
    """Начинает ли строка новую фразу или реплику (тогда перенос перед ней — настоящий): с заглавной буквы или цифры,
    в том числе после тире реплики. Строка со строчной буквы — продолжение предложения с прошлой строки."""
    first = DASH_START.sub("", line).lstrip("«\"„'(")[:1]
    return first.isupper() or first.isdigit()


def tidy(raw: str) -> str:
    """Чистит текст анекдота для Discord. На сайте много старых текстов, свёрстанных с переносами посреди предложений
    («в казарме<br>трахают табуретку») и без переносов перед репликами («прапорщику:- Товарищ»). Поэтому:
    переносы посреди предложения склеиваются, каждая реплика начинается с новой строки, а дефис реплики меняется
    на тире «—» — строку с «- » в начале Discord показал бы пунктом списка."""
    lines = [" ".join(line.split()) for line in raw.split("\n")]
    lines = [line for line in lines if line]
    merged: list[str] = []
    for line in lines:
        if merged and not _starts_line(line):
            merged[-1] += " " + line
        else:
            merged.append(line)
    text = GLUED_REPLY.sub(r"\1\n— ", "\n".join(merged))
    text = "\n".join(DASH_START.sub("— ", line) for line in text.split("\n"))
    return SPACED_HYPHEN.sub("—", text).strip()


def parse_jokes(html: str) -> list[Joke]:
    """Анекдоты со страницы по порядку; слишком длинные для одного сообщения пропускаются."""
    page = _JokePage()
    page.feed(html)
    page.close()
    return [j for j in page.jokes if len(j.text) <= MAX_JOKE_CHARS]


TOTAL_RE = re.compile(r"всего найдено:\s*(\d+)")


def parse_total(html: str) -> int | None:
    """Сколько всего нашёл поиск (строка «всего найдено: N» на странице поиска); None — строки нет."""
    m = TOTAL_RE.search(html)
    return int(m[1]) if m else None


def format_joke(joke: Joke) -> str:
    """Текст анекдота (разметка Discord в нём показывается как есть) и мелкая подпись-ссылка на источник.
    Ссылка в <…>, чтобы Discord не разворачивал под анекдотом превью сайта."""
    # escape — построчно: разметку строки («1. », «# ») Discord ищет в начале каждой строки, а не только первой
    text = "\n".join(escape(line) for line in joke.text.split("\n"))
    return f"{text}\n-# [anekdot.ru](<{joke.url}>)"
