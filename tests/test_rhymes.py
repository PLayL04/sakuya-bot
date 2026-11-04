"""Ответ гифкой на «да» и «нет» (rhymes/): когда срабатывает, шанс из настроек, ответ без пинга."""

from unittest import mock

import discord
import pytest

from sakuya_bot.rhymes import cog as rhymes_cog
from sakuya_bot.rhymes.cog import RhymeCog
from sakuya_bot.rhymes.detect import rhyme_kind

from fakes import member


@pytest.mark.parametrize("text, kind", [
    ("да", "yes"), ("Да!", "yes"), ("даааа)))", "yes"), ("да?", "yes"), ("ДА.", "yes"), ("да-да", "yes"),
    ("ну да", "yes"), ("конечно да 👍", "yes"), ("<@123> да", "yes"), ("да <:Wave:123456789012345678>", "yes"),
    ("нет", "no"), ("неееет", "no"), ("Нет.", "no"), ("точно нет", "no"), ("ну уж нет!", "no"),
    ("да, давай завтра", None),          # слово не последнее
    ("кот да пёс", None),
    ("нет денег", None),
    ("я думаю, что всё-таки да", None),  # длинное сообщение
    ("> да", None),                      # цитата — чужие слова
    ("!да", None),                       # команда
    ("дача", None), ("нетто", None), ("еда", None),
    ("", None), ("))))", None),
])
def test_rhyme_kind(text, kind):
    assert rhyme_kind(text) == kind


def test_quote_then_answer():
    assert rhyme_kind("> пойдёшь завтра?\nда") == "yes"


def message(text, bot=False):
    msg = mock.MagicMock(spec=discord.Message)
    msg.content, msg.id, msg.channel = text, 1, "общий"
    msg.author = member()
    msg.author.bot = bot
    return msg


@pytest.fixture
def gifs(tmp_path, monkeypatch):
    files = {"yes": tmp_path / "to-yes.gif", "no": tmp_path / "to-no.gif"}
    for path in files.values():
        path.write_bytes(b"GIF89a")
    monkeypatch.setattr(rhymes_cog, "FILES", files)
    return files


async def react(text, roll=0.0, **kwargs):
    msg = message(text, **kwargs)
    await RhymeCog(mock.Mock(), roll=lambda: roll).on_message(msg)
    return msg


async def test_replies_without_ping(gifs):
    msg = await react("да")  # шанс по умолчанию 10 %, выпало 0
    kwargs = msg.reply.call_args.kwargs
    assert kwargs["mention_author"] is False and kwargs["file"].filename == "to-yes.gif"
    assert (await react("нет")).reply.call_args.kwargs["file"].filename == "to-no.gif"


@pytest.mark.parametrize("chance, roll, replied", [
    (10, 0.099, True), (10, 0.10, False),  # 10 % — выпало меньше 0,1
    (100, 0.999, True),                    # 100 — всегда
    (0, 0.0, False),                       # 0 — выключено
])
async def test_chance(gifs, settings, chance, roll, replied):
    settings(yes_no_chance=chance)
    assert (await react("да", roll=roll)).reply.called is replied


async def test_disabled_does_not_check_text(gifs, settings, monkeypatch):
    settings(yes_no_chance=0)
    check = mock.Mock()
    monkeypatch.setattr(rhymes_cog, "rhyme_kind", check)
    await react("да")
    check.assert_not_called()


async def test_ignores_bots_and_other_text(gifs):
    assert not (await react("да", bot=True)).reply.called
    assert not (await react("да, давай")).reply.called


async def test_missing_gif_and_send_error_do_not_fail(gifs):
    gifs["yes"].unlink()
    assert not (await react("да")).reply.called
    msg = message("нет")
    msg.reply.side_effect = discord.HTTPException(mock.Mock(status=500, reason="err"), "сбой")
    await RhymeCog(mock.Mock(), roll=lambda: 0).on_message(msg)  # не падает
