"""Команды /reaction и реакции на сообщения (reactions/cog.py) с поддельным Discord."""

import json

import pytest

import sakuya_bot.reactions.cog as reactions_cog
from sakuya_bot.reactions.cog import ReactionCog

from fakes import LOG_CHANNEL_ID, attachment, call, http_error, message


@pytest.fixture
def cog(bot, settings):
    settings(log_channel=LOG_CHANNEL_ID)
    return bot.reaction_cog


def add(cog, word="кот", **params):
    return call(ReactionCog.add, cog, word=word, **params)


# ---------- создание ----------

async def test_add_combination(cog, log_channel):
    it = await add(cog, word="привет", emoji="🇰 🇴 🇹")
    assert cog.reactions[-1]["emojis"] == ["🇰", "🇴", "🇹"] and "(по порядку)" in it.last
    assert any("создаёт правило" in t for t in log_channel.texts)


async def test_add_keycap(cog):
    await add(cog, emoji="1️⃣")
    assert cog.reactions[-1]["emojis"] == ["1️⃣"]


@pytest.mark.parametrize("params, needle", [
    ({"emoji": "<:x1:999999999999999999>"}, "не видит"),     # эмодзи другого сервера
    ({"emoji": "кот"}, "не эмодзи"),
    ({"emoji": "🐱 🐱"}, "дважды"),
    ({}, "Укажите эмодзи"),
    ({"word": "  ", "emoji": "🐱"}, "не может быть пустым"),
    ({"image": attachment(size=50 * 2**20)}, "слишком большой"),
    ({"image": attachment("doc.pdf")}, "не картинка"),
    ({"file": "нет.png"}, "не найден"),
    ({"image": attachment(error=http_error())}, "Не удалось скачать"),
])
async def test_add_rejects(cog, params, needle):
    it = await add(cog, **params)
    assert needle in it.last and not cog.reactions


async def test_add_with_upload_defers(cog, img_dir):
    it = await add(cog, emoji="🐱", image=attachment("cat.gif"))
    assert it.kinds == ["defer", "followup"] and (img_dir / cog.reactions[-1]["media"]).exists()


async def test_old_rules_are_upgraded(isolated, bot):
    (isolated / "reactions.json").write_text(json.dumps(
        [{"id": "old001", "word": "кот", "emoji": "🐱", "media": None, "match": "forms"}]), encoding="utf-8")
    cog = ReactionCog(bot)
    assert cog.reactions[0]["emojis"] == ["🐱"] and "emoji" not in cog.reactions[0]


async def test_long_list_is_split(cog):
    cog.reactions = [{"id": f"{i:06x}", "word": f"слово{i}", "emojis": ["🐱", "🐶", "🐭"], "media": f"картинка-{i}.gif",
                      "match": "forms"} for i in range(150)]
    it = await call(ReactionCog.list_, cog)
    assert len(it.texts) > 1 and all(len(t) <= 2000 for t in it.texts)


# ---------- реакции на сообщения ----------

async def test_reactions_in_order_without_duplicates(cog):
    cog.reactions = [
        {"id": "a", "word": "привет", "emojis": ["🇰", "🇴", "🇹"], "media": None, "match": "forms"},
        {"id": "b", "word": "привет", "emojis": ["🇴", "🐱"], "media": None, "match": "forms"},
    ]
    msg = message("Привет всем")
    await cog.on_message(msg)
    assert msg.reactions == ["🇰", "🇴", "🇹", "🐱"]


async def test_bot_messages_are_ignored(cog):
    await add(cog, emoji="🐱")
    msg = message("кот")
    msg.author.bot = True
    await cog.on_message(msg)
    assert msg.reactions == []


async def test_image_reply_cooldown(cog, img_dir, settings):
    settings(image_reply_cooldown=60)
    (img_dir / "cat.gif").write_bytes(b"gif")
    await add(cog, file="cat.gif")
    first, second, other_channel = message("кот"), message("снова кот"), message("кот", channel_id=6)
    for msg in (first, second, other_channel):
        await cog.on_message(msg)
    assert first.reply_files == [["cat.gif"]]
    assert second.reply_files == []                 # в том же канале — кулдаун
    assert other_channel.reply_files == [["cat.gif"]]  # в другом канале — свой кулдаун


