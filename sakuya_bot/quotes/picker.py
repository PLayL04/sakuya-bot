"""Выбор случайной цитаты из текстовых файлов и оформление её в сообщение Discord. Без зависимостей от Discord."""

import random
import re
from dataclasses import dataclass
from pathlib import Path

MESSAGE_LIMIT = 2000  # лимит длины сообщения в Discord
MAX_QUOTE_CHARS = 1600  # сам текст цитаты; остальное — на разметку, экранирование и подпись

# сколько символов набирать: (вес режима, от, до). Короткие цитаты — обычно одно предложение,
# средние — несколько предложений, длинные — абзац целиком или несколько абзацев
LENGTHS = {
    "short": (0.4, 40, 200),
    "medium": (0.4, 200, 600),
    "long": (0.2, 600, MAX_QUOTE_CHARS),
}
MIN_QUOTE_CHARS = 25  # короче — не цитата («Да.», «Глава 2»)
ATTEMPTS = 40

# сокращения, после точки в которых предложение не кончается («т. е.», «г. Москва», «Dr. Smith»)
ABBREVIATIONS = {"т", "е", "д", "п", "г", "гг", "в", "вв", "им", "ул", "пр", "проф", "акад", "др", "см", "ср",
                 "напр", "стр", "рис", "тыс", "млн", "млрд", "руб", "коп", "св", "mr", "mrs", "ms", "dr", "st",
                 "vs", "etc", "jr", "sr", "prof", "no", "vol", "p", "pp"}
# конец предложения: . ! ? … (и их сочетания), за ними могут идти закрывающие кавычки и скобки, потом пробел
SENTENCE_END = re.compile(r"[.!?…]+[»”\"'’)\]]*(?=\s)")
# предложение начинается с заглавной буквы или цифры, перед ними могут быть открывающие кавычки, тире и скобки
SENTENCE_START = re.compile(r"[«“„\"'(\[—–\-\s]*[A-ZА-ЯЁ0-9]")
# символы разметки Discord, которые в тексте цитаты нужно показать как есть
MARKDOWN_CHARS = re.compile(r"([\\*_~`|>\[\]])")
# начало строки, которое Discord принял бы за заголовок, подпись или список
LINE_MARKUP = re.compile(r"^(#|-#|[-+*] |\d+[.)] )")


@dataclass
class Quote:
    paragraphs: list[list[str]]  # абзацы цитаты, каждый — список предложений
    source: str                  # подпись: автор и/или название

    @property
    def text(self) -> str:
        return "\n".join(" ".join(p) for p in self.paragraphs)


@dataclass
class QuoteSource:
    name: str
    paragraphs: list[list[str]]  # абзацы текста, каждый — список предложений

    @property
    def size(self) -> int:
        return sum(len(s) for p in self.paragraphs for s in p)


def split_sentences(paragraph: str) -> list[str]:
    """Делит абзац на предложения по . ! ? …, не разрезая инициалы и сокращения («А. С. Пушкин», «т. е.»)."""
    sentences, start = [], 0
    for m in SENTENCE_END.finditer(paragraph):
        rest = paragraph[m.end():]
        if not SENTENCE_START.match(rest):
            continue  # дальше строчная буква — это не конец предложения («— сказал он», «и т. д. и т. п.»)
        if m.group().startswith(".") and len(m.group().rstrip("»”\"'’)]")) == 1:
            word = re.search(r"(\w+)$", paragraph[start:m.start()])
            if word and (word.group(1).lower() in ABBREVIATIONS or (len(word.group(1)) == 1 and word.group(1).isupper())):
                continue  # инициал или сокращение
        sentences.append(paragraph[start:m.end()].strip())
        start = m.end()
    tail = paragraph[start:].strip()
    if tail:
        sentences.append(tail)
    return [s for s in sentences if s]


def is_heading(paragraph: str) -> bool:
    """Заголовки, номера глав и прочие строки без знака препинания в конце в цитаты не берём."""
    paragraph = paragraph.rstrip("»”\"'’)] ")
    return len(paragraph) < 80 and not paragraph.endswith((".", "!", "?", "…", ":", ";", ","))


def parse_text(text: str) -> tuple[str | None, list[list[str]]]:
    """Текст файла -> (подпись из первой строки «# ...» или None, абзацы по предложениям).

    Абзацы разделяются пустой строкой; переносы строк внутри абзаца склеиваются, кроме строк, начинающихся
    с тире (реплики диалога). Если пустых строк в тексте нет вообще, абзацем считается каждая строка.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    lines = text.split("\n")
    title = None
    if lines and lines[0].startswith("# "):
        title = lines.pop(0)[2:].strip() or None
    body = "\n".join(lines).strip()
    if re.search(r"\n[ \t]*\n", body):
        # перенос перед тире — новая реплика; остальные переносы склеиваются ниже вместе с лишними пробелами
        raw = [p for block in re.split(r"\n[ \t]*\n", body) for p in re.split(r"\n(?=[ \t]*[—–-] )", block)]
    else:
        raw = body.split("\n")
    paragraphs = []
    for p in raw:
        p = re.sub(r"\s+", " ", p).strip()
        if p and not is_heading(p):
            paragraphs.append(split_sentences(p))
    return title, paragraphs


def read_text(path: Path) -> str:
    """UTF-8 (с BOM или без), а если не читается — cp1251, как сохраняет Блокнот в старых Windows."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1251")


