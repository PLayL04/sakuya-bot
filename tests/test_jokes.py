"""!анекдот: разбор страницы anekdot.ru, запас анекдотов и повторная загрузка, ошибки сайта (jokes/). Без сети:
страница собирается в тесте по образцу разметки сайта."""

import asyncio
import contextlib
from urllib.parse import parse_qs, urlparse
from unittest import mock

import aiohttp
import pytest

from sakuya_bot.jokes import cache as jokes_cache
from sakuya_bot.jokes.cache import JokeCache, JokesUnavailable, NothingFound, normalize_query, search_url
from sakuya_bot.jokes.cog import UNAVAILABLE_TEXT, JokeCog
from sakuya_bot.jokes.parser import MAX_JOKE_CHARS, Joke, format_joke, parse_jokes, parse_total, tidy


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
    assert jokes[0].text == 'Первая строка.\n— Реплика "в кавычках", с пробелами.\n— Ответ.'


def test_page_without_jokes():
    assert parse_jokes("<html><body>Сайт на обслуживании</body></html>") == []


def test_format_joke_escapes_markup_and_links_source():
    text = format_joke(Joke("-202", "Про *звёздочки* и _подчёркивания_"))
    assert text.startswith(r"Про \*звёздочки\* и \_подчёркивания\_")
    assert text.endswith("-# [anekdot.ru](<https://www.anekdot.ru/id/-202/>)")  # <…> — без превью сайта


# ---------- оформление: старые тексты с переносами посреди предложений и слипшимися репликами ----------

def test_tidy_hard_wrapped_dialogue():
    raw = ("Мама зовёт сына:- Петя! Иди обедать, суп\nстынет! Петя прибегает.- Мам, а можно\n"
           "- без лука? Мама кивает и\nулыбается.- Конечно, - говорит она, - можно.")
    assert tidy(raw) == ("Мама зовёт сына:\n"
                         "— Петя! Иди обедать, суп стынет! Петя прибегает.\n"
                         "— Мам, а можно — без лука? Мама кивает и улыбается.\n"
                         "— Конечно, — говорит она, — можно.")


def test_tidy_keeps_intended_lines():
    raw = "Встречаются двое.\n- Привет!\n -Здорово.\nСтишок:\nУтро красит\nНежным цветом\n\n2. Второе."
    assert tidy(raw) == "Встречаются двое.\n— Привет!\n— Здорово.\nСтишок:\nУтро красит\nНежным цветом\n2. Второе."


def test_tidy_keeps_hyphenated_words():
    assert tidy("Ну-ка, кто-нибудь - сюда! Счёт 2-1.") == "Ну-ка, кто-нибудь — сюда! Счёт 2-1."


def test_reply_lines_are_not_lists_in_discord():
    lines = format_joke(Joke("1", "Вопрос:\n— Ответ.\n1. Пункт\n# не заголовок")).split("\n")
    assert lines[1] == "— Ответ." and lines[2] == r"1\. Пункт" and lines[3] == r"\# не заголовок"


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

    async def slow_fetch(url):
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


# ---------- поиск: !анекдот прапорщик гвозди ----------

def search_page(total, *ids):
    return page(*(topic(i, f"Найденный анекдот {i}.") for i in ids)).replace(
        "<body>", f"<body><div>в тексте символов не больше; всего найдено: {total}</div>")


class SearchSite:
    """Поиск на сайте: страница по номеру из адреса. urls — какие адреса запрашивали."""

    def __init__(self, pages):
        self.pages, self.urls = pages, []

    async def __call__(self, url):
        self.urls.append(url)
        if "/random/" in url:
            return jokes_page("r1", "r2")
        number = int(parse_qs(urlparse(url).query)["page"][0])
        return self.pages.get(number, search_page(0))

    def page_numbers(self):
        return [int(parse_qs(urlparse(u).query)["page"][0]) for u in self.urls if "/search/" in u]