async def test_ping_emoji(cog, bot, monkeypatch):
    msg = message("эй")
    monkeypatch.setattr(type(bot), "user", property(lambda self: "BOT"))
    msg.mentions = ["BOT"]
    msg.guild.emojis = []
    await cog.on_message(msg)
    assert msg.reactions == [reactions_cog.PING_EMOJI_FALLBACK]  # эмодзи из настроек на сервере нет — запасной


# ---------- изменение, пауза, удаление ----------

@pytest.fixture
async def rule(cog):
    await add(cog, word="привет", emoji="👋")
    return cog.reactions[-1]


async def test_edit(cog, rule, log_channel):
    it = await call(ReactionCog.edit, cog, id=rule["id"], word="Здравствуй", emoji="🇭 🇮", match="word")
    assert (rule["word"], rule["emojis"], rule["match"]) == ("здравствуй", ["🇭", "🇮"], "word") and "Готово" in it.last
    assert any("изменяет правило" in t and "→" in t for t in log_channel.texts)


async def test_edit_cannot_remove_everything(cog, rule):
    it = await call(ReactionCog.edit, cog, id=rule["id"], emoji="-")
    assert "ни эмодзи, ни картинки" in it.last and rule["emojis"] == ["👋"]


async def test_edit_swap_emoji_and_picture(cog, rule, img_dir):
    await call(ReactionCog.edit, cog, id=rule["id"], emoji="-", image=attachment("hi.gif"))
    assert rule["emojis"] == [] and rule["media"]
    picture = rule["media"]
    await call(ReactionCog.edit, cog, id=rule["id"], emoji="🙂", file="-")
    assert rule["media"] is None and rule["emojis"] == ["🙂"]
    assert not (img_dir / picture).exists()  # убранная картинка удалена с диска


@pytest.mark.parametrize("params, needle", [({"emoji": "кот"}, "не эмодзи"), ({}, "Укажите, что изменить"),
                                            ({"word": " "}, "не может быть пустым")])
async def test_edit_rejects(cog, rule, params, needle):
    before = dict(rule)
    it = await call(ReactionCog.edit, cog, id=rule["id"], **params)
    assert needle in it.last and rule == before


async def test_pause_resume(cog, rule):
    await call(ReactionCog.pause, cog, id=rule["id"])
    msg = message("привет")
    await cog.on_message(msg)
    assert msg.reactions == []
    assert "на паузе" in (await call(ReactionCog.list_, cog)).last
    await call(ReactionCog.resume, cog, id=rule["id"])
    await cog.on_message(msg)
    assert msg.reactions == ["👋"]


async def test_remove(cog, rule, log_channel):
    it = await call(ReactionCog.remove, cog, id=rule["id"])
    assert "удалено" in it.last and not cog.reactions
    assert "не найдено" in (await call(ReactionCog.remove, cog, id=rule["id"])).last
    assert any("удаляет правило" in t for t in log_channel.texts)


async def test_shared_picture_is_kept_until_unused(cog, bot, img_dir):
    """Картинка удаляется, только когда она не нужна ни правилу, ни расписанию; файлы, положенные вручную, — никогда."""
    from sakuya_bot.schedules.cog import ScheduleCog
    from fakes import FakeChannel

    (img_dir / "вручную.png").write_bytes(b"x")
    await add(cog, emoji="🐱", file="вручную.png")
    await add(cog, word="пёс", emoji="🐶", image=attachment("dog.gif"))
    dog = cog.reactions[-1]["media"]
    await call(ScheduleCog.daily, bot.schedule_cog, time="08:00", channel=FakeChannel(5), file=dog)
    await call(ReactionCog.remove, cog, id=cog.reactions[-1]["id"])
    assert (img_dir / dog).exists()  # её ещё использует расписание
    await call(ScheduleCog.remove, bot.schedule_cog, id=bot.schedule_cog.schedules[0]["id"])
    assert not (img_dir / dog).exists()
    await call(ReactionCog.remove, cog, id=cog.reactions[0]["id"])
    assert (img_dir / "вручную.png").exists()
