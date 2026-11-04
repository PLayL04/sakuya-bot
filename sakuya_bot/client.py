"""Класс бота: подключение функций (когов), общие события и обработка ошибок."""

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .beer.cog import BeerCog
from .coffee.cog import CoffeeCog
from .config import GUILD, GUILD_ID
from .distort.cog import DistortCog
from .help import HelpCog
from .imaging.command import COOLDOWN_TEXT
from .imaging.sources import clear_downloads
from .logs import log, who
from .menus import MenuCog
from .museum.cog import MuseumCog
from .quotes.cog import QuoteCog
from .reactions.cog import ReactionCog
from .replies import respond
from .rhymes.cog import RhymeCog
from .schedules.cog import ScheduleCog
from .settings import current
from .settings.cog import SettingsCog
from .storage import remove_unused_images

RESPONSE_WINDOW = 3.0  # секунд: столько Discord ждёт первого ответа на команду, потом — «Приложение не отвечает»
SLOW_DELIVERY = 2.0  # команда дошла до бота позже — пишем в лог: на ответ почти не осталось времени
UNKNOWN_INTERACTION = 10062  # код ошибки Discord: ответили позже RESPONSE_WINDOW


def seconds_since_created(interaction: discord.Interaction) -> float:
    """Сколько секунд прошло с момента вызова команды. Время вызова сообщает Discord (в ID взаимодействия),
    а «сейчас» — по часам сервера: если они сбиты (проверить — timedatectl), цифры будут неточными."""
    return (discord.utils.utcnow() - interaction.created_at).total_seconds()


