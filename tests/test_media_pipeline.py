"""Сценарий команд с картинками (imaging/command.py): скачивание во временный файл, очередь и ошибки на каждом шаге.
Сеть подменена: файлы «скачиваются» из fakes.WEB (см. conftest.fake_fetch)."""

import asyncio
import io
import time

import aiohttp
import discord
import pytest
from PIL import Image

import sakuya_bot.imaging.command as command
import sakuya_bot.imaging.sources as sources
from sakuya_bot.distort.effect import EXPLODE_EFFECT

from fakes import WEB, image_attachment, member, message_with


def png_bytes(size=(120, 80)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 100, 50)).save(buf, "PNG")
    return buf.getvalue()


def apply(attachment, effect=EXPLODE_EFFECT):
    return command.apply_effect(effect, attachment, None, "explode", member(), message_with(attachment))


async def test_success_removes_downloaded_file(downloads):
    result = await apply(image_attachment(png_bytes()))
    assert result.filename == "explode.png" and downloads() == []


@pytest.mark.parametrize("error, text", [
    (aiohttp.ClientResponseError(None, (), status=404), "удалили"),
    (aiohttp.ClientResponseError(None, (), status=503), "ошибкой 503"),
    (asyncio.TimeoutError(), "скачивается слишком долго"),
    (aiohttp.ClientConnectionError("сеть"), "ошибка сети"),
])
async def test_download_errors_have_own_texts(error, text, downloads):
    a = image_attachment(b"")
    WEB[a.url] = error
    result = await apply(a)
    assert text in result and result != command.TIMEOUT_TEXT  # таймаут скачивания — не «долго обрабатывается»
    assert downloads() == [] and command._downloading == 0


async def test_unexpected_error_is_answered(monkeypatch, downloads):
    def broken(*args):
        raise RuntimeError("сломался шаблон")
    monkeypatch.setattr(command, "run_effect", broken)
    assert await apply(image_attachment(png_bytes())) == command.FAILED_TEXT
    await asyncio.sleep(0.05)  # файл удаляется, когда поток закончит
    assert downloads() == [] and command._pending == 0


async def test_memory_error_is_answered(monkeypatch):
    def heavy(*args):
        raise MemoryError
    monkeypatch.setattr(command, "run_effect", heavy)
    assert await apply(image_attachment(png_bytes())) == command.MEMORY_TEXT


async def test_file_kept_until_timed_out_thread_finishes(monkeypatch, downloads):
    seen = []

    def slow(effect, path, limit):
        time.sleep(0.3)
        seen.append(path.exists())  # поток ещё читает файл — его не должны удалить из-под него
    monkeypatch.setattr(command, "run_effect", slow)
    monkeypatch.setattr(command, "PROCESS_TIMEOUT", 0.05)
    assert await apply(image_attachment(png_bytes())) == command.TIMEOUT_TEXT
    assert len(downloads()) == 1
    await asyncio.sleep(0.4)
    assert seen == [True] and downloads() == []


async def test_downloads_take_place_in_queue(monkeypatch):
    """Скачивания занимают место в очереди и идут не больше MAX_DOWNLOADS одновременно."""
    release, active, peak = asyncio.Event(), [0], [0]

    async def slow_fetch(url, dest):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await release.wait()
        dest.write_bytes(WEB[url])
        active[0] -= 1
    monkeypatch.setattr(sources, "fetch", slow_fetch)
    tasks = [asyncio.create_task(apply(image_attachment(png_bytes()))) for _ in range(command.MAX_PENDING)]
    await asyncio.sleep(0.05)
    assert command.queue_full() and peak[0] == command.MAX_DOWNLOADS
    assert await apply(image_attachment(png_bytes())) == command.BUSY_TEXT
    release.set()
    results = await asyncio.gather(*tasks)
    assert all(r.filename == "explode.png" for r in results)
    assert command._downloading == 0 and command._pending == 0


async def test_menu_reports_failed_send(started):
    from fakes import FakeInteraction
    menu = started.get_cog("MenuCog")
    it = FakeInteraction(user=member(member_id=555_001))
    sent = it.followup.send

    async def send(content=None, **kwargs):
        if "file" in kwargs:
            raise discord.HTTPException(type("R", (), {"status": 413, "reason": "too large"})(), "too large")
        await sent(content, **kwargs)
    it.followup.send = send
    await menu.apply(it, message_with(image_attachment(png_bytes())), "explode", EXPLODE_EFFECT)
    assert "слишком большим" in it.last
