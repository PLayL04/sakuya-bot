"""Проверки доступа к командам."""

import discord

from .config import ADMIN_ROLE_IDS


def is_admin(user: discord.abc.User) -> bool:
    """Есть ли у участника хотя бы одна из ролей ADMIN_ROLE_ID."""
    return isinstance(user, discord.Member) and any(r.id in ADMIN_ROLE_IDS for r in user.roles)


class AdminOnly:
    """Примесь для кога: все его слэш-команды доступны только участникам с ролью ADMIN_ROLE_ID.

    discord.py вызывает interaction_check кога перед каждой его слэш-командой.
    Ставится в списке родителей перед commands.GroupCog, иначе не переопределит его метод.
    """

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return is_admin(interaction.user)
