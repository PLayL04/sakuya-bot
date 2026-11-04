"""Разбор страницы случайных анекдотов anekdot.ru и оформление анекдота в сообщение Discord. Без Discord и сети.

На странице несколько анекдотов, каждый — блок <div class="topicbox" data-id="…"> с текстом в <div class="text">,
строки текста разделены <br>. Блоки без data-id (заголовок страницы) пропускаются."""

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
                lines = [" ".join(line.split()) for line in "".join(self._parts).split("\n")]
                text = "\n".join(lines).strip()
                if text:
                    self.jokes.append(Joke(self._id, text))
                self._id = None

    def handle_data(self, data):
        if self._depth:
            self._parts.append(data)


def parse_jokes(html: str) -> list[Joke]:
    """Анекдоты со страницы по порядку; слишком длинные для одного сообщения пропускаются."""
    page = _JokePage()
    page.feed(html)
    page.close()
    return [j for j in page.jokes if len(j.text) <= MAX_JOKE_CHARS]


def format_joke(joke: Joke) -> str:
    """Текст анекдота (разметка Discord в нём показывается как есть) и мелкая подпись-ссылка на источник.
    Ссылка в <…>, чтобы Discord не разворачивал под анекдотом превью сайта."""
    return f"{escape(joke.text)}\n-# [anekdot.ru](<{joke.url}>)"
