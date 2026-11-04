"""Поддельные объекты Discord для тестов: взаимодействие (слэш-команда), участник, канал, вложение, сообщение."""

from pathlib import Path
from unittest import mock

import discord

LOG_CHANNEL_ID = 999


class FakeResponse:
    """interaction.response: первый ответ на команду (или defer — «бот думает…»). Второй ответ сюда Discord не примет."""

    def __init__(self, interaction):
        self.interaction = interaction
        self.done = False

    def is_done(self) -> bool:
        return self.done

    async def send_message(self, content=None, **kwargs):
        assert not self.done, "второй ответ через response — Discord его отклонит, нужен followup"
        self.done = True
        self.interaction.replies.append(("response", content, kwargs))

    async def defer(self, **kwargs):
        assert not self.done, "defer после ответа"
        self.done = True
        self.interaction.replies.append(("defer", None, kwargs))


class FakeFollowup:
    def __init__(self, interaction):
        self.interaction = interaction

    async def send(self, content=None, **kwargs):
        assert self.interaction.response.done, "followup без ответа или defer"
        self.interaction.replies.append(("followup", content, kwargs))


class FakeInteraction:
    """Вызов слэш-команды. replies — всё, что бот ответил: (способ, текст, параметры)."""

    def __init__(self, user=None, guild=None):
        self.user = user or member(manage=True)
        self.guild = guild or fake_guild()
        self.channel = FakeChannel(5)
        self.client = mock.Mock()
        self.client.user.name = "Sakuya"
        self.type = discord.InteractionType.application_command
        self.command = mock.Mock(qualified_name="test")
        self.replies = []
        self.response = FakeResponse(self)
        self.followup = FakeFollowup(self)

    @property
    def texts(self) -> list[str]:
        return [content for kind, content, _ in self.replies if kind != "defer"]

    @property
    def last(self) -> str:
        return self.texts[-1] if self.texts else ""

    @property
    def kinds(self) -> list[str]:
        return [kind for kind, _, _ in self.replies]


def member(roles=(), manage=False, member_id=7):
    """Участник сервера: роли по ID и есть ли право «Управлять сервером»."""
    m = mock.MagicMock(spec=discord.Member)
    m.id = member_id
    m.mention = f"<@{member_id}>"
    m.bot = False
    m.roles = [mock.Mock(id=r) for r in roles]
    m.guild_permissions = mock.Mock(manage_guild=manage)
    m.__str__ = lambda self: f"user{member_id}"
    return m


def permissions(**missing):
    """Права бота в канале: всё нужное есть, кроме переданного (attach_files=False и т. п.)."""
    p = mock.Mock(view_channel=True, send_messages=True, attach_files=True)
    for name, value in missing.items():
        setattr(p, name, value)
    return p


class FakeChannel:
    """Текстовый канал: запоминает отправленное; права бота в нём можно урезать."""

    def __init__(self, channel_id: int, **missing_permissions):
        self.id = channel_id
        self.mention = f"<#{channel_id}>"
        self.parent_id = None
        self.missing = missing_permissions
        self.sent: list[dict] = []

    def permissions_for(self, _member):
        return permissions(**self.missing)

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})

    @property
    def texts(self) -> list[str]:
        return [m["content"] for m in self.sent if m["content"]]

    def __str__(self):
        return f"channel{self.id}"


def fake_guild(filesize_limit=10 * 2**20, roles=(), channels=(), emojis=()):
    g = mock.MagicMock()
    g.filesize_limit = filesize_limit
    g.get_role = lambda i: object() if i in roles else None
    known = {c.id: c for c in channels}
    g.get_channel_or_thread = known.get
    g.emojis = [mock.Mock(name=n) for n in emojis]
    for e, n in zip(g.emojis, emojis):
        e.name = n
    return g


def attachment(name="cat.png", size=1000, data=b"image", error: Exception | None = None):
    """Вложение к команде; error — что выбросит скачивание."""
    a = mock.Mock()
    a.filename, a.size = name, size

    async def save(path):
        if error:
            raise error
        Path(path).write_bytes(data)

    async def read():
        if error:
            raise error
        return data

    a.save, a.read = save, read
    return a


def http_error(status=500, text="boom") -> discord.HTTPException:
    return discord.HTTPException(mock.Mock(status=status, reason="error"), text)


def message(content: str, channel_id=5):
    """Сообщение в чате; reactions и reply_files — что бот на него поставил и ответил."""
    msg = mock.MagicMock()
    msg.author.bot = False
    msg.author.__str__ = lambda self: "user"
    msg.content = content
    msg.mentions, msg.role_mentions = [], []
    msg.channel = FakeChannel(channel_id)
    msg.reactions, msg.reply_files = [], []

    async def add_reaction(emoji):
        msg.reactions.append(str(emoji))

    async def reply(files=(), **kwargs):
        msg.reply_files.append([f.filename for f in files])

    msg.add_reaction, msg.reply = add_reaction, reply
    return msg


async def call(command, cog, interaction=None, **params) -> FakeInteraction:
    """Вызывает слэш-команду кога как Discord: только переданные параметры, остальные — по умолчанию."""
    interaction = interaction or FakeInteraction()
    await command.callback(cog, interaction, **params)
    return interaction


_attachment_ids = iter(range(1, 10**9))
# «интернет» для тестов: адрес -> содержимое файла или исключение. Скачивание (sources.fetch) в тестах берёт отсюда
WEB: dict[str, bytes | BaseException] = {}


def image_attachment(data: bytes, name="pic.png", content_type="image/png"):
    """Настоящее (для isinstance) вложение Discord с картинкой — как в сообщении, по которому нажали ПКМ."""
    a = mock.MagicMock(spec=discord.Attachment)
    a.filename, a.content_type, a.size = name, content_type, len(data)
    a.url = f"https://cdn/{next(_attachment_ids)}/{name}"
    a.read = mock.AsyncMock(return_value=data)
    WEB[a.url] = data
    return a


def message_with(*attachments, message_id=42):
    msg = mock.MagicMock(spec=discord.Message)
    msg.id = message_id
    msg.attachments, msg.embeds, msg.stickers = list(attachments), [], []
    return msg