def load_source(path: Path) -> QuoteSource:
    title, paragraphs = parse_text(read_text(path))
    return QuoteSource(name=title or path.stem, paragraphs=paragraphs)


class QuoteLibrary:
    """Все *.txt из папки. Изменённые и новые файлы перечитываются при следующем выборе — без перезапуска бота."""

    def __init__(self, folder: Path):
        self.folder = folder
        self._cache: dict[Path, tuple[float, QuoteSource]] = {}

    def sources(self) -> list[QuoteSource]:
        paths = sorted(self.folder.glob("*.txt")) if self.folder.is_dir() else []
        fresh = {}
        for path in paths:
            mtime = path.stat().st_mtime
            cached = self._cache.get(path)
            fresh[path] = cached if cached and cached[0] == mtime else (mtime, load_source(path))
        self._cache = fresh
        return [source for _, source in fresh.values() if source.paragraphs]

    def pick(self, rng: random.Random | None = None) -> Quote | None:
        sources = self.sources()
        if not sources:
            return None
        rng = rng or random.Random()
        # файл выбирается пропорционально размеру: каждый кусок текста выпадает одинаково часто
        source = rng.choices(sources, weights=[s.size for s in sources])[0]
        return pick_quote(source, rng)


def pick_quote(source: QuoteSource, rng: random.Random, mode: str | None = None) -> Quote | None:
    """Случайный отрывок из целых предложений. Длина случайная: от одного предложения до нескольких абзацев."""
    if mode is None:
        mode = rng.choices(list(LENGTHS), weights=[w for w, _, _ in LENGTHS.values()])[0]
    _, low, high = LENGTHS[mode]
    target = rng.randint(low, high)
    best: list[list[str]] | None = None
    for _ in range(ATTEMPTS):
        p = rng.randrange(len(source.paragraphs))
        # длинная цитата начинается с начала абзаца, короткие — с любого предложения
        s = 0 if mode == "long" else rng.randrange(len(source.paragraphs[p]))
        first = source.paragraphs[p][s]
        if len(first) > MAX_QUOTE_CHARS or not SENTENCE_START.match(first):
            continue
        chosen = collect(source.paragraphs, p, s, target, cross_paragraphs=mode != "short")
        size = quote_len(chosen)
        if size < MIN_QUOTE_CHARS:
            continue
        if low <= size <= high:
            return Quote(chosen, source.name)
        if best is None or abs(size - target) < abs(quote_len(best) - target):
            best = chosen
    return Quote(best, source.name) if best else None


def collect(paragraphs: list[list[str]], p: int, s: int, target: int, cross_paragraphs: bool) -> list[list[str]]:
    """Набирает предложения, начиная с paragraphs[p][s], пока не наберётся target символов (но не больше MAX_QUOTE_CHARS)."""
    chosen = [[paragraphs[p][s]]]
    size = len(paragraphs[p][s])
    while size < target:
        s += 1
        if s >= len(paragraphs[p]):
            if not cross_paragraphs or p + 1 >= len(paragraphs):
                break
            p, s = p + 1, 0
            chosen.append([])
        sentence = paragraphs[p][s]
        if size + len(sentence) + 1 > MAX_QUOTE_CHARS:
            break
        # переход за середину цели — берём предложение, если с ним ближе к цели, чем без него
        if size + len(sentence) > target and size + len(sentence) - target > target - size and size >= MIN_QUOTE_CHARS:
            break
        chosen[-1].append(sentence)
        size += len(sentence) + 1
    return [p for p in chosen if p]


def quote_len(paragraphs: list[list[str]]) -> int:
    return sum(len(s) + 1 for p in paragraphs for s in p)


def escape(text: str) -> str:
    """Чтобы *, _, ` и прочее из текста показывались как есть, а не превращались в разметку."""
    text = MARKDOWN_CHARS.sub(r"\\\1", text)
    return LINE_MARKUP.sub(r"\\\1", text)


def format_quote(quote: Quote) -> str:
    """Цитата блоком «>» (каждый абзац — отдельной строкой) и подпись курсивом мелким шрифтом под ней.

    Если сообщение не влезает в 2000 символов (из-за экранирования), с конца убираются целые предложения.
    """
    paragraphs = [list(p) for p in quote.paragraphs]
    while True:
        lines = [f"> {escape(' '.join(p))}" for p in paragraphs]
        # одно короткое предложение — жирным: так оно читается как афоризм
        if len(paragraphs) == 1 and len(paragraphs[0]) == 1 and len(paragraphs[0][0]) <= 200:
            lines = [f"> **{escape(paragraphs[0][0])}**"]
        message = "\n".join(lines) + f"\n-# — *{escape(quote.source)}*"
        if len(message) <= MESSAGE_LIMIT or sum(map(len, paragraphs)) <= 1:
            return message[:MESSAGE_LIMIT]
        paragraphs[-1].pop()
        if not paragraphs[-1]:
            paragraphs.pop()
