"""Поиск слова в тексте: все формы слова (pymorphy3 / simplemma), точное слово или часть слова. Без Discord."""

import re
from functools import lru_cache

import pymorphy3
import simplemma

MATCH_NAMES = {"forms": "все формы", "word": "точное слово", "contains": "часть слова"}
morph = pymorphy3.MorphAnalyzer()


@lru_cache(maxsize=10_000)
def word_lemmas(word: str) -> frozenset[str]:
    if re.fullmatch(r"[a-z]+", word):
        # английское слово: simplemma (cats -> cat, went -> go); само слово тоже оставляем
        return frozenset({word, simplemma.lemmatize(word, lang="en")})
    # русское: все начальные формы слова — у «стали» это и «сталь», и «стать», лучше лишняя реакция, чем пропущенная
    return frozenset(p.normal_form for p in morph.parse(word))


@lru_cache(maxsize=256)
def text_lemmas(text: str) -> tuple[frozenset[str], ...]:
    return tuple(word_lemmas(w) for w in re.findall(r"\w+", text))


def rule_matches(rule: dict, text: str) -> bool:
    if rule["match"] == "forms":
        # фраза совпадает, если подряд идущие слова сообщения — формы слов правила
        need, have = text_lemmas(rule["word"]), text_lemmas(text)
        return any(
            all(need[j] & have[i + j] for j in range(len(need)))
            for i in range(len(have) - len(need) + 1)
        ) if need else False
    if rule["match"] == "contains":
        return rule["word"] in text
    # целое слово: не часть другого слова (\w в Python понимает кириллицу)
    return re.search(rf"(?<!\w){re.escape(rule['word'])}(?!\w)", text) is not None
