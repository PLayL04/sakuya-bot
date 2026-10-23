"""Точка входа: python bot.py. Сам бот — в пакете sakuya_bot/."""

from sakuya_bot.client import SakuyaBot
from sakuya_bot.config import TOKEN
from sakuya_bot.logs import log, setup_logging

if __name__ == "__main__":
    setup_logging()
    log.info("Запуск sakuya-bot...")
    # log_handler=None: логирование уже настроено в setup_logging, в том числе для discord.py
    SakuyaBot().run(TOKEN, log_handler=None)
