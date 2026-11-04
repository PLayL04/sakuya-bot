"""Проверки доступа к командам."""

import discord

from .settings import current


def is_admin(user: discord.abc.User) -> bool:
    """Может ли участник управлять ботом: есть роль из настроек (admin_roles) или право «Управлять сервером».
    Право сервера работает всегда — так доступ не теряется, если роль удалили или настройки испорчены."""
    if not isinstance(user, discord.Member):
        return False
    return user.guild_permissions.manage_guild or any(r.id in current().admin_roles for r in user.roles)


class AdminOnly:
    """Примесь для кога: все его слэш-команды доступны только тем, кто управляет ботом (см. is_admin).

    discord.py вызывает interaction_check кога перед каждой его слэш-командой.
    Ставится в списке родителей перед commands.GroupCog, иначе не переопределит его метод.
    """

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return is_admin(interaction.user)
