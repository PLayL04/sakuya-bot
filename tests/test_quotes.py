"""!цитата: деление на предложения, выбор отрывка, оформление в лимит Discord (quotes/picker.py)."""

import random

import pytest

from sakuya_bot.quotes.picker import (MAX_QUOTE_CHARS, MESSAGE_LIMIT, Quote, QuoteLibrary, QuoteSource, format_quote,
                                      parse_text, pick_quote, split_sentences)


@pytest.mark.parametrize("text, expected", [
    ("Первое. Второе! Третье?", ["Первое.", "Второе!", "Третье?"]),
    ("Это написал А. С. Пушкин. Да.", ["Это написал А. С. Пушкин.", "Да."]),
    ("То есть, т. е. так. Конец.", ["То есть, т. е. так.", "Конец."]),
    ("— Привет, — сказал он. — Пока.", ["— Привет, — сказал он.", "— Пока."]),
    ("Ну… и что? Ничего.", ["Ну… и что?", "Ничего."]),
    ("Это было в 1999 г. Давно.", ["Это было в 1999 г. Давно."]),
])
def test_split_sentences(text, expected):
    assert split_sentences(text) == expected


def test_parse_text_title_paragraphs_and_headings():
    title, paragraphs = parse_text("# Автор — Книга\n\nГлава 1\n\nПервый абзац. Тут два предложения.\n"
                                   "Продолжение абзаца.\n\nВторой абзац.\n")
    assert title == "Автор — Книга"
    assert paragraphs == [["Первый абзац.", "Тут два предложения.", "Продолжение абзаца."], ["Второй абзац."]]


def test_dialogue_lines_are_separate_paragraphs():
    _, paragraphs = parse_text("Он вошёл.\n— Кто здесь?\n— Я.\n\nКонец сцены.")
    assert paragraphs == [["Он вошёл."], ["— Кто здесь?"], ["— Я."], ["Конец сцены."]]


def test_short_quote_is_bold():
    assert format_quote(Quote([["Обычная фраза."]], "Кто-то")) == "> **Обычная фраза.**\n-# — *Кто-то*"


def test_markdown_is_escaped():
    message = format_quote(Quote([["Звёздочки *тут* и `код`, а также @everyone."]], "Автор"))
    assert "\\*тут\\*" in message and "\\`код\\`" in message


def test_escaped_quote_still_fits():
    long = " ".join(["*_~`|"] * 330) + "."  # ~2000 символов, после экранирования — почти вдвое больше
    message = format_quote(Quote([[long]], "Автор"))
    assert len(message) <= MESSAGE_LIMIT
    assert message.endswith("-# — *Автор*") and "…" in message


def test_long_quote_drops_sentences_from_the_end():
    paragraphs = [[f"Предложение номер {i} с *разметкой* внутри." for i in range(60)]]
    message = format_quote(Quote(paragraphs, "Автор"))
    assert len(message) <= MESSAGE_LIMIT and message.endswith("-# — *Автор*")
    assert "Предложение номер 0 " in message and "номер 59 " not in message


def test_long_source_is_cut():
    assert len(format_quote(Quote([["Фраза."]], "И" * 5000))) < 300


@pytest.mark.parametrize("seed", range(30))
def test_pick_quote_whole_sentences(seed):
    sentences = [f"Предложение {i} довольно обычной длины, чтобы набрать текст." for i in range(40)]
    source = QuoteSource("Книга", [sentences[i:i + 4] for i in range(0, 40, 4)])
    quote = pick_quote(source, random.Random(seed))
    assert quote is not None and quote.source == "Книга"
    assert all(s in sentences for p in quote.paragraphs for s in p)
    assert len(quote.text) <= MAX_QUOTE_CHARS + 100


def test_library_reads_utf8_and_cp1251(tmp_path):
    (tmp_path / "a.txt").write_text("# Первый\n\nТекст первого файла. Ещё предложение.", encoding="utf-8")
    (tmp_path / "b.txt").write_bytes("Текст из Блокнота в кодировке Windows. Второе предложение.".encode("cp1251"))
    (tmp_path / "skip.md").write_text("Не текст для цитат.", encoding="utf-8")
    library = QuoteLibrary(tmp_path)
    assert sorted(s.name for s in library.sources()) == ["b", "Первый"]
    assert library.pick(random.Random(1)) is not None


def test_library_empty_folder(tmp_path):
    assert QuoteLibrary(tmp_path / "нет").pick() is None