def test_search_url_like_the_form_on_the_site():
    params = parse_qs(urlparse(search_url("прапорщик гвозди", 2)).query)
    assert params["query"] == ["прапорщик гвозди"] and params["page"] == ["2"]
    assert params["mode"] == ["all"] and params["xcnt"] == ["20"] and params["order"] == ["0"]
    assert all(params[f"ch[{s}]"] == ["on"] for s in "jsotabcpeE")
    assert params["max_len"] == ["1800"]  # длинные тексты сайт отсеивает сам


def test_normalize_query_and_total():
    assert normalize_query("  Прапорщик   ГВОЗДИ ") == "прапорщик гвозди"
    assert normalize_query("   ") == ""
    assert parse_total(search_page(1000, 1)) == 1000 and parse_total(jokes_page(1)) is None


def test_search_highlight_is_dropped():
    html = page(topic(5, 'Сидит <span style="background-color:#ffff80">прапорщик</span>, забивает гвоздь.'))
    assert parse_jokes(html)[0].text == "Сидит прапорщик, забивает гвоздь."


async def test_search_pages_one_by_one_then_from_the_start(monkeypatch):
    monkeypatch.setattr(jokes_cache, "PER_PAGE", 2)
    site = SearchSite({1: search_page(3, 1, 2), 2: search_page(3, 3)})
    cache = JokeCache(site)
    ids = [(await cache.next("прапорщик гвозди")).id for _ in range(5)]
    assert ids == ["1", "2", "3", "1", "2"]  # по порядку, после последней страницы — сначала
    assert site.page_numbers() == [1, 2, 1]  # новая страница — только когда кончилась предыдущая


async def test_page_past_the_end_starts_over(monkeypatch):
    site = SearchSite({1: search_page(25, 1)})  # «найдено 25», а второй страницы уже нет
    cache = JokeCache(site)
    assert [(await cache.next("гвозди")).id for _ in range(2)] == ["1", "1"]
    assert site.page_numbers() == [1, 2, 1]


async def test_each_query_and_random_have_own_stock():
    site = SearchSite({1: search_page(2, 10, 11)})
    cache = JokeCache(site)
    assert (await cache.next("гвозди")).id == "10"
    assert (await cache.next()).id == "r1"  # случайные — из своего запаса
    assert (await cache.next(normalize_query(" ГВОЗДИ "))).id == "11"  # тот же запрос — тот же запас
    assert site.page_numbers() == [1]


async def test_nothing_found_is_not_a_site_failure():
    site = SearchSite({})
    cache = JokeCache(site)
    with pytest.raises(NothingFound):
        await cache.next("зюзюка")
    assert cache.failed_at is None and (await cache.next()).id == "r1"  # сайт работает — пауза не нужна


async def test_command_with_words_searches():
    site = SearchSite({1: search_page(1, 42)})
    cog = JokeCog(mock.Mock(), JokeCache(site))
    ctx = fake_ctx()
    await JokeCog.joke.callback(cog, ctx, words="  Прапорщик  гвозди")
    assert ctx.send.call_args.args[0].startswith("Найденный анекдот 42.")
    assert "query=%D0%BF%D1%80%D0%B0%D0%BF%D0%BE%D1%80%D1%89%D0%B8%D0%BA+%D0%B3%D0%B2%D0%BE%D0%B7%D0%B4%D0%B8" in site.urls[0]


async def test_command_nothing_found():
    cog = JokeCog(mock.Mock(), JokeCache(SearchSite({})))
    ctx = fake_ctx()
    await JokeCog.joke.callback(cog, ctx, words="зю_зюка")
    assert ctx.reply.call_args.args[0] == r"Не нашёл анекдотов по запросу «зю\_зюка»."
    ctx.command.reset_cooldown.assert_called_once_with(ctx)


async def test_single_page_result_is_not_downloaded_again():
    """«прапорщик гвозди» в режиме all — всего 4 текста: их выдаём по кругу, а не качаем ту же страницу снова."""
    site = SearchSite({1: search_page(4, 1, 2, 3, 4)})
    cache = JokeCache(site)
    ids = [(await cache.next("прапорщик гвозди")).id for _ in range(10)]
    assert ids == ["1", "2", "3", "4", "1", "2", "3", "4", "1", "2"]
    assert site.page_numbers() == [1]
