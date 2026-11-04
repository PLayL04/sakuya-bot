"""Длинные списки — несколькими сообщениями (replies.py)."""

from sakuya_bot.replies import MESSAGE_LIMIT, split_message


def test_long_list_is_split_without_losing_lines():
    lines = [f"`{i:06x}` — **слово номер {i}** → 🐱 🐶 (все формы)" for i in range(200)]
    parts = split_message(lines, header="Заголовок")
    assert len(parts) > 1
    assert all(len(p) <= MESSAGE_LIMIT for p in parts)
    assert "\n".join(parts) == "Заголовок\n" + "\n".join(lines)  # ни одна строка не потеряна и не разорвана


def test_short_list_is_one_message():
    assert split_message(["a", "b"], "h") == ["h\na\nb"]


def test_too_long_line_is_cut():
    assert split_message(["x" * 5000]) == ["x" * (MESSAGE_LIMIT - 1) + "…"]


def test_empty():
    assert split_message([]) == []