class SakuyaTree(app_commands.CommandTree):
    """Дерево слэш-команд с замером: когда команда дошла до бота (для разбора «Приложение не отвечает»)."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # вызывается первым делом для каждой команды, ещё до проверок доступа и самой команды
        delivered = seconds_since_created(interaction)
        interaction.extras["delivered_after"] = delivered
        if delivered > SLOW_DELIVERY and interaction.type == discord.InteractionType.application_command:
            log.warning("Команда /%s от %s дошла до бота только через %.1f с после вызова (Discord ждёт ответ %.0f с)",
                        command_name(interaction), who(interaction.user), delivered, RESPONSE_WINDOW)
        return True


def command_name(interaction: discord.Interaction) -> str:
    return interaction.command.qualified_name if interaction.command else "?"


def is_expired(error: BaseException) -> bool:
    """Discord уже не принимает ответ: с вызова команды прошло больше RESPONSE_WINDOW секунд."""
    return isinstance(error, discord.NotFound) and error.code == UNKNOWN_INTERACTION


class SakuyaBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        # нужно, чтобы видеть текст сообщений; включается в Developer Portal -> Bot -> Message Content Intent
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents, case_insensitive=True, help_command=None,
                         tree_cls=SakuyaTree)
        self.schedule_cog = ScheduleCog(self)
        self.reaction_cog = ReactionCog(self)

    async def setup_hook(self):
        self.tree.on_error = self.on_tree_error
        # каждый ког — отдельная функция бота со своими командами, данными и событиями;
        # настройки — первыми: остальные коги читают их уже при загрузке
        await self.add_cog(SettingsCog(self), guild=GUILD)
        await self.add_cog(self.schedule_cog, guild=GUILD)
        await self.add_cog(self.reaction_cog, guild=GUILD)
        await self.add_cog(CoffeeCog(self))
        await self.add_cog(BeerCog(self))
        await self.add_cog(DistortCog(self))
        await self.add_cog(MuseumCog(self))
        await self.add_cog(QuoteCog(self))
        await self.add_cog(RhymeCog(self))
        # после команд с картинками: меню берёт их кулдауны, а /help — их алиасы
        await self.add_cog(MenuCog(self))
        await self.add_cog(HelpCog(self), guild=GUILD)
        # синхронизация только для своего сервера — команды появляются сразу
        synced = await self.tree.sync(guild=GUILD)
        log.info("Слэш-команды синхронизированы с сервером %s: %s", GUILD_ID, ", ".join(f"/{c.name}" for c in synced))
        self.cleanup_images()  # картинки, оставшиеся от сбоя посреди команды
        clear_downloads()  # и скачанные для команд с картинками
        self.heartbeat.start()

    def cleanup_images(self) -> None:
        """Удаляет из img/ загруженные через бота картинки, которые больше не нужны ни одному правилу и расписанию.
        Сбой уборки (файл занят, нет прав, испорчен uploads.json) не мешает команде или циклу, которые её вызвали:
        ненужная картинка просто удалится в следующий раз."""
        try:
            in_use = {s.get("image") for s in self.schedule_cog.schedules}
            in_use |= {r["media"] for r in self.reaction_cog.reactions if r.get("media")}
            remove_unused_images(in_use)
        except Exception:
            log.exception("Не удалось убрать неиспользуемые картинки из img/")

    def stats(self) -> str:
        return f"расписаний: {len(self.schedule_cog.schedules)}, правил реакций: {len(self.reaction_cog.reactions)}"

    async def on_ready(self):
        log.info("Бот онлайн как %s, %s", who(self.user), self.stats())

    async def on_disconnect(self):
        log.warning("Соединение с Discord потеряно, переподключаемся...")

    async def on_resumed(self):
        log.info("Соединение с Discord восстановлено")

    async def on_app_command_completion(self, interaction: discord.Interaction, command: app_commands.Command):
        params = ", ".join(f"{k}={getattr(v, 'filename', v)}" for k, v in interaction.namespace)
        delivered = interaction.extras.get("delivered_after")
        log.info("Команда /%s от %s в #%s%s%s", command.qualified_name, who(interaction.user), interaction.channel,
                 f" ({params})" if params else "", f", дошла через {delivered:.1f} с" if delivered is not None else "")

    async def on_tree_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        name = command_name(interaction)
        original = getattr(error, "original", error)
        if is_expired(original):
            # ответ опоздал: сообщить участнику уже нельзя, а трассировка тут ничего не скажет. По двум временам
            # видно, кто виноват: дошла поздно — задержка доставки у Discord, дошла вовремя — медленно ушёл ответ
            delivered = interaction.extras.get("delivered_after")
            log.warning("Discord не дождался ответа на /%s от %s: команда дошла до бота через %s после вызова, "
                        "ответ отклонён через %.1f с (Discord ждёт %.0f с)", name, who(interaction.user),
                        f"{delivered:.1f} с" if delivered is not None else "? с",
                        seconds_since_created(interaction), RESPONSE_WINDOW)
            return
        if isinstance(error, app_commands.CheckFailure):
            log.warning("Отказано в доступе: /%s от %s (нет ни одной из ролей %s и права «Управлять сервером»)",
                        name, who(interaction.user), ", ".join(map(str, sorted(current().admin_roles))) or "—")
            text = "Эта команда доступна только участникам с ролью управления ботом или правом «Управлять сервером»."
        else:
            log.error("Ошибка команды /%s от %s: %s", name, who(interaction.user), original, exc_info=original)
            # иначе у модератора просто повиснет «Приложение не отвечает»
            text = f"Что-то пошло не так ({type(original).__name__}). Подробности — в логе бота, попробуйте ещё раз позже."
        if interaction.type == discord.InteractionType.autocomplete:
            return  # на подсказки при вводе сообщением не ответить
        try:
            await respond(interaction, text)
        except discord.HTTPException as e:
            log.error("Не удалось сообщить об ошибке команды /%s: %s", name, e)  # например, ответ уже просрочен

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return  # обычное сообщение, начинающееся с «!»
        if isinstance(error, commands.CommandOnCooldown):
            waiting = COOLDOWN_TEXT.get(ctx.command.name, "Подожди немного ⏳")
            await ctx.reply(f"{waiting} Попробуй через {error.retry_after:.0f} сек.", mention_author=False, delete_after=10)
            return
        if isinstance(error, commands.CheckFailure):
            return  # личные сообщения или канал, где команда не разрешена — молча игнорируем
        log.error("Ошибка команды !%s от %s: %s", ctx.command, who(ctx.author), error,
                  exc_info=getattr(error, "original", error))
        await ctx.reply("Что-то пошло не так, попробуй ещё раз позже.", mention_author=False)

    @tasks.loop(hours=1)
    async def heartbeat(self):
        # раз в час пишет в лог, что бот жив — удобно проверять, что он не завис
        log.info("Бот работает: задержка %d мс, %s", self.latency * 1000, self.stats())

    @heartbeat.before_loop
    async def before_heartbeat(self):
        await self.wait_until_ready()
