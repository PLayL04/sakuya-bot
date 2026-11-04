"""!анекдот: разбор страницы anekdot.ru, запас анекдотов и повторная загрузка, ошибки сайта (jokes/). Без сети:
страница собирается в тесте по образцу разметки сайта."""

import asyncio
import contextlib
from unittest import mock

import aiohttp
import pytest

from sakuya_bot.jokes import cache as jokes_cache
from sakuya_bot.jokes.cache import JokeCache, JokesUnavailable
from sakuya_bot.jokes.cog import UNAVAILABLE_TEXT, JokeCog
from sakuya_bot.jokes.parser import MAX_JOKE_CHARS, Joke, format_joke, parse_jokes


def topic(joke_id, text_html):
    return (f'<div class="topicbox" id="1" data-id="{joke_id}" data-t="j"><p class="title"><a href="#">01.01.2020</a></p>'
            f'<div class="text">{text_html}</div><div class="votingbox"><div class="num">1</div>'
            f'<div class="rates" data-id="{joke_id}"></div></div></div>')


def page(*topics):
    # первый topicbox — заголовок страницы, без data-id: это не анекдот
    return '<html><body><div class="topicbox"><div><h1>Случайные анекдоты</h1></div></div>' + "".join(topics) + "</body></html>"


SAMPLE = page(
    topic(101, "Первая строка.<br>- Реплика &quot;в кавычках&quot;,   с   пробелами.<br/> - Ответ."),
    topic(-202, "Короткий анекдот про *звёздочки* и @everyone."),
    topic(303, "   "),  # пустой — пропускается
    topic(404, "х" * (MAX_JOKE_CHARS + 1)),  # слишком длинный для сообщения
)


# ---------- разбор страницы ----------

def test_parse_jokes():
    jokes = parse_jokes(SAMPLE)
    assert [j.id for j in jokes] == ["101", "-202"]
    assert jokes[0].text == 'Первая строка.\n- Реплика "в кавычках", с пробелами.\n- Ответ.'


def test_page_without_jokes():
    assert parse_jokes("<html><body>Сайт на обслуживании</body></html>") == []


def test_format_joke_escapes_markup_and_links_source():
    text = format_joke(Joke("-202", "Про *звёздочки* и _подчёркивания_"))
    assert text.startswith(r"Про \*звёздочки\* и \_подчёркивания\_")
    assert text.endswith("-# [anekdot.ru](<https://www.anekdot.ru/id/-202/>)")  # <…> — без превью сайта


# ---------- запас ----------

def fake_site(*pages):
    """Сайт, который по очереди отдаёт страницы (или выбрасывает исключение); await_count — сколько раз к нему ходили."""
    return mock.AsyncMock(side_effect=list(pages))


def jokes_page(*ids):
    return page(*(topic(i, f"Анекдот номер {i}.") for i in ids))


async def test_page_is_fetched_once_until_jokes_run_out():
    fetch = fake_site(jokes_page(1, 2, 3), jokes_page(4, 5))
    cache = JokeCache(fetch)
    assert [(await cache.next()).id for _ in range(3)] == ["1", "2", "3"]
    assert fetch.await_count == 1  # три анекдота — один запрос к сайту
    assert (await cache.next()).id == "4"  # запас кончился — новая страница
    assert fetch.await_count == 2


async def test_shown_jokes_are_not_repeated():
    cache = JokeCache(fake_site(jokes_page(1, 2), jokes_page(2, 3), jokes_page(1, 2)))
    assert [(await cache.next()).id for _ in range(3)] == ["1", "2", "3"]  # «2» со второй страницы уже был
    assert (await cache.next()).id in {"1", "2"}  # все уже были — лучше повтор, чем отказ


async def test_simultaneous_requests_fetch_one_page():
    started = asyncio.Event()

    async def slow_fetch():
        started.set()
        await asyncio.sleep(0.05)
        return jokes_page(1, 2, 3)
    fetch = mock.AsyncMock(side_effect=slow_fetch)
    cache = JokeCache(fetch)
    jokes = await asyncio.gather(*(cache.next() for _ in range(3)))
    assert sorted(j.id for j in jokes) == ["1", "2", "3"] and fetch.await_count == 1


@pytest.mark.parametrize("failure", [
    aiohttp.ClientConnectionError("нет сети"),
    asyncio.TimeoutError(),
    "<html>другая разметка</html>",  # сайт ответил, но анекдотов не нашлось
])
async def test_site_failure_then_pause_then_retry(failure):
    now = [1000.0]
    fetch = fake_site(failure, jokes_page(7))
    cache = JokeCache(fetch, clock=lambda: now[0])
    with pytest.raises(JokesUnavailable):
        await cache.next()
    now[0] += jokes_cache.RETRY_AFTER - 1
    with pytest.raises(JokesUnavailable, match="недавно не ответил"):
        await cache.next()
    assert fetch.await_count == 1  # в паузе к сайту не ходим
    now[0] += 2
    assert (await cache.next()).id == "7"


# ---------- команда ----------

@contextlib.asynccontextmanager
async def typing():
    yield


def fake_ctx():
    ctx = mock.MagicMock()
    ctx.typing = typing
    ctx.reply, ctx.send = mock.AsyncMock(), mock.AsyncMock()
    return ctx


async def test_command_sends_joke_without_pings():
    cog = JokeCog(mock.Mock(), JokeCache(fake_site(SAMPLE)))
    ctx = fake_ctx()
    await JokeCog.joke.callback(cog, ctx)
    text = ctx.send.call_args.args[0]
    assert text.startswith("Первая строка.") and "anekdot.ru/id/101/" in text
    assert ctx.send.call_args.kwargs["allowed_mentions"].everyone is False


async def test_command_when_site_is_down_does_not_spend_cooldown():
    cog = JokeCog(mock.Mock(), JokeCache(fake_site(aiohttp.ClientConnectionError("нет сети"))))
    ctx = fake_ctx()
    await JokeCog.joke.callback(cog, ctx)
    assert ctx.reply.call_args.args[0] == UNAVAILABLE_TEXT and not ctx.send.called
    ctx.command.reset_cooldown.assert_called_once_with(ctx)


async def test_command_aliases(started):
    assert {started.get_command(a).name for a in ("анекдот", "анек", "joke", "шутка")} == {"анекдот"}
