"""Запас анекдотов: страница с сайта скачивается, только когда запас кончился, — так к сайту мало запросов.

Запасов несколько: один для случайных анекдотов (!анекдот) и по одному на каждый поисковый запрос
(!анекдот прапорщик гвозди). Поиск выдаёт результаты страницами по 20: сначала первая (самые подходящие),
когда она кончится — вторая и так далее, а после последней — снова с первой."""

import asyncio
import math
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urlencode

import aiohttp

from ..logs import log
from .parser import SITE, Joke, parse_jokes, parse_total

RANDOM_URL = f"{SITE}/random/anekdot/"
SEARCH_URL = f"{SITE}/search/"
# параметры поиска — как в форме на сайте: искать во всех разделах, по релевантности
SEARCH_SECTIONS = "jsotabcpeE"
# all — тексты, где есть все слова запроса: «прапорщик гвозди» — 22 текста про прапорщика с гвоздями;
# any — хотя бы одно слово (там же 1000 текстов, большинство просто про прапорщика); phrase — слова подряд
SEARCH_MODE = "all"
PER_PAGE = 20
SEARCH_MAX_LEN = 1800  # сайт сам не присылает тексты длиннее — они всё равно не влезли бы в сообщение
MAX_QUERY_CHARS = 100
MAX_QUERIES = 50  # запасов по запросам храним не больше; давно не спрошенные забываются

FETCH_TIMEOUT = 15  # секунд на скачивание страницы
RETRY_AFTER = 30  # секунд: после неудачной загрузки сайт не трогаем, а сразу отвечаем, что не вышло
REMEMBER = 500  # сколько последних выданных анекдотов помнить, чтобы не повторять их из следующих страниц
HEADERS = {"User-Agent": "sakuya-bot (Discord bot; random jokes for one server)"}


class JokesUnavailable(Exception):
    """Анекдотов нет: сайт не ответил или страница не разобралась. Текст — причина, для лога."""


class NothingFound(Exception):
    """Поиск ничего не нашёл. Сайт при этом работает."""


def normalize_query(text: str) -> str:
    """«  Прапорщик   ГВОЗДИ » -> «прапорщик гвозди»: один и тот же запрос — один запас."""
    return " ".join(text.split()).lower()[:MAX_QUERY_CHARS].strip()


def search_url(query: str, page: int = 1) -> str:
    params = [("query", query), *((f"ch[{s}]", "on") for s in SEARCH_SECTIONS),
              ("mode", SEARCH_MODE), ("xcnt", PER_PAGE), ("max_len", SEARCH_MAX_LEN), ("order", 0), ("page", page)]
    return f"{SEARCH_URL}?{urlencode(params)}"


async def fetch_page(url: str) -> str:
    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.text()


@dataclass
class Stock:
    """Запас одного источника: случайные анекдоты или один поисковый запрос."""
    jokes: deque[Joke] = field(default_factory=deque)
    page: int = 0  # последняя скачанная страница поиска
    # всё, что нашёл поиск, если оно уместилось на одной странице: дальше выдаётся по кругу без запросов к сайту
    everything: list[Joke] = field(default_factory=list)
    pages: int | None = None  # сколько всего страниц у поиска (по «всего найдено»)


class JokeCache:
    """Выдаёт анекдоты по одному из запаса. Запас кончился — скачивает новую страницу (одну, даже если
    анекдот попросили сразу несколько человек). Недавно выданные анекдоты не повторяются."""

    def __init__(self, fetch: Callable[[str], Awaitable[str]] = fetch_page, clock: Callable[[], float] = time.monotonic):
        self.fetch = fetch
        self.clock = clock
        self.random = Stock()
        self.searches: OrderedDict[str, Stock] = OrderedDict()
        self.shown: deque[str] = deque(maxlen=REMEMBER)  # ID выданных по порядку — самые старые забываются
        self.failed_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def jokes(self) -> deque[Joke]:
        """Запас случайных анекдотов."""
        return self.random.jokes

    def stock(self, query: str | None) -> Stock:
        if not query:
            return self.random
        if query not in self.searches:
            self.searches[query] = Stock()
            while len(self.searches) > MAX_QUERIES:
                self.searches.popitem(last=False)
        self.searches.move_to_end(query)
        return self.searches[query]

    async def next(self, query: str | None = None) -> Joke:
        """Следующий анекдот: случайный или по запросу (normalize_query). JokesUnavailable — запас пуст, а новые
        скачать не вышло; NothingFound — по запросу ничего нет."""
        async with self._lock:  # пока одна команда качает страницу, остальные ждут её, а не качают свою
            stock = self.stock(query)
            if not stock.jokes:
                await (self.refill_search(stock, query) if query else self.refill_random())
            joke = stock.jokes.popleft()
            self.shown.append(joke.id)
            return joke

    async def load(self, url: str) -> str:
        if self.failed_at is not None and self.clock() - self.failed_at < RETRY_AFTER:
            raise JokesUnavailable(f"сайт недавно не ответил, следующая попытка не раньше чем через {RETRY_AFTER} с")
        try:
            html = await self.fetch(url)
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeDecodeError) as e:
            self.failed_at = self.clock()
            raise JokesUnavailable(f"не удалось скачать {url}: {e!r}") from e
        self.failed_at = None
        return html

    def add(self, stock: Stock, jokes: list[Joke]) -> int:
        fresh = [j for j in jokes if j.id not in self.shown]
        stock.jokes.extend(fresh or jokes)  # все уже были — лучше повтор, чем отказ
        return len(fresh)

    async def refill_random(self) -> None:
        jokes = parse_jokes(await self.load(RANDOM_URL))
        if not jokes:
            # сайт ответил, но анекдотов на странице нет — скорее всего, поменялась разметка
            self.failed_at = self.clock()
            raise JokesUnavailable(f"на {RANDOM_URL} не нашлось ни одного анекдота (поменялась разметка сайта?)")
        fresh = self.add(self.random, jokes)
        log.info("Анекдоты: скачано %d с %s, новых %d", len(jokes), RANDOM_URL, fresh)

    async def refill_search(self, stock: Stock, query: str) -> None:
        if stock.everything:
            # поиск нашёл мало (одна страница) и она уже скачана: та же страница с сайта ничего нового не даст
            self.add(stock, stock.everything)
            return
        page = stock.page + 1
        if stock.pages is not None and page > stock.pages:
            page = 1  # результаты кончились — по кругу (повторы отсеются, пока помним выданные)
        html = await self.load(search_url(query, page))
        jokes = parse_jokes(html)
        if not jokes and page > 1:
            # страница за последней (число найденного на сайте могло поменяться) — снова с первой
            page = 1
            html = await self.load(search_url(query, page))
            jokes = parse_jokes(html)
        if not jokes:
            raise NothingFound(query)
        if (total := parse_total(html)) is not None:
            stock.pages = max(1, math.ceil(total / PER_PAGE))
        stock.page = page
        if stock.pages == 1:
            stock.everything = jokes
        fresh = self.add(stock, jokes)
        log.info("Анекдоты по запросу «%s»: страница %d из %s, скачано %d, новых %d",
                 query, page, stock.pages or "?", len(jokes), fresh)
