"""Поиск слова в сообщении: формы слова, точное слово, часть слова (reactions/matching.py)."""

import pytest

from sakuya_bot.reactions.matching import rule_matches


def rule(word: str, match: str) -> dict:
    return {"word": word, "match": match}


@pytest.mark.parametrize("word, text, expected", [
    ("кот", "вижу кота", True),
    ("кот", "с котами", True),
    ("кот", "котлета", False),
    ("кот", "котик", False),                  # уменьшительные — отдельные слова
    ("человек", "люди пришли", True),
    ("бежать", "он бежал", True),
    ("доброе утро", "доброго утра всем", True),
    ("доброе утро", "утро доброе", False),    # фраза — слова подряд и по порядку
    ("cat", "two cats", True),
    ("go", "we went home", True),
    ("mouse", "three mice", True),
])
def test_forms(word, text, expected):
    assert rule_matches(rule(word, "forms"), text) is expected


@pytest.mark.parametrize("text, expected", [("кот пришёл", True), ("кота нет", False), ("котлета", False), ("кот!", True)])
def test_exact_word(text, expected):
    assert rule_matches(rule("кот", "word"), text) is expected


@pytest.mark.parametrize("text, expected", [("котлета", True), ("скот", True), ("кит", False)])
def test_contains(text, expected):
    assert rule_matches(rule("кот", "contains"), text) is expected
