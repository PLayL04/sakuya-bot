"""Запас анекдотов: страница с сайта скачивается, только когда запас кончился, — так к сайту мало запросов."""

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable

import aiohttp

from ..logs import log
from .parser import SITE, Joke, parse_jokes

RANDOM_URL = f"{SITE}/random/anekdot/"
FETCH_TIMEOUT = 15  # секунд на скачивание страницы
RETRY_AFTER = 30  # секунд: после неудачной загрузки сайт не трогаем, а сразу отвечаем, что не вышло
REMEMBER = 500  # сколько последних выданных анекдотов помнить, чтобы не повторять их из следующих страниц
HEADERS = {"User-Agent": "sakuya-bot (Discord bot; random jokes for one server)"}


class JokesUnavailable(Exception):
    """Анекдотов нет: сайт не ответил или страница не разобралась. Текст — причина, для лога."""


async def fetch_page(url: str = RANDOM_URL) -> str:
    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.text()


class JokeCache:
    """Выдаёт анекдоты по одному из запаса. Запас кончился — скачивает новую страницу (одну, даже если
    анекдот попросили сразу несколько человек). Недавно выданные анекдоты не повторяются."""

    def __init__(self, fetch: Callable[[], Awaitable[str]] = fetch_page, clock: Callable[[], float] = time.monotonic):
        self.fetch = fetch
        self.clock = clock
        self.jokes: deque[Joke] = deque()
        self.shown: deque[str] = deque(maxlen=REMEMBER)  # ID выданных по порядку — самые старые забываются
        self.failed_at: float | None = None
        self._lock = asyncio.Lock()

    async def next(self) -> Joke:
        """Следующий анекдот. JokesUnavailable — если запас пуст, а новые скачать не вышло."""
        async with self._lock:  # пока одна команда качает страницу, остальные ждут её, а не качают свою
            if not self.jokes:
                await self.refill()
            joke = self.jokes.popleft()
            self.shown.append(joke.id)
            return joke

    async def refill(self) -> None:
        if self.failed_at is not None and self.clock() - self.failed_at < RETRY_AFTER:
            raise JokesUnavailable(f"сайт недавно не ответил, следующая попытка не раньше чем через {RETRY_AFTER} с")
        try:
            jokes = parse_jokes(await self.fetch())
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeDecodeError) as e:
            self.failed_at = self.clock()
            raise JokesUnavailable(f"не удалось скачать {RANDOM_URL}: {e!r}") from e
        if not jokes:
            # сайт ответил, но анекдотов на странице нет — скорее всего, поменялась разметка
            self.failed_at = self.clock()
            raise JokesUnavailable(f"на {RANDOM_URL} не нашлось ни одного анекдота (поменялась разметка сайта?)")
        self.failed_at = None
        fresh = [j for j in jokes if j.id not in self.shown]
        self.jokes.extend(fresh or jokes)  # все уже были — лучше повтор, чем отказ
        log.info("Анекдоты: скачано %d с %s, новых %d", len(jokes), RANDOM_URL, len(fresh))
