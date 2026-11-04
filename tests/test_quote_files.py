"""Команды /quotes (quotes/files.py): добавить, скачать, удалить тексты для !цитата; имена файлов; канал логов."""

import pytest

from sakuya_bot.quotes.files import MAX_FILE_BYTES, QuoteFilesCog, safe_name
from sakuya_bot.quotes.picker import QuoteLibrary

from fakes import LOG_CHANNEL_ID, FakeInteraction, attachment, call, member

TEXT = "# Автор — Книга\n\nПервый абзац. Тут два предложения.\n\nВторой абзац с репликой.\n"


@pytest.fixture
def folder(tmp_path):
    return tmp_path / "quotes"


@pytest.fixture
def cog(bot, folder, settings):
    settings(log_channel=LOG_CHANNEL_ID)
    return QuoteFilesCog(bot, QuoteLibrary(folder))


def text_file(name="книга.txt", text=TEXT, encoding="utf-8", data=None):
    data = data if data is not None else text.encode(encoding)
    return attachment(name, size=len(data), data=data)


def upload(cog, file, **params):
    return call(QuoteFilesCog.upload, cog, file=file, **params)


@pytest.mark.parametrize("name, safe", [
    ("Ночное такси.txt", "Ночное такси.txt"),
    ("taxi", "taxi.txt"),
    ("../../bot.py", "bot.py.txt"),             # без папок
    ("..\\..\\data\\settings.toml", "settings.toml.txt"),
    ("<script>*?.txt", "script.txt"),
    ("...", None), ("", None),
])
def test_safe_name(name, safe):
    assert safe_name(name) == safe


async def test_upload_list_download_remove(cog, folder, log_channel):
    it = await upload(cog, text_file())
    assert it.kinds == ["defer", "followup"] and "Готово" in it.last and "«Автор — Книга»" in it.last
    assert (folder / "книга.txt").read_text(encoding="utf-8") == TEXT
    assert any("добавляет текст для цитат" in t and "<@7>" in t for t in log_channel.texts)
    assert cog.library.sources()[0].name == "Автор — Книга"  # !цитата видит файл сразу

    it = await call(QuoteFilesCog.list_, cog)
    assert "`книга.txt`" in it.last and "2 абзацев" in it.last and "100 % выдачи" in it.last

    it = await call(QuoteFilesCog.download, cog, name="книга.txt")
    assert it.replies[-1][2]["file"].filename == "книга.txt"

    it = await call(QuoteFilesCog.remove, cog, name="книга.txt")
    assert "Удалено" in it.last and it.replies[-1][2]["file"].filename == "книга.txt"  # копия — в ответе
    assert not (folder / "книга.txt").exists() and cog.library.sources() == []
    assert any("удаляет текст для цитат" in t for t in log_channel.texts)


async def test_cp1251_is_saved_as_utf8(cog, folder):
    await upload(cog, text_file(encoding="cp1251"))
    assert (folder / "книга.txt").read_text(encoding="utf-8") == TEXT


async def test_existing_file_is_replaced_only_on_request(cog, folder):
    await upload(cog, text_file())
    it = await upload(cog, text_file(text="Новый текст. Совсем другой."))
    assert "уже есть" in it.last and (folder / "книга.txt").read_text(encoding="utf-8") == TEXT
    it = await upload(cog, text_file(text="Новый текст. Совсем другой."), replace=True)
    assert "Готово" in it.last and "Новый текст" in (folder / "книга.txt").read_text(encoding="utf-8")


async def test_custom_name(cog, folder):
    await upload(cog, text_file("x.txt"), name="Ночное такси")
    assert (folder / "Ночное такси.txt").exists()


@pytest.mark.parametrize("file, needle", [
    (text_file("книга.pdf"), "`.txt`"),
    (attachment("big.txt", size=MAX_FILE_BYTES + 1), "слишком большой"),
    (text_file(data=b"\x89PNG\x00\x00binary"), "не похоже на текст"),
    (text_file(text="Глава 1\nОглавление\n"), "ни одного предложения"),
])
async def test_bad_files_are_not_saved(cog, folder, file, needle):
    it = await upload(cog, file)
    assert needle in it.last
    assert not folder.exists() or not list(folder.iterdir())


async def test_missing_file(cog):
    for command in (QuoteFilesCog.download, QuoteFilesCog.remove):
        assert "нет" in (await call(command, cog, name="нет-такого.txt")).last


async def test_empty_list_and_autocomplete(cog, folder):
    assert "пока нет" in (await call(QuoteFilesCog.list_, cog)).last
    folder.mkdir()
    (folder / "a.txt").write_text("Предложение. Ещё одно.", encoding="utf-8")
    (folder / "b.txt").write_text("Глава 1", encoding="utf-8")  # без предложений — в списке с пометкой
    choices = await cog.name_autocomplete(FakeInteraction(), "")
    assert [c.value for c in choices] == ["a.txt", "b.txt"]
    it = await call(QuoteFilesCog.list_, cog)
    assert "не нашлось ни одного предложения" in it.last and "100 % выдачи" in it.last


async def test_only_admins(cog, settings):
    settings(admin_roles=frozenset({111}))
    assert not await cog.interaction_check(FakeInteraction(user=member(roles=[222])))
    assert await cog.interaction_check(FakeInteraction(user=member(roles=[111])))
