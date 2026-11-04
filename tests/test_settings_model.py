"""Файл настроек data/settings.toml: чтение, проверка, шаблон, дописывание новых ключей, хранилище."""

import os
import time

import pytest

from sakuya_bot import config
from sakuya_bot.settings import STORE, current
from sakuya_bot.settings.model import Settings, SettingsError, changes, complete, describe_value, parse, render, FIELDS


def problems(text: str) -> list[str]:
    with pytest.raises(SettingsError) as e:
        parse(text)
    return e.value.problems


def test_template_round_trip():
    s = Settings(admin_roles=frozenset({111, 222}), fun_channels=frozenset({5}), fun_cooldown=7, log_channel=999,
                 timezone="Asia/Yekaterinburg", ping_emoji="Wave")
    assert parse(render(s)) == s


def test_missing_keys_get_defaults():
    assert parse("[fun]\ncooldown = 5\n") == Settings(fun_cooldown=5)
    assert parse("") == Settings()


def test_ids_as_strings_and_numbers():
    assert parse('[fun]\nchannels = ["123", 456]\n').fun_channels == {123, 456}
    assert parse('[logs]\nchannel = "123456789012345678"\n').log_channel == 123456789012345678


@pytest.mark.parametrize("text, needle", [
    ("[fun]\ncooldwn = 5", "cooldown"),                      # опечатка — подсказка, какие ключи есть
    ("[funn]\ncooldown = 5", "Неизвестный раздел"),
    ('[fun]\ncooldown = "5"', "целое число"),
    ("[fun]\ncooldown = -1", "от 0 до 3600"),
    ("[fun]\ncooldown = true", "целое число"),
    ("[access]\nadmin_roles = 123", "список ID"),
    ('[access]\nadmin_roles = ["роль модератора"]', "не похоже на ID"),
    ('[schedule]\ntimezone = "Moscow"', "часовой пояс"),
    ('[reactions]\nping_emoji = ""', "пустым"),
    ("[reactions]\nyes_no_chance = 150", "процентов от 0 до 100"),
    ('[logs]\nchannel = "логи"', "не похоже на ID канала"),
    ("[fun\ncooldown = 5", "TOML"),
])
def test_errors(text, needle):
    assert needle in problems(text)[0]


def test_all_errors_at_once():
    assert len(problems("[fun]\ncooldown = -1\nchannels = 5")) == 2


def test_emoji_name_colons_are_stripped():
    assert parse('[reactions]\nping_emoji = ":Wave:"').ping_emoji == "Wave"


def test_every_setting_documented_in_template():
    text = render(Settings())
    for f in FIELDS:
        assert f"{f.key} = " in text and f"[{f.section}]" in text


OLD_FILE = """# мой комментарий: роль модераторов
[access]
admin_roles = [111]  # модеры

[fun]
cooldown = 30
"""


def test_complete_adds_new_keys_and_keeps_comments():
    text, added = complete(OLD_FILE)
    assert {f.key for f in added} == {"channels", "image_cooldown", "ping_emoji", "yes_no_chance", "timezone", "channel"}
    assert "# мой комментарий: роль модераторов" in text and "admin_roles = [111]  # модеры" in text
    assert parse(text) == Settings(admin_roles=frozenset({111}), fun_cooldown=30)
    assert complete(text) == (text, [])  # второй раз дописывать нечего


@pytest.mark.parametrize("unusual", ["[fun\n", "fun.cooldown = 5\n"])  # сломанный файл; раздел без заголовка [fun]
def test_complete_leaves_unusual_files_alone(unusual):
    assert complete(unusual) == (unusual, [])


def test_describe_and_changes():
    old, new = Settings(), Settings(fun_cooldown=0, log_channel=5)
    assert [f.key for f in changes(old, new)] == ["cooldown", "channel"]
    by_key = {f.key: f for f in FIELDS}
    assert describe_value(by_key["cooldown"], 0) == "без ограничения"
    assert describe_value(by_key["channel"], 0) == "не задан"
    assert describe_value(by_key["yes_no_chance"], 0) == "выключено"
    assert describe_value(by_key["yes_no_chance"], 10) == "10 %"
    assert describe_value(by_key["channels"], frozenset()) == "во всех каналах"
    assert describe_value(by_key["admin_roles"], frozenset({1}), lambda kind, i: f"<@&{i}>") == "<@&1>"


# ---------- хранилище: файл на диске ----------

def touch_later(path, seconds):
    t = time.time() + seconds
    os.utime(path, (t, t))


def test_store_creates_file_from_env_defaults(monkeypatch):
    monkeypatch.setattr(STORE, "settings", Settings(admin_roles=frozenset({111, 222}), timezone="Asia/Yekaterinburg"))
    STORE.load()
    assert "admin_roles = [111, 222]" in STORE.read_text()
    assert parse(STORE.read_text()) == current()


def test_store_completes_old_file_on_start():
    STORE.path.write_text(OLD_FILE, encoding="utf-8")
    STORE.load()
    assert "[logs]" in STORE.read_text()
    assert current().fun_cooldown == 30 and current().admin_roles == {111}  # и значения из файла применились


def test_store_reloads_edited_file_and_ignores_broken():
    STORE.load()
    STORE.path.write_text(STORE.read_text().replace("cooldown = 15", "cooldown = 30"), encoding="utf-8")
    touch_later(STORE.path, 5)
    assert STORE.reload_if_changed() and current().fun_cooldown == 30
    STORE.path.write_text("[fun]\ncooldown = много\n", encoding="utf-8")
    touch_later(STORE.path, 10)
    assert not STORE.reload_if_changed() and current().fun_cooldown == 30  # сломанный файл — прежние настройки


def test_store_apply():
    STORE.load()
    STORE.apply(render(Settings(fun_cooldown=3)))
    assert current().fun_cooldown == 3 and "cooldown = 3" in STORE.read_text()
    with pytest.raises(SettingsError):
        STORE.apply("[fun]\ncooldown = -5\n")
    assert current().fun_cooldown == 3


def test_env_migration(monkeypatch):
    monkeypatch.setenv("ADMIN_ROLE_ID", "111, 222")
    monkeypatch.setenv("FUN_CHANNEL_ID", "5 6")
    monkeypatch.setenv("TIMEZONE", "Asia/Novosibirsk")
    assert config._env_defaults() == {"admin_roles": {111, 222}, "fun_channels": {5, 6}, "timezone": "Asia/Novosibirsk"}


def test_env_migration_bad_timezone(monkeypatch):
    monkeypatch.setenv("TIMEZONE", "Moscow")
    with pytest.raises(SystemExit):
        config._env_defaults()
